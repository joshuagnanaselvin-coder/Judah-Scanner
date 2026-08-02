"""FastAPI entry point — serves frontend, REST API, and WebSocket.

3 Dimensions:
  D1 (HTF) → backend/scanner.py — WebSocket /ws for D1 signals
  D2 (LTF) → backend/engines/ltf_engine.py — runs in background, 15M
  D3 (Fusion) → backend/engines/signal_fusion.py — watches D1+D2, pushes to /ws-fusion
"""
import asyncio
import logging
import json
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
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
        with open(os.path.join(frontend_dir, "index.html")) as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Judah Scanner</h1><p>Frontend not found.</p>")

@app.get("/api/signals")
async def get_signals():
    """D1 signals (HTF)."""
    signals = signal_store.get_all()
    return {"count": len(signals), "timestamp": _now_ms(),
            "stats": performance_tracker.get_stats(), "signals": signals}

@app.get("/api/fusion")
async def get_fusion():
    """D3 fusion signals (frontend display)."""
    return {"count": len(state_store.d3_fusion),
            "timestamp": _now_ms(),
            "stats": state_store.get_stats(),
            "signals": state_store.get_all_fusion()}

@app.get("/api/health")
async def health():
    return {"status": "ok", "ws_connected": market_data.ws_connected,
            "signal_count": len(signal_store.signals),
            "fusion_count": len(state_store.d3_fusion),
            "stats": state_store.get_stats()}

@app.get("/api/pairs")
async def get_pairs():
    return {"pairs": scanner.symbols, "timeframes": TIMEFRAMES_HTF}

@app.get("/api/stats")
async def get_stats():
    return performance_tracker.get_stats()

@app.get("/api/logs")
async def get_logs(limit: int = 100):
    from backend.signal_logger import get_recent_logs
    return get_recent_logs(limit)

@app.get("/api/debug-fusion")
async def debug_fusion():
    """Diagnostic: show D1/D2 overlap and why fusion produces few results."""
    d1 = dict(state_store.d1_tiers)
    d2 = state_store.get_all_d2_signals()
    active = state_store.get_active_coins()

    d2_coins = set(d2.keys())
    active_set = set(active)
    overlap = d2_coins & active_set
    only_d2 = d2_coins - active_set

    # Sample D1 tiers
    d1_sample = {}
    for coin in list(d1.keys())[:10]:
        entry = d1[coin]
        d1_sample[coin] = {"tier": entry.get("tier"), "score": entry.get("score"), "age_sec": round(__import__('time').time() - entry.get("updated_at", 0), 1)}

    # Sample D2 signals
    d2_sample = {}
    for coin, sig in list(d2.items())[:10]:
        d2_sample[coin] = {"score": getattr(sig, 'score', 0), "tier": getattr(sig, 'tier', '?'), "dir": getattr(sig, 'direction', '?')}

    # D1 tiers for overlapped coins
    overlap_detail = {}
    for coin in list(overlap)[:10]:
        d1_entry = d1.get(coin, {})
        overlap_detail[coin] = {"d1_tier": d1_entry.get("tier"), "d1_score": d1_entry.get("score"), "d2_score": getattr(d2.get(coin), 'score', 0)}

    # Why no overlap?
    no_overlap_reason = {}
    for coin in list(only_d2)[:5]:
        d1_entry = d1.get(coin)
        if not d1_entry:
            no_overlap_reason[coin] = "no D1 data"
        else:
            no_overlap_reason[coin] = f"D1={d1_entry.get('tier')} score={d1_entry.get('score')}"

    return {
        "tier_thresholds": {"SNIPER": 70, "OPPORTUNITY": 55, "WATCH": 40},
        "d1_count": len(d1),
        "d2_count": len(d2),
        "active_count": len(active),
        "d3_fusion_count": len(state_store.d3_fusion),
        "overlap_count": len(overlap),
        "only_d2_count": len(only_d2),
        "d1_sample": d1_sample,
        "d2_sample": d2_sample,
        "overlap_detail": overlap_detail,
        "no_overlap_reason": no_overlap_reason,
    }

@app.get("/api/performance")
async def get_performance():
    from backend.performance_tracker import PerformanceTrackerCSV
    tracker = PerformanceTrackerCSV()
    tracker.load_from_csv()
    return {
        "summary": tracker.get_summary(),
        "by_scenario": tracker.get_scenario_report(),
    }


@app.post("/api/restart")
async def restart_scanner():
    logger.info("[restart] Initiating full scanner restart...")
    result = await scanner.restart()
    return {"ok": True, "msg": "Restart triggered", "detail": result}


# ---- HELPERS ----

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)

def _safe(obj):
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(x) for x in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


# ---- STARTUP ----

@app.get("/api/health")
async def health():
    """Instant health check — always responds, even during startup."""
    import time
    ready = state_store.last_d1_scan > 0
    return {
        "status": "ok" if ready else "initializing",
        "ready": ready,
        "ws_connected": market_data.ws_connected if hasattr(market_data, 'ws_connected') else False,
        "signal_count": len(signal_store.get_all()),
        "fusion_count": len(state_store.d3_fusion),
        "stats": {
            "d1_coins": len(state_store.d1_tiers),
            "d2_signals": len(state_store.d2_signals),
            "d3_fusion": len(state_store.d3_fusion),
            "last_d1_scan": state_store.last_d1_scan,
            "last_d2_scan": state_store.last_d2_scan,
            "last_d3_fusion": state_store.last_d3_fusion,
        },
    }


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
