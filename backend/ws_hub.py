"""WebSocket broadcast hub — decoupled from main.py.

Dimensions push messages here. Frontend connects via /ws-fusion in main.py.
"""
from typing import Any

_clients: list = []


def add_client(ws):
    _clients.append(ws)


def remove_client(ws):
    if ws in _clients:
        _clients.remove(ws)


async def broadcast(message: dict):
    """Push a message to all connected frontend clients."""
    if not _clients:
        return
    dead = []
    for ws in _clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.remove(ws)


def get_initial_payload(store) -> dict:
    """Build the initial snapshot sent on WebSocket connect."""
    return {
        "type": "INITIAL",
        "signals": list(store.get_all_decisions().values()),
        "stats": store.get_stats(),
    }
