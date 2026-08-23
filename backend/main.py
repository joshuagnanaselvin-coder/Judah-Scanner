"""FastAPI entry point — serves frontend, REST API, and WebSocket.

3 Dimensions (independent, no cross-communication):
  D1 (4H)    → backend/scanner.py          — 4H candle-close driven, writes to state_store
  D2 (15M)   → backend/engines/ltf_engine.py — 15M candle-close driven, writes to state_store
  D3 (Fusion)→ backend/engines/signal_fusion.py — reads from data_layer, pushes to /ws-fusion

Data flow: D1 → state_store → data_layer → D3 (read-only)
            D2 → state_store → data_layer → D3 (read-only)
"""
import asyncio
import logging
import json
import os
from typing import Optional
from datetime import datetime, timezone

# Load .env file (no external deps needed — use stdlib or python-dotenv)
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(_env_path)
except Exception:
    pass
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from backend.market_data import market_data
from backend.scanner import scanner
from backend.signal_store import signal_store
from backend.performance_tracker import performance_tracker
from backend.state_store import state_store
from backend.data_layer import data_layer
from backend.engines.ltf_engine import ltf_engine
from backend.engines.signal_fusion import fusion_engine
from backend import ws_hub
from backend import db
from backend.auth import (
    ensure_bootstrap,
    authenticate,
    create_user,
    validate_invite,
    create_invite,
    validate_token,
    revoke_token,
    list_users,
    deactivate_user,
    prune_old_sessions,
    list_invites,
    _create_session,
)
from backend.db import prune_old
from backend.config import HOST, PORT, TIMEFRAMES_HTF, BINANCE_REST_BASE, TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Rotating file handler — prevent unbounded log growth (was 105MB)
from logging.handlers import RotatingFileHandler
_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_log_dir, exist_ok=True)
_fh = RotatingFileHandler(
    os.path.join(_log_dir, "server.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,               # keep 5 rotated files
    encoding="utf-8",
)
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
logging.getLogger().addHandler(_fh)

logger = logging.getLogger("judah")


# ── Helpers ─────────────────────────────────────────────────────────

def _safe(obj):
    """Make objects JSON-serializable for WebSocket sends."""
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "timestamp"):
        return obj.timestamp()
    return obj


def _now_ms() -> int:
    """Current epoch timestamp in milliseconds."""
    import time
    return int(time.time() * 1000)


app = FastAPI(title="Judah Scanner")

# Serve frontend static files with NO caching
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
if os.path.exists(frontend_dir):
    from fastapi.responses import FileResponse as _FR
    class _NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            if isinstance(response, _FR):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
    app.mount("/static", _NoCacheStaticFiles(directory=frontend_dir), name="static")

# ---- D3 FUSION WEBSOCKET (Frontend) ----

@app.websocket("/ws-fusion")
async def ws_fusion(ws: WebSocket):
    token = ws.query_params.get("token", "")
    if token:
        user = await validate_token(token)
        if not user:
            await ws.close(code=4001, reason="Unauthorized")
            return

    await ws.accept()
    await ws_hub.add_client(ws)
    logger.info(f"[ws-fusion] Client connected ({len(ws_hub._clients)} total)")

    # Send current state immediately
    try:
        await ws.send_json(ws_hub.get_initial_payload(state_store))
    except Exception:
        pass

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        logger.info("[ws-fusion] Client disconnected")
    finally:
        await ws_hub.remove_client(ws)


# ---- D1 WEBSOCKET (for D1 signal stream) ----

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    async def on_new(new_signals, all_signals, refreshed, revalidated=None):
        try:
            if revalidated:
                await ws.send_json({
                    "type": "REVALIDATED", "signals": _safe(revalidated),
                    "timestamp": _now_ms(),
                })
            if new_signals:
                await ws.send_json({
                    "type": "NEW_SIGNALS", "signals": _safe(new_signals),
                    "timestamp": _now_ms(),
                })
            if all_signals:
                await ws.send_json({"type": "REFRESH", "signals": _safe(all_signals)})
        except Exception as e:
            logger.warning(f"[ws] Send error: {e}")

    scanner.on_new_signals(on_new)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        logger.info("[ws] Client disconnected")


# ---- AUTH ----

async def _get_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))) -> Optional[str]:
    """Extract token from Authorization header or cookie."""
    if credentials and credentials.credentials:
        return credentials.credentials
    token = request.cookies.get("session_token", "")
    return token or None


async def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))) -> dict:
    """FastAPI dependency: require authenticated user."""
    token = await _get_token(request, credentials)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = await validate_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


async def get_current_admin(request: Request, credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False))) -> dict:
    """FastAPI dependency: require admin user."""
    user = await get_current_user(request, credentials)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@app.post("/auth/signup")
async def auth_signup(request: Request):
    """Sign up with invite code. Returns session token on success."""
    try:
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password", "")
        code = (body.get("invite_code") or "").strip()

        if not email or "@" not in email:
            return JSONResponse(status_code=400, content={"error": "Valid email required"})
        if not password or len(password) < 6:
            return JSONResponse(status_code=400, content={"error": "Password must be at least 6 characters"})
        if not code:
            return JSONResponse(status_code=400, content={"error": "Invite code required"})

        # Validate invite
        valid = await validate_invite(code, email)
        if not valid:
            return JSONResponse(status_code=400, content={"error": "Invalid or expired invite code"})

        # Create user
        user = await create_user(email, password, role="user")
        token = await _create_session(user["user_id"], user["email"], user["role"])
        logger.info("[auth] Signup: %s", email)
        return {"token": token, "user": {"email": user["email"], "role": user["role"]}}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error(f"[auth/signup] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Signup failed"})


@app.post("/auth/login")
async def auth_login(request: Request):
    """Login with email + password. Returns session token."""
    try:
        body = await request.json()
        email = (body.get("email") or "").strip().lower()
        password = body.get("password", "")

        if not email or not password:
            return JSONResponse(status_code=400, content={"error": "Email and password required"})

        user = await authenticate(email, password)
        if not user:
            logger.warning(f"[auth] Failed login: {email}")
            return JSONResponse(status_code=401, content={"error": "Invalid credentials"})

        token = await _create_session(user["user_id"], user["email"], user["role"])
        logger.info("[auth] Login: %s", email)
        return {"token": token, "user": {"email": user["email"], "role": user["role"]}}
    except Exception as e:
        logger.error(f"[auth/login] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Login failed"})


@app.post("/auth/logout")
async def auth_logout(request: Request):
    """Logout — revoke session token."""
    try:
        token = await _get_token(request)
        if token:
            await revoke_token(token)
        return {"ok": True}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    """Return current user info."""
    return {"email": user["email"], "role": user["role"], "user_id": user["user_id"]}


@app.post("/auth/invite")
async def auth_invite(request: Request, admin: dict = Depends(get_current_admin)):
    """Admin: generate invite code."""
    try:
        body = await request.json()
        bound_email = body.get("email")
        invite = await create_invite(admin["email"], bound_email=bound_email)
        return invite
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/auth/invites")
async def auth_list_invites(admin: dict = Depends(get_current_admin)):
    """Admin: list invite codes."""
    invites = await list_invites()
    return {"invites": invites}


@app.get("/auth/users")
async def auth_list_users(admin: dict = Depends(get_current_admin)):
    """Admin: list all users."""
    users = await list_users()
    return {"users": users}


@app.delete("/auth/users/{user_id}")
async def auth_deactivate_user(user_id: int, admin: dict = Depends(get_current_admin)):
    """Admin: deactivate a user."""
    ok = await deactivate_user(user_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    return {"ok": True}


# ---- AUTH-PROTECTED REST API ----

@app.get("/login")
async def login_page():
    """Serve login page."""
    try:
        with open(os.path.join(frontend_dir, "login.html"), encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Login</h1><p>Login page not found.</p>")


@app.get("/admin")
async def admin_panel(request: Request):
    """Serve admin panel if authenticated as admin, otherwise redirect to login."""
    try:
        token = request.cookies.get("session_token", "")
        if not token:
            raise HTTPException(status_code=401)
        user = await validate_token(token)
        if not user or user.get("role") != "admin":
            raise HTTPException(status_code=403)
        with open(os.path.join(frontend_dir, "admin.html"), encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except HTTPException:
        return HTMLResponse(content='<script>window.location.href="/login"</script>', status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Admin Panel</h1><p>admin.html not found.</p>")

@app.get("/")
async def dashboard(request: Request):
    """Serve dashboard if authenticated, otherwise redirect to login."""
    try:
        token = request.cookies.get("session_token", "")
        if not token:
            raise HTTPException(status_code=401)
        user = await validate_token(token)
        if not user:
            raise HTTPException(status_code=401)
        with open(os.path.join(frontend_dir, "index.html"), encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except HTTPException:
        return HTMLResponse(content='<script>window.location.href="/login"</script>', status_code=200)
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Judah Scanner</h1><p>Frontend not found.</p>")

@app.get("/api/signals", dependencies=[Depends(get_current_user)])
async def get_signals():
    """D1 signals (HTF)."""
    try:
        signals = signal_store.get_all()
        return {"count": len(signals), "timestamp": _now_ms(),
                "stats": performance_tracker.get_stats(), "signals": list(signals)}
    except Exception as e:
        logger.error(f"[api/signals] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch signals", "detail": str(e)})


@app.get("/api/fusion", dependencies=[Depends(get_current_user)])
async def get_fusion():
    """D3 decision signals (frontend display)."""
    try:
        return {"count": len(state_store.d3_decisions),
                "timestamp": _now_ms(),
                "stats": state_store.get_stats(),
                "signals": list(state_store.get_all_decisions().values())}
    except Exception as e:
        logger.error(f"[api/fusion] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch fusion data", "detail": str(e)})


@app.get("/api/pairs", dependencies=[Depends(get_current_user)])
async def get_pairs():
    try:
        return {"pairs": scanner.symbols, "timeframes": TIMEFRAMES_HTF}
    except Exception as e:
        logger.error(f"[api/pairs] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch pairs", "detail": str(e)})


@app.get("/api/stats", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        return performance_tracker.get_stats()
    except Exception as e:
        logger.error(f"[api/stats] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch stats", "detail": str(e)})


@app.get("/api/debug-fusion", dependencies=[Depends(get_current_user)])
async def debug_fusion():
    """Diagnostic: show D1/D2 overlap and decision layer output."""
    try:
        d1 = dict(state_store.d1_tiers)
        d2 = state_store.get_all_d2_signals()
        decisions = state_store.get_all_decisions()

        d2_coins = set(d2.keys())
        decision_coins = set(decisions.keys())
        overlap = d2_coins & decision_coins

        # Sample D1 tiers
        d1_sample = {}
        for coin in list(d1.keys())[:10]:
            entry = d1[coin]
            d1_sample[coin] = {"tier": entry.get("tier"), "score": entry.get("score"), "age_sec": round(__import__('time').time() - entry.get("updated_at", 0), 1)}

        # Sample D2 signals
        d2_sample = {}
        for coin, sig in list(d2.items())[:10]:
            d2_sample[coin] = {"score": getattr(sig, 'score', 0), "tier": getattr(sig, 'tier', '?'), "dir": getattr(sig, 'direction', '?')}

        # Sample decisions
        decision_sample = {}
        for coin in list(decisions.keys())[:10]:
            d = decisions[coin]
            decision_sample[coin] = {
                "type": d.get("signal_type"),
                "d1_tier": d.get("d1_tier"),
                "d1_score": d.get("d1_score"),
                "d2_score": d.get("d2_score"),
                "action": d.get("action"),
                "ev": d.get("expected_value"),
            }

        return {
            "tier_thresholds": {"SNIPER": TIER_SNIPER_SCORE, "OPPORTUNITY": TIER_OPPORTUNITY_SCORE, "WATCH": TIER_WATCH_SCORE},
            "d1_count": len(d1),
            "d2_count": len(d2),
            "decision_count": len(decisions),
            "overlap_count": len(overlap),
            "d1_sample": d1_sample,
            "d2_sample": d2_sample,
            "decision_sample": decision_sample,
        }
    except Exception as e:
        logger.error(f"[api/debug-fusion] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch debug data", "detail": str(e)})


@app.get("/api/performance", dependencies=[Depends(get_current_user)])
async def get_performance():
    try:
        return performance_tracker.get_stats()
    except Exception as e:
        logger.error(f"[api/performance] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch performance data", "detail": str(e)})


# ── Analytics REST API (Phase 6) ──────────────────────────────────────────

@app.get("/api/analytics/outcomes", dependencies=[Depends(get_current_user)])
async def analytics_outcomes():
    """Signal outcome stats from SQLite — aggregate win rates by type/tier/session."""
    try:
        stats = await db.get_outcome_stats()
        return stats if stats else {"error": "No data yet"}
    except Exception as e:
        logger.error(f"[api/analytics/outcomes] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/analytics/evolution/{coin}", dependencies=[Depends(get_current_user)])
async def analytics_evolution(coin: str):
    """State transition history for a specific coin."""
    try:
        rows = await db.get_evolution_history(coin.upper(), limit=50)
        return {"coin": coin.upper(), "history": rows}
    except Exception as e:
        logger.error(f"[api/analytics/evolution] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/analytics/bayes", dependencies=[Depends(get_current_user)])
async def analytics_bayes():
    """Bayesian calibration table — win rates per state+signal type."""
    try:
        table = await db.get_bayes_table()
        return {"entries": len(table), "calibration": table}
    except Exception as e:
        logger.error(f"[api/analytics/bayes] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/analytics/decisions", dependencies=[Depends(get_current_user)])
async def analytics_decisions():
    """Recent D3 fusion decisions from SQLite."""
    try:
        rows = await db.get_recent_decisions(limit=100)
        return {"count": len(rows), "decisions": rows}
    except Exception as e:
        logger.error(f"[api/analytics/decisions] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/analytics/db", dependencies=[Depends(get_current_user)])
async def analytics_db():
    """DB file size + row counts for all tables."""
    try:
        stats = await db.get_db_stats()
        return stats
    except Exception as e:
        logger.error(f"[api/analytics/db] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/logs", dependencies=[Depends(get_current_user)])
async def api_logs(lines: int = 200, source: str = "all"):
    """Tail the server log file.

    source: all (default), d1, d2, d3, errors
    """
    try:
        log_path = os.path.join(_log_dir, "server.log")
        if not os.path.exists(log_path):
            return JSONResponse(status_code=404, content={"error": "No log file yet"})
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        raw = all_lines[-lines * 2:]  # read extra for filtering
        filtered = []
        source_map = {
            "d1": ["judah.scanner", "judah.confluence"],
            "d2": ["judah.ltf_engine", "judah.ltf", "judah.ltf_pipeline", "judah.crt",
                   "judah.smc", "judah.builder", "judah.correlation", "judah.flow",
                   "judah.fast_mover", "judah.engine"],
            "d3": ["judah.fusion"],
            "errors": ["ERROR", "CRITICAL", "Task exception"],
        }
        src = source_map.get(source, [])

        for line in raw:
            if source == "all":
                filtered.append(line)
            elif source == "errors":
                up = line.upper()
                if any(k in up for k in src):
                    filtered.append(line)
            else:
                if any(lg in line for lg in src):
                    filtered.append(line)
            if len(filtered) >= lines:
                break

        tail = "".join(filtered[-lines:])
        return JSONResponse(content={
            "lines": tail.splitlines(),
            "total": len(all_lines),
            "filtered": len(filtered),
            "source": source,
        })
    except Exception as e:
        logger.error(f"[api/logs] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/health/detail", dependencies=[Depends(get_current_user)])
async def health_detail():
    """Richer health: scan cycle ages, error counts from logs, uptime."""
    try:
        import time
        now = time.time()
        h = {"status": "ok", "uptime_s": 0, "d1": {}, "d2": {}, "d3": {},
             "ws": {}, "errors_1h": 0}

        # Basic state
        h["status"] = "ok" if state_store.last_d1_scan > 0 else "initializing"
        h["ws"]["connected"] = market_data.ws_connected if hasattr(market_data, 'ws_connected') else False
        h["ws"]["clients"] = len(ws_hub._clients) if hasattr(ws_hub, '_clients') else 0
        h["signals"] = len(signal_store.get_all())
        h["decisions"] = len(state_store.d3_decisions)

        # D1/D2/D3 timing
        d1_ts = state_store.last_d1_scan
        d2_ts = state_store.last_d2_scan
        d3_ts = state_store.last_d3_fusion
        h["d1"] = {
            "last_scan_ts": d1_ts,
            "age_s": round(now - d1_ts, 1) if d1_ts > 0 else None,
            "status": "live" if d1_ts > 0 and (now - d1_ts) < 30 else "stale" if d1_ts > 0 else "never",
            "coins": len(state_store.d1_tiers),
        }
        h["d2"] = {
            "last_scan_ts": d2_ts,
            "age_s": round(now - d2_ts, 1) if d2_ts > 0 else None,
            "status": "live" if d2_ts > 0 and (now - d2_ts) < 30 else "stale" if d2_ts > 0 else "never",
            "signals": len(state_store.d2_signals),
        }
        h["d3"] = {
            "last_scan_ts": d3_ts,
            "age_s": round(now - d3_ts, 1) if d3_ts > 0 else None,
            "status": "live" if d3_ts > 0 and (now - d3_ts) < 30 else "stale" if d3_ts > 0 else "never",
            "decisions": len(state_store.d3_decisions),
        }

        # Data layer quality stats + stale counts
        try:
            payload = await data_layer.get_fusion_payload()
            h["d1_valid"] = payload["d1_coin_count"]
            h["d2_valid"] = payload["d2_coin_count"]
            h["d3_total"] = len(state_store.d3_decisions)
            now_ts = time.time()
            d1_stale = 0
            d1_freshness = {}
            from backend.config import SIGNAL_TTL_MINUTES, D2_SIGNAL_TTL_MINUTES
            cutoff_d1 = now_ts - (SIGNAL_TTL_MINUTES * 60)
            for coin, entry in state_store.d1_tiers.items():
                updated_at = entry.get("updated_at", 0)
                if updated_at < cutoff_d1:
                    d1_stale += 1
                else:
                    age_min = (now_ts - updated_at) / 60
                    label = "HOT" if age_min < 3 else "WARM" if age_min < 8 else "COOL" if age_min < 15 else "STALE"
                    d1_freshness[label] = d1_freshness.get(label, 0) + 1
            h["d1_stale"] = d1_stale
            h["d1_freshness"] = d1_freshness
            d2_stale = 0
            cutoff_d2 = now_ts - (D2_SIGNAL_TTL_MINUTES * 60)
            for coin, sig in state_store.d2_signals.items():
                born_at = getattr(sig, 'born_at', None)
                if born_at:
                    born_ts = born_at.timestamp() if hasattr(born_at, 'timestamp') else float(born_at)
                    if born_ts < cutoff_d2:
                        d2_stale += 1
            h["d2_stale"] = d2_stale
        except Exception:
            h.setdefault("d1_valid", 0)
            h.setdefault("d1_stale", 0)
            h.setdefault("d2_valid", 0)
            h.setdefault("d2_stale", 0)
            h.setdefault("d3_total", 0)
            h.setdefault("d1_freshness", {})

        # Count errors in last ~200 lines of log
        try:
            log_path = os.path.join(_log_dir, "server.log")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    recent = f.readlines()[-200:]
                h["errors_1h"] = sum(1 for l in recent if "ERROR" in l)
                h["warnings_1h"] = sum(1 for l in recent if "WARNING" in l)
        except Exception:
            pass

        return h
    except Exception as e:
        logger.error(f"[api/health/detail] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/restart", dependencies=[Depends(get_current_admin)])
async def restart_scanner():
    try:
        logger.info("[restart] Soft restart: restarting scan loops only...")

        # Restart D1 scan loop (keeps symbols, WS, candles intact)
        d1_result = await scanner.soft_restart()

        # Restart D2 scan loop
        d2_result = await ltf_engine.soft_restart()

        return {"ok": True, "msg": "Scanner restarted", "detail": {**d1_result, **d2_result}}
    except Exception as e:
        logger.error(f"[api\\restart] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Restart failed", "detail": str(e)})


@app.get("/api/health")
async def health():
    """Instant health check — public (no auth required) for monitoring."""
    try:
        import time
        ready = state_store.last_d1_scan > 0
        return {
            "status": "ok" if ready else "initializing",
            "ready": ready,
            "ws_connected": market_data.ws_connected if hasattr(market_data, 'ws_connected') else False,
            "signal_count": len(signal_store.get_all()),
            "decision_count": len(state_store.d3_decisions),
            "stats": state_store.get_stats(),
        }
    except Exception as e:
        logger.error(f"[api/health] Error: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


async def _bootstrap():
    """Runs in background — does not block the health endpoint.

    Fetches USDT-M futures from Binance Futures API and applies a strict
    filter to exclude stock tokens, leveraged tokens, and BUSD pairs.
    Only clean crypto USDT-M futures enter the scanner.
    """
    import aiohttp
    from backend.symbol_filter import filter_usdt_futures
    from backend.config import BINANCE_FUTURES_BASE

    pairs = []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{BINANCE_FUTURES_BASE}/exchangeInfo",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    raw = [
                        sym["symbol"]
                        for sym in data.get("symbols", [])
                        if sym.get("status") == "TRADING"
                        and sym.get("quoteAsset") == "USDT"
                        and sym.get("contractType") == "PERPETUAL"
                    ]
                    pairs = filter_usdt_futures(raw)
                else:
                    logger.error(f"[server] Binance Futures API returned {resp.status}")
    except Exception as e:
        logger.error(f"[server] Failed to fetch pairs from Binance Futures: {e}")

    if not pairs:
        logger.error("[server] No USDT-M futures pairs found! Using fallback.")
        pairs = ["BTCUSDT", "ETHUSDT"]

    scanner.symbols = pairs
    logger.info(f"[server] Found {len(pairs)} USDT pairs")

    # === STEP 1: Bootstrap candle data FIRST (before any engine starts scanning) ===
    # Without this, all scanners run with 0 candles and produce 0 results.
    try:
        logger.info(f"[server] Bootstrapping candles for {len(pairs)} pairs "
                    f"(4H + 15M = {len(pairs)*2} requests)...")
        count = await market_data.bootstrap(pairs)
        logger.info(f"[server] Bootstrap complete: {count}/{len(pairs)*2} candle sets downloaded")
    except Exception as e:
        logger.error(f"[server] Bootstrap failed: {e}")

    # === STEP 2: Start D1 (4H scanner) — candles are ready, no race ===
    try:
        await scanner.start(pairs)
        logger.info("[server] D1 scanner started")
    except Exception as e:
        logger.error(f"[server] D1 scanner failed to start: {e}")

    # === STEP 3: Start D2 engine (15M — independent of D1) ===
    try:
        await ltf_engine.start(pairs)
        logger.info("[server] D2 LTF engine started")
    except Exception as e:
        logger.error(f"[server] D2 engine failed to start: {e}")

    # === STEP 4: Start D3 Fusion Engine (reads from state_store via data_layer) ===
    try:
        await fusion_engine.start()
        logger.info("[server] D3 Fusion Engine started")

        # Wire D2 → D3 event: D2 calls this after each scan cycle
        ltf_engine._d3_notify = fusion_engine.notify
        # Wire D1 → D3 event: D1 calls this after each scan cycle
        scanner._d3_notify = fusion_engine.notify
        logger.info("[server] D1→D3 and D2→D3 event channels wired")
    except Exception as e:
        logger.error(f"[server] D3 Fusion Engine failed to start: {e}")

    logger.info(f" Judah Scanner running — {len(pairs)} pairs on {BINANCE_REST_BASE}")


@app.on_event("startup")
async def startup():
    """Fire-and-forget bootstrap — health endpoint is available immediately."""
    import asyncio

    # Phase 22: Initialize SQLite schema (idempotent — safe to call on every restart)
    try:
        await db.init_schema()
        logger.info("[startup] SQLite schema initialized")
    except Exception:
        logger.exception("[startup] DB schema init failed")

    # Create admin user if not exists (idempotent)
    try:
        await ensure_bootstrap()
        logger.info("[startup] Auth bootstrap complete")
    except Exception:
        logger.exception("[startup] Auth bootstrap failed")

    asyncio.create_task(_bootstrap())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
