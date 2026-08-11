"""Signal History — archives expired D3 decisions for 2-hour lookback.

When a D2 signal expires (15-min TTL) and D3 removes its decision,
the decision is moved here instead of being deleted. The frontend
renders these in a "Recent History" section below active signals.

History auto-prunes items older than 2 hours.
"""
import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger("judah.history")

# How long to keep expired signals in history (minutes)
HISTORY_TTL_MINUTES = 120


class SignalHistory:
    """Thread-safe circular buffer for expired signal decisions."""

    def __init__(self, maxlen: int = 500):
        self._items: deque = deque(maxlen=maxlen)

    def add(self, decision: dict, expiry_reason: str = "ttl_expired"):
        """Archive an expired D3 decision.

        Args:
            decision: The D3 decision dict (from state_store.d3_decisions).
            expiry_reason: Why it expired — 'ttl_expired', 'setup_broken', 'd1_dropped'.
        """
        entry = {
            **decision,
            "expired_at": datetime.now(timezone.utc).timestamp(),
            "expiry_reason": expiry_reason,
        }
        self._items.append(entry)
        logger.debug(f"[history] Archived {decision.get('coin')}: {expiry_reason} "
                     f"(total history: {len(self._items)})")

    def get_recent(self, max_age_min: int = HISTORY_TTL_MINUTES) -> list:
        """Return items from the last max_age_min minutes, newest first."""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_min * 60)
        recent = [item for item in self._items
                  if item.get("expired_at", 0) > cutoff]
        recent.sort(key=lambda x: x.get("expired_at", 0), reverse=True)
        return recent

    def get_all(self) -> list:
        """Return all items, newest first."""
        return sorted(self._items, key=lambda x: x.get("expired_at", 0), reverse=True)

    def prune(self, max_age_min: int = HISTORY_TTL_MINUTES):
        """Remove items older than max_age_min."""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_min * 60)
        before = len(self._items)
        self._items = deque(
            (item for item in self._items
             if item.get("expired_at", 0) > cutoff),
            maxlen=self._items.maxlen,
        )
        removed = before - len(self._items)
        if removed:
            logger.debug(f"[history] Pruned {removed} expired entries "
                         f"({len(self._items)} remaining)")

    @property
    def count(self) -> int:
        return len(self._items)


# Singleton
signal_history = SignalHistory()
