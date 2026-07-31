"""WebSocket broadcast hub — decoupled from main.py to avoid circular imports.

Dimensions push messages here. Frontend connects via /ws-fusion in main.py.
"""
from typing import Any

_fusion_clients: list = []


def add_client(ws):
    _fusion_clients.append(ws)


def remove_client(ws):
    if ws in _fusion_clients:
        _fusion_clients.remove(ws)


async def broadcast(message: dict):
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


def get_initial_payload(store) -> dict:
    """Build the initial snapshot sent on WebSocket connect."""
    return {
        "type": "INITIAL",
        "signals": store.get_all_fusion(),
        "stats": store.get_stats(),
    }
