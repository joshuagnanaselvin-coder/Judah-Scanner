"""FastAPI entry point — serves frontend, REST API, and WebSocket.

3 Dimensions:
  D1 (HTF) → backend/scanner.py — WebSocket /ws for D1 signals
  D2 (LTF) → backend/engines/ltf_engine.py — runs in background
  D3 (Fusion) → backend/engines/signal_fusion.py — WebSocket /ws-fusion for frontend
"""
import asyncio
import logging
import json
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from backend.market_data import market_data
from backend.scanner import scanner
from backend.signal_store import signal_store
from backend.performance_tracker import performance_tracker
from backend.state_store import state_store
from backend.engines.ltf_engine import ltf_engine
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

_fusion_clients: list = []

@app.websocket("/ws-fusion")
async def ws_fusion(ws: WebSocket):
    await ws.accept()
    _fusion_clients.append(ws)
    ws_hub.add_client(ws)
    logger.info(f"[ws-fusion] Client connected ({len(_fusion_clients)} total)")

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
        if ws in _fusion_clients:
            _fusion_clients.remove(ws)
        ws_hub.remove_client(ws)


async def broadcast_to_frontend(message: dict):
    """Push a message to all connected frontend clients."""
    if not _fusion_clients:
        return
    dead = []
    for ws in _fusion_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _fusion_clients.remove(ws)


# ---- D1 WEBSOCKET (existing, for backward compat) ----

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

@app.on_event("startup")
async def startup():
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

    # Hook D1 scanner to trigger D2 on tier changes
    scanner.on_tier_change = ltf_engine.on_d1_tier_change
    scanner.on_candle_close = ltf_engine.on_candle_close

    # Start D1
    try:
        await scanner.start(pairs)
    except Exception as e:
        logger.error(f"[server] D1 scanner failed to start: {e}")

    # Start D2 engine
    try:
        await ltf_engine.start()
        logger.info("[server] D2 LTF engine started")
    except Exception as e:
        logger.error(f"[server] D2 engine failed to start: {e}")

    logger.info(f" Judah Scanner running at http://{HOST}:{PORT}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
