"""Phase 14 — Deterministic timestamp source.

For replayability, all timing-dependent business decisions must derive
their clock from a single, injectable source.

Business logic must NOT call `datetime.now()` or `time.time()` directly
as an implicit input to decisions — use deterministic_now() instead.

The snapshot timestamp is the single source of truth for a decision cycle.
During replay, it replaces real time so that the same snapshot always
produces the same result.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("judah.time")

# ── Deterministic clock ────────────────────────────────────────────────────────

_snapshot_ts: Optional[float] = None  # epoch seconds, set per decision cycle


def set_snapshot_timestamp(ts: float) -> None:
    """Set the deterministic clock for the current decision cycle.

    During replay, this is set to snapshot.snapshot_timestamp so that
    all downstream timing-dependent logic sees a stable clock.
    """
    global _snapshot_ts
    _snapshot_ts = ts


def clear_snapshot_timestamp() -> None:
    """Reset to real wall-clock time."""
    global _snapshot_ts
    _snapshot_ts = None


def deterministic_now() -> datetime:
    """Return current datetime.

    If a snapshot timestamp is set, derive from it.
    Otherwise use real wall-clock time (production mode).
    """
    if _snapshot_ts is not None:
        return datetime.fromtimestamp(_snapshot_ts, tz=timezone.utc)
    return datetime.now(timezone.utc)


def deterministic_timestamp() -> float:
    """Return current epoch timestamp.

    If a snapshot timestamp is set, use it.
    Otherwise use real wall-clock time.
    """
    if _snapshot_ts is not None:
        return _snapshot_ts
    return datetime.now(timezone.utc).timestamp()


def deterministic_now_ms() -> int:
    """Return current epoch timestamp in milliseconds."""
    return int(deterministic_timestamp() * 1000)


def age_seconds(born_at: datetime) -> float:
    """Return age of a born_at datetime in seconds, using deterministic clock."""
    now = deterministic_now()
    return (now - born_at).total_seconds()


def age_minutes(born_at: datetime) -> float:
    """Return age of a born_at datetime in minutes, using deterministic clock."""
    return age_seconds(born_at) / 60.0


__all__ = [
    "set_snapshot_timestamp",
    "clear_snapshot_timestamp",
    "deterministic_now",
    "deterministic_timestamp",
    "deterministic_now_ms",
    "age_seconds",
    "age_minutes",
]
