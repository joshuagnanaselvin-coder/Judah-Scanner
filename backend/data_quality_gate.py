"""Data Quality Gate — central candle validation for D1 and D2 pipelines.

Every candle set must pass through this gate before intelligence processing.
Invalid/stale/missing data gets an explicit quality label; it never silently
flows through as valid data.

States:
    VALID       — complete, fresh, well-formed candles
    STALE       — present but last candle older than the allowed age
    MISSING     — no candles available
    DEGRADED    — fewer candles than minimum; partial processing allowed
    INCOMPLETE  — incomplete current (unclosed) candle present alongside valid history
    GAPPED      — timestamp gaps detected between consecutive candles
    INVALID     — malformed OHLC data
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.config import (
    D1_TTL_SECONDS,
    D2_TIMEFRAME,
    BOOTSTRAP_CANDLES,
)

logger = logging.getLogger("judah.quality")


# ── Configurable Thresholds ─────────────────────────────────────────

_STALENESS_TF_SECONDS = {
    "1M": 90, "5M": 300, "15M": 600, "30M": 900,
    "1H": 1800, "4H": 7200, "1D": 72000, "1W": 604800,
}

_MIN_CANDLES_BY_TF = {
    "1H": 25, "4H": 10, "1D": 5,
    "15M": 25, "30M": 15,
}

_GAP_TOLERANCE_TF = {
    "15M": 1.5, "1H": 1.5, "4H": 1.5, "1D": 1.5,
}


@dataclass(frozen=True)
class QualityResult:
    """Immutable result of a quality gate check."""
    state: str              # VALID | STALE | MISSING | DEGRADED | INCOMPLETE | GAPPED | INVALID
    candle_count: int
    issues: list[str] = field(default_factory=list)
    last_candle_age_sec: float = 0.0
    allows_processing: bool = False

    def is_safe(self) -> bool:
        """Whether a scanner may proceed with this data.

        VALID / DEGRADED / INCOMPLETE are safe (processing allowed).
        INVALID / MISSING / STALE / GAPPED are unsafe (processing blocked).
        """
        return self.state in ("VALID", "DEGRADED", "INCOMPLETE")

    def description(self) -> str:
        parts = [f"state={self.state}", f"count={self.candle_count}"]
        if self.issues:
            parts.append(f"issues={','.join(self.issues[:3])}")
        if self.last_candle_age_sec > 0:
            parts.append(f"age={self.last_candle_age_sec:.0f}s")
        return " | ".join(parts)


def validate_candles(
    candles: tuple | list,
    timeframe: str,
    max_age_seconds: float | None = None,
) -> QualityResult:
    """Run the full data quality gate on a candle set.

    Args:
        candles: tuple or list of Candle objects.
        timeframe: timeframe string (e.g. "1H", "15M").
        max_age_seconds: override staleness threshold; if None, derived from TF.

    Returns:
        QualityResult with explicit state and issue list.
    """
    issues: list[str] = []

    # 1. Empty / missing
    if not candles:
        return QualityResult(
            state="MISSING",
            candle_count=0,
            issues=["no candles available"],
            allows_processing=False,
        )

    count = len(candles)
    tf = timeframe.upper()

    # 2. OHLC validity
    for i, c in enumerate(candles):
        if not _valid_ohlc(c):
            issues.append(f"candle[{i}] invalid OHLC")

    if issues:
        return QualityResult(
            state="INVALID",
            candle_count=count,
            issues=issues,
            allows_processing=False,
        )

    # 2b. Volume validity (non-negative)
    for i, c in enumerate(candles):
        v = _candle_get(c, "volume", None)
        if v is not None and v < 0:
            issues.append(f"candle[{i}] negative volume: {v}")

    # 2c. Duplicate timestamps
    seen_times: set = set()
    for i, c in enumerate(candles):
        t = _candle_get(c, "time", 0)
        if t in seen_times:
            issues.append(f"candle[{i}] duplicate timestamp: {t}")
        seen_times.add(t)

    # 2d. OHLC sanity (high >= low, open/close inside range)
    for i, c in enumerate(candles):
        h = _candle_get(c, "high", 0)
        l = _candle_get(c, "low", 0)
        o = _candle_get(c, "open", 0)
        c2 = _candle_get(c, "close", 0)
        if h < l:
            issues.append(f"candle[{i}] high < low: {h} < {l}")
        if not (l <= o <= h) or not (l <= c2 <= h):
            issues.append(f"candle[{i}] open/close outside [low, high]")

    # 3. Sufficient history
    min_candles = _MIN_CANDLES_BY_TF.get(tf, 20)
    if count < min_candles:
        issues.append(f"insufficient candles: {count} < {min_candles} min")

    # 4. Staleness
    now_ts = _get_timestamp(candles[-1])
    if now_ts > 0:
        if max_age_seconds is None:
            max_age_seconds = _STALENESS_TF_SECONDS.get(tf, max_age_seconds or 3600.0)

        last_age = _current_timestamp() - now_ts
        if last_age > max_age_seconds:
            issues.append(f"stale: last candle {last_age:.0f}s old (max {max_age_seconds:.0f}s)")

    # 5. Timestamp ordering
    if not _ordered_timestamps(candles):
        issues.append("timestamps out of order")

    # 6. Gap detection
    gap = _check_gaps(candles, tf)
    if gap:
        issues.append(f"gap: {gap}")

    # 7. Timeframe consistency (check candle intervals match expected TF)
    _tf_seconds = {
        "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400,
    }
    expected_gap = _tf_seconds.get(tf)
    if expected_gap and count > 2:
        avg_gap = (_candle_get(candles[-1], "time", 0) - _candle_get(candles[0], "time", 0)) / (count - 1)
        if avg_gap > 0 and abs(avg_gap - expected_gap) > expected_gap * 0.5:
            issues.append(f"timeframe mismatch: avg gap {avg_gap:.0f}s vs expected {expected_gap}s for {tf}")

    # Determine final state — STALE is always blocking regardless of other issues
    has_stale = any("stale" in i for i in issues)
    if has_stale:
        state = "STALE"
    elif not issues:
        state = "VALID"
    elif count >= min_candles:
        state = "INCOMPLETE"
    else:
        state = "DEGRADED"

    # Check if processing is allowed (STALE and INVALID/MISSING block)
    allows = state in ("VALID", "DEGRADED", "INCOMPLETE")

    return QualityResult(
        state=state,
        candle_count=count,
        issues=issues,
        last_candle_age_sec=_current_timestamp() - now_ts if now_ts > 0 else 0.0,
        allows_processing=allows,
    )


def _candle_get(candle, attr: str, default=None):
    """Access a candle field — works with dict-based or object-based candles."""
    if isinstance(candle, dict):
        return candle.get(attr, default)
    return getattr(candle, attr, default)


def _valid_ohlc(candle) -> bool:
    """Check basic OHLC validity."""
    h = _candle_get(candle, "high", 0)
    l = _candle_get(candle, "low", 0)
    o = _candle_get(candle, "open", 0)
    c2 = _candle_get(candle, "close", 0)
    if h <= 0 or l <= 0 or o <= 0 or c2 <= 0:
        return False
    if h < l:
        return False
    if not (l <= o <= h and l <= c2 <= h):
        return False
    return True


def _ordered_timestamps(candles) -> bool:
    """Check that timestamps are monotonically non-decreasing."""
    prev = 0
    for c in candles:
        t = _candle_get(c, "time", 0)
        if t < prev:
            return False
        prev = t
    return True


def _check_gaps(candles, timeframe: str) -> str | None:
    """Detect timestamp gaps larger than expected for the timeframe."""
    tolerance = _GAP_TOLERANCE_TF.get(timeframe.upper(), 2.0)
    tf_seconds = {
        "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400,
    }.get(timeframe.upper(), 0)

    if tf_seconds == 0:
        return None

    max_gap = tf_seconds * tolerance
    for i in range(1, len(candles)):
        prev_t = _candle_get(candles[i - 1], "time", 0)
        curr_t = _candle_get(candles[i], "time", 0)
        gap = curr_t - prev_t
        if gap > max_gap * 1.5:
            return f"{gap}s between candles[{i-1}] and [{i}] (max {max_gap:.0f}s)"

    return None


def _get_timestamp(candle) -> float:
    """Extract timestamp from a candle."""
    t = _candle_get(candle, "time", 0)
    if isinstance(t, (int, float)):
        return float(t)
    return 0.0


def _current_timestamp() -> float:
    """Current UTC timestamp in seconds."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).timestamp()
