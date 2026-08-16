"""FastAPI entry point — serves frontend, REST API, and WebSocket.

3 Dimensions:
  D1 (HTF) → backend/scanner.py — WebSocket /ws for D1 signals
  D2 (LTF) → backend/engines/ltf_engine.py — runs in background, 15M
  D3 (Decision) → backend/engines/signal_fusion.py — watches D1+D2, pushes to /ws-fusion
"""
import asyncio
import logging
import json
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from backend.market_data import market_data
from backend.scanner import scanner
from backend.signal_store import signal_store
from backend.performance_tracker import performance_tracker
from backend.state_store import state_store
from backend.engines.ltf_engine import ltf_engine
from backend.engines.signal_fusion import fusion_engine
from backend import ws_hub
from backend.config import HOST, PORT, TIMEFRAMES_HTF, BINANCE_REST_BASE

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
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
import os
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
    await ws.accept()
    ws_hub.add_client(ws)
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
        ws_hub.remove_client(ws)


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


# ---- REST API ----

@app.get("/")
async def dashboard():
    try:
        with open(os.path.join(frontend_dir, "index.html"), encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Judah Scanner</h1><p>Frontend not found.</p>")

@app.get("/api/signals")
async def get_signals():
    """D1 signals (HTF)."""
    try:
        signals = signal_store.get_all()
        return {"count": len(signals), "timestamp": _now_ms(),
                "stats": performance_tracker.get_stats(), "signals": list(signals)}
    except Exception as e:
        logger.error(f"[api/signals] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch signals", "detail": str(e)})


@app.get("/api/fusion")
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


@app.get("/api/pairs")
async def get_pairs():
    try:
        return {"pairs": scanner.symbols, "timeframes": TIMEFRAMES_HTF}
    except Exception as e:
        logger.error(f"[api/pairs] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch pairs", "detail": str(e)})


@app.get("/api/stats")
async def get_stats():
    try:
        return performance_tracker.get_stats()
    except Exception as e:
        logger.error(f"[api/stats] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch stats", "detail": str(e)})


@app.get("/api/debug-fusion")
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
            "tier_thresholds": {"SNIPER": 85, "OPPORTUNITY": 65, "WATCH": 40},
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


@app.get("/api/performance")
async def get_performance():
    try:
        return performance_tracker.get_stats()
    except Exception as e:
        logger.error(f"[api/performance] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Failed to fetch performance data", "detail": str(e)})


@app.post("/api/restart")
async def restart_scanner():
    try:
        logger.info("[restart] Initiating full scanner restart...")
        result = await scanner.restart()
        return {"ok": True, "msg": "Restart triggered", "detail": result}
    except Exception as e:
        logger.error(f"[api/restart] Error: {e}")
        return JSONResponse(status_code=500, content={"error": "Restart failed", "detail": str(e)})


@app.get("/api/health")
async def health():
    """Instant health check — always responds, even during startup."""
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
    """Runs in background — does not block the health endpoint."""
    import aiohttp
    pairs = []
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BINANCE_REST_BASE}/exchangeInfo", timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = [s["symbol"] for s in data.get("symbols", [])
                             if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"]
                else:
                    logger.error(f"[server] Binance API returned {resp.status}")
    except Exception as e:
        logger.error(f"[server] Failed to fetch pairs from Binance: {e}")

    if not pairs:
        logger.error("[server] No pairs found! Using fallback.")
        pairs = ["BTCUSDT", "ETHUSDT"]

    scanner.symbols = pairs
    logger.info(f"[server] Found {len(pairs)} USDT pairs")

    # Start D1
    try:
        await scanner.start(pairs)
    except Exception as e:
        logger.error(f"[server] D1 scanner failed to start: {e}")

    # Start D2 engine (15M — independent, same 4-layer pipeline)
    try:
        await ltf_engine.start(pairs)
        logger.info("[server] D2 LTF engine started")
    except Exception as e:
        logger.error(f"[server] D2 engine failed to start: {e}")

    # Start D3 Fusion Engine (watches D1 + D2, pushes to frontend)
    try:
        await fusion_engine.start()
        logger.info("[server] D3 Fusion Engine started")
    except Exception as e:
        logger.error(f"[server] D3 Fusion Engine failed to start: {e}")

    logger.info(f" Judah Scanner running — {len(pairs)} pairs on {BINANCE_REST_BASE}")


@app.on_event("startup")
async def startup():
    """Fire-and-forget bootstrap — health endpoint is available immediately."""
    import asyncio
    asyncio.create_task(_bootstrap())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
