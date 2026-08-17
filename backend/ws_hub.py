"""WebSocket broadcast hub — thread-safe, decoupled from main.py.

Dimensions push messages here. Frontend connects via /ws-fusion in main.py.
"""
import asyncio
import logging
from typing import Any

logger = logging.getLogger("judah.ws_hub")

_clients: list = []
_clients_lock = asyncio.Lock()


async def add_client(ws):
    """Add a WebSocket client (thread-safe)."""
    async with _clients_lock:
        _clients.append(ws)
    logger.debug(f"[ws_hub] Client added ({len(_clients)} total)")


async def remove_client(ws):
    """Remove a WebSocket client (thread-safe)."""
    async with _clients_lock:
        if ws in _clients:
            _clients.remove(ws)
    logger.debug(f"[ws_hub] Client removed ({len(_clients)} total)")


async def broadcast(message: dict):
    """Push a message to all connected frontend clients.

    Dead clients are silently removed. Failed sends don't crash the loop.
    """
    if not _clients:
        return
    dead = []
    async with _clients_lock:
        for ws in list(_clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in _clients:
                _clients.remove(ws)
    if dead:
        logger.debug(f"[ws_hub] Removed {len(dead)} dead clients ({len(_clients)} remaining)")


def get_initial_payload(store) -> dict:
    """Build the initial snapshot sent on WebSocket connect."""
    try:
        decisions = store.get_all_decisions().values()
    except Exception:
        decisions = []
    return {
        "type": "INITIAL",
        "signals": list(decisions),
        "stats": store.get_stats(),
    }