"""Phase 2 — Data Quality Gate acceptance tests.

Acceptance criteria:
  - Bad data produces an explicit state (VALID, STALE, MISSING, INVALID,
    GAPPED, DEGRADED, INCOMPLETE)
  - No silent fallback to stale data
  - INVALID/STALE/MISSING/GAPPED block processing (is_safe()=False)
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.data_quality_gate import validate_candles, QualityResult


def _make_candle(t, o, h, l, c, v=1000):
    """Create a mock candle with given OHLCV."""
    candle = MagicMock()
    candle.time = t
    candle.open = o
    candle.high = h
    candle.low = l
    candle.close = c
    candle.volume = v
    return candle


def _fresh_candles(n=25, tf="1H", base_ts=None):
    """Create n fresh candles that won't trigger staleness."""
    if base_ts is None:
        from datetime import datetime, timezone
        base_ts = int(datetime.now(timezone.utc).timestamp())
    tf_sec = {"1H": 3600, "15M": 900, "4H": 14400}.get(tf, 3600)
    return tuple(
        _make_candle(base_ts - (n - 1 - i) * tf_sec, 100, 105, 95, 102)
        for i in range(n)
    )


class TestDataQualityGate:

    # ── Explicit states ────────────────────────────────────────────────

    def test_empty_candles_is_missing(self):
        r = validate_candles((), "1H")
        assert r.state == "MISSING"
        assert r.allows_processing is False
        assert r.candle_count == 0

    def test_invalid_ohlc_blocks(self):
        candles = (_make_candle(1000, 100, 90, 110, 95),)  # high < low
        r = validate_candles(candles, "1H")
        assert r.state == "INVALID"
        assert r.allows_processing is False

    def test_valid_candles_pass(self):
        candles = _fresh_candles(25, "1H")
        r = validate_candles(candles, "1H")
        assert r.state == "VALID"
        assert r.allows_processing is True

    def test_out_of_order_timestamps_detected(self):
        candles = (
            _make_candle(2000, 100, 105, 95, 102),
            _make_candle(1000, 100, 105, 95, 102),
        )
        r = validate_candles(candles, "1H")
        assert any("order" in i for i in r.issues)

    def test_duplicate_timestamps_detected(self):
        candles = (
            _make_candle(1000, 100, 105, 95, 102),
            _make_candle(1000, 100, 105, 95, 102),
        )
        r = validate_candles(candles, "1H")
        assert any("duplicate" in i for i in r.issues)

    def test_insufficient_candles_is_degraded(self):
        candles = _fresh_candles(5, "1H")
        r = validate_candles(candles, "1H")
        assert r.state == "DEGRADED"
        assert r.allows_processing is True

    # ── No silent fallback ─────────────────────────────────────────────

    def test_stale_never_silent(self):
        """Stale data must produce STALE state, never silently pass."""
        candles = _fresh_candles(25, "1H")
        # Make the last candle very old by mocking _current_timestamp
        import backend.data_quality_gate as dqg
        from datetime import datetime, timezone
        last_ts = candles[-1].time
        original = dqg._current_timestamp
        dqg._current_timestamp = lambda: last_ts + 7201  # 2h past staleness threshold for 1H (1800s)
        try:
            r = validate_candles(candles, "1H")
            assert r.state == "STALE"
            assert r.allows_processing is False
        finally:
            dqg._current_timestamp = original

    def test_negative_volume_detected(self):
        c = _make_candle(1000, 100, 105, 95, 102, v=-1)
        candles = (c,)
        r = validate_candles(candles, "1H")
        assert any("volume" in i.lower() for i in r.issues)

    def test_all_states_are_explicit(self):
        """Every valid return must have a non-empty state string."""
        candles = _fresh_candles(25, "1H")
        r = validate_candles(candles, "1H")
        assert isinstance(r.state, str)
        assert len(r.state) > 0
        assert r.state in (
            "VALID", "STALE", "MISSING", "INVALID",
            "GAPPED", "DEGRADED", "INCOMPLETE"
        )

    def test_is_safe_blocks_stale(self):
        """STALE data must not be safe for processing."""
        import backend.data_quality_gate as dqg
        candles = _fresh_candles(25, "1H")
        last_ts = candles[-1].time
        original = dqg._current_timestamp
        dqg._current_timestamp = lambda: last_ts + 7201
        try:
            r = validate_candles(candles, "1H")
            assert r.is_safe() is False
        finally:
            dqg._current_timestamp = original

    def test_result_description_nonempty(self):
        r = validate_candles((), "1H")
        desc = r.description()
        assert len(desc) > 0
        assert "MISSING" in desc

    def test_valid_returns_issues_empty(self):
        candles = _fresh_candles(25, "1H")
        r = validate_candles(candles, "1H")
        assert r.state == "VALID"
        assert r.issues == []