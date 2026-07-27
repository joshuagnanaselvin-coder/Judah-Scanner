"""FastAPI entry point — serves frontend, REST API, and WebSocket."""
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from backend.market_data import market_data
from backend.scanner import scanner
from backend.signal_store import signal_store
from backend.performance_tracker import performance_tracker
from backend.config import HOST, PORT, TIMEFRAMES, BINANCE_REST_BASE

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("judah")

app = FastAPI(title="Judah Scanner")

# Serve frontend static files with NO caching (edits always show immediately)
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

# ---- REST API ----

@app.get("/")
async def dashboard():
    try:
        with open(os.path.join(frontend_dir, "index.html")) as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Judah Scanner</h1><p>Frontend not found. Check frontend/index.html</p>")

@app.get("/api/signals")
async def get_signals():
    signals = signal_store.get_all()
    return {"count": len(signals), "timestamp": _now_ms(),
            "stats": performance_tracker.get_stats(), "signals": signals}

@app.get("/api/health")
async def health():
    return {"status": "ok", "ws_connected": market_data.ws_connected,
            "signal_count": len(signal_store.signals),
            "stats": performance_tracker.get_stats()}

@app.get("/api/pairs")
async def get_pairs():
    return {"pairs": scanner.symbols, "timeframes": TIMEFRAMES}

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
    """Wipe signals + FVG ledger, re-bootstrap candles, reconnect WS.
    Returns when restart begins (async — watch status bar for completion)."""
    logger.info("[restart] Initiating full scanner restart...")
    result = await scanner.restart()
    return {"ok": True, "msg": "Restart triggered", "detail": result}

# ---- HELPERS ----

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)

def _safe(obj):
    """Make object JSON-serializable — convert datetime, bytes, and unknown types."""
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
    # Fallback: convert to string
    return str(obj)

# ---- WEBSOCKET ----

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    # Send initial snapshot
    await ws.send_json({
        "type": "INITIAL",
        "signals": signal_store.get_all(),
        "stats": performance_tracker.get_stats(),
    })

    # Register callback for new signals
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

def _now_ms():
    return int(datetime.now(timezone.utc).timestamp() * 1000)

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
        logger.error("[server] No pairs found! Server starting but scanner will be empty.")
        pairs = ["BTCUSDT", "ETHUSDT"]  # fallback minimal set

    scanner.symbols = pairs
    logger.info(f"[server] Found {len(pairs)} USDT pairs")

    try:
        await scanner.start(pairs)
    except Exception as e:
        logger.error(f"[server] Scanner failed to start: {e}")

    logger.info(f" Judah Scanner running at http://{HOST}:{PORT}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
