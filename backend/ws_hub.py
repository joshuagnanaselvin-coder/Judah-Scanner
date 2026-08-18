"""WebSocket broadcast hub — thread-safe, decoupled from main.py.

Phase 17: Bounded delivery.
  - Per-client asyncio.Queue(maxsize=8) prevents slow clients from
    buffering unbounded messages.
  - broadcast() is non-blocking — it just enqueues and returns.
  - A background drain task per client serialises sends.
  - Dead clients are detected when send_json raises and cleaned up.
"""
import asyncio
import logging
import weakref
from typing import Any

logger = logging.getLogger("judah.ws_hub")

# Phase 17: bounded delivery constants
_WS_QUEUE_MAXSIZE = 8           # Max pending messages per client
_WS_SEND_TIMEOUT = 5.0          # Seconds before a send is considered stuck


class ClientConnection:
    """Wraps a WebSocket with a bounded send queue + drain task."""

    def __init__(self, ws):
        self._ws_ref = weakref.ref(ws)
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_WS_QUEUE_MAXSIZE)
        self._drain_task: asyncio.Task | None = None
        self._dead = False

    @property
    def ws(self):
        return self._ws_ref()

    def start_drain(self):
        """Launch the background drain loop for this client."""
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def _drain_loop(self):
        """Continuously drain messages from the queue and send them."""
        ws = self._ws_ref()
        if ws is None:
            return
        while not self._dead:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                ws_ref = self._ws_ref()
                if ws_ref is None:
                    self._dead = True
                    return
                continue
            try:
                await asyncio.wait_for(ws.send_json(msg), timeout=_WS_SEND_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"[ws_hub] Client send timeout — marking dead")
                self._dead = True
            except Exception:
                self._dead = True
            finally:
                self._queue.task_done()

    async def enqueue(self, message: dict) -> bool:
        """Phase 17: Try to enqueue a message. Drops if queue is full (bounded).

        Returns True if queued, False if dropped.
        """
        if self._dead:
            return False
        try:
            self._queue.put_nowait(message)
            return True
        except asyncio.QueueFull:
            logger.warning(f"[ws_hub] Queue full — dropping message for client "
                           f"(queue size={self._queue.qsize()}, maxsize={_WS_QUEUE_MAXSIZE})")
            return False

    def is_dead(self) -> bool:
        return self._dead or self._ws_ref() is None

    async def close(self):
        """Cancel drain task and close the underlying websocket."""
        self._dead = True
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        ws = self._ws_ref()
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


# Module-level client registry (replaces plain _clients list)
_clients: list[ClientConnection] = []
_clients_lock = asyncio.Lock()


async def add_client(ws):
    """Add a WebSocket client (thread-safe).

    Phase 17: Wraps ws in a ClientConnection with bounded queue.
    """
    conn = ClientConnection(ws)
    async with _clients_lock:
        _clients.append(conn)
    conn.start_drain()
    logger.debug(f"[ws_hub] Client added ({_count_alive()} alive)")


async def remove_client(ws):
    """Remove a WebSocket client by websocket reference (thread-safe)."""
    async with _clients_lock:
        to_remove = [c for c in _clients if c.ws is ws]
        for c in to_remove:
            await c.close()
            if c in _clients:
                _clients.remove(c)
    logger.debug(f"[ws_hub] Client removed ({_count_alive()} alive)")


async def broadcast(message: dict):
    """Phase 17: Push a message to all connected frontend clients.

    Non-blocking — enqueues into per-client bounded queues.
    Slow/full clients have messages dropped rather than blocking delivery.
    """
    if not _clients:
        return
    dead = []
    async with _clients_lock:
        for conn in list(_clients):
            if conn.is_dead():
                dead.append(conn)
                continue
            queued = await conn.enqueue(message)
            if not queued:
                # Queue full — mark dead so it gets cleaned up
                dead.append(conn)
        for conn in dead:
            await conn.close()
            if conn in _clients:
                _clients.remove(conn)
    if dead:
        logger.debug(f"[ws_hub] Removed {len(dead)} dead/slow clients "
                     f"({_count_alive()} remaining)")


def _count_alive() -> int:
    return sum(1 for c in _clients if not c.is_dead())


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
