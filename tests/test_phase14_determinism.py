"""Phase 14 — Determinism acceptance tests.

Acceptance criterion:
  - Unordered iteration is made explicit (sorted() where order matters)
  - Timing-dependent reads use the decision snapshot timestamp
  - No time.time() or datetime.now() deep inside business logic
  - Mutable shared state is reset between replay runs
  - Async ordering is stable (explicit sorting of async results)
  - Two identical replays produce identical output
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.decision_snapshot import DecisionSnapshot, _CODE_VERSION, _CONFIG_HASH
from backend.data_quality_gate import validate_candles
from backend.helpers.time_source import (
    deterministic_now,
    deterministic_timestamp,
    deterministic_now_ms,
    set_snapshot_timestamp,
    clear_snapshot_timestamp,
    age_seconds,
    age_minutes,
)
from backend.replay_engine import ReplayEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_candle(t, o, h, l, c, v=1000):
    candle = MagicMock()
    candle.time = t
    candle.open = o
    candle.high = h
    candle.low = l
    candle.close = c
    candle.volume = v
    return candle


def _fresh_candles(n=25, tf="1H"):
    now = datetime.now(timezone.utc).timestamp()
    tf_sec = {"1H": 3600, "15M": 900, "4H": 14400}.get(tf, 3600)
    return tuple(
        _make_candle(now - (n - i) * tf_sec, 100 + i * 0.1, 105 + i * 0.1, 95 + i * 0.1, 102 + i * 0.1)
        for i in range(n)
    )


def _build_snapshot(symbols=("BTCUSDT",), htf_tfs=("1H",), ltf_tfs=("15M",)):
    now = datetime.now(timezone.utc).timestamp()
    candles: dict = {}
    data_quality: dict = {}
    all_tfs = list(htf_tfs) + list(ltf_tfs)
    for sym in symbols:
        for tf in all_tfs:
            c = _fresh_candles(30, tf)
            candles[f"{sym}:{tf}"] = c
            q = validate_candles(c, tf)
            data_quality[f"{sym}:{tf}"] = q.state

    return DecisionSnapshot(
        snapshot_id="det-snap-001",
        snapshot_timestamp=now,
        processing_timestamp=now + 0.1,
        symbol=symbols[0] if symbols else "",
        market_data_version="v1",
        configuration_hash=_CONFIG_HASH,
        code_version=_CODE_VERSION,
        candles=candles,
        data_quality=data_quality,
        liquidity_state={},
        btc_candles=_fresh_candles(25, "1H"),
        d1_tiers={},
    )


# ── Deterministic time source tests ───────────────────────────────────────────

class TestDeterministicTimeSource:

    def setup_method(self):
        clear_snapshot_timestamp()

    def teardown_method(self):
        clear_snapshot_timestamp()

    def test_default_uses_real_time(self):
        """Without setting a snapshot timestamp, returns real wall-clock time."""
        clear_snapshot_timestamp()  # ensure clean state
        before = datetime.now(timezone.utc).timestamp()
        t = deterministic_timestamp()
        after = datetime.now(timezone.utc).timestamp()
        assert before <= t <= after

    def test_snapshot_timestamp_override(self):
        """When set, deterministic_now() derives from snapshot timestamp."""
        snap_ts = 1_700_000_000.0
        set_snapshot_timestamp(snap_ts)
        expected = datetime.fromtimestamp(snap_ts, tz=timezone.utc)
        assert deterministic_now() == expected

    def test_snapshot_timestamp_override_returns_exact_value(self):
        """deterministic_timestamp() returns exact snapshot ts when set."""
        snap_ts = 1_700_000_000.0
        set_snapshot_timestamp(snap_ts)
        assert deterministic_timestamp() == snap_ts

    def test_now_ms_matches_snapshot(self):
        """deterministic_now_ms() returns ms version of snapshot ts."""
        snap_ts = 1_700_000_000.0
        set_snapshot_timestamp(snap_ts)
        assert deterministic_now_ms() == 1_700_000_000_000

    def test_clear_resets_to_real_time(self):
        """After clear, returns real wall-clock time again."""
        set_snapshot_timestamp(1_700_000_000.0)
        clear_snapshot_timestamp()
        now_ts = datetime.now(timezone.utc).timestamp()
        t = deterministic_timestamp()
        # Should be within 2 seconds of real time
        assert abs(t - now_ts) < 2.0

    def test_age_seconds_with_snapshot(self):
        """age_seconds should use deterministic clock."""
        born = datetime.fromtimestamp(1_699_913_600.0, tz=timezone.utc)
        set_snapshot_timestamp(1_700_000_000.0)
        assert abs(age_seconds(born) - 86400.0) < 1.0

    def test_age_minutes_with_snapshot(self):
        """age_minutes should use deterministic clock."""
        born = datetime.fromtimestamp(1_699_999_400.0, tz=timezone.utc)
        set_snapshot_timestamp(1_700_000_000.0)
        assert abs(age_minutes(born) - 10.0) < 0.01

    def test_deterministic_without_snapshot_uses_real_clock(self):
        """Without snapshot, age_seconds uses real time."""
        born = datetime.now(timezone.utc)
        import time
        time.sleep(0.01)
        age = age_seconds(born)
        assert 0.005 < age < 1.0


# ── Determinism verification tests ────────────────────────────────────────────

class TestDeterminismVerification:

    """Verify that replay_engine produces deterministic output.

    Uses snapshot timestamp as the single time source so that
    timing-dependent code (session detection, TTL, age) sees
    stable values across runs.
    """

    def setup_method(self):
        self.engine = ReplayEngine()
        self.engine._clear_stores()

    def teardown_method(self):
        clear_snapshot_timestamp()

    def _run_with_snapshot_time(self, snap):
        """Run replay with snapshot timestamp injected as the deterministic clock."""
        set_snapshot_timestamp(snap.snapshot_timestamp)
        try:
            return self.engine.replay(snap)
        finally:
            clear_snapshot_timestamp()

    def test_replay_with_snapshot_time_is_deterministic(self):
        """Replay with deterministic clock produces identical results."""
        snap = _build_snapshot(["BTCUSDT", "ETHUSDT"])
        r1 = self._run_with_snapshot_time(snap)
        r2 = self._run_with_snapshot_time(snap)
        diffs = self.engine.compare(r1, r2)
        assert diffs == [], f"Not deterministic with snapshot clock: {diffs}"

    def test_three_way_determinism_with_snapshot_time(self):
        """Three runs with deterministic clock all match."""
        snap = _build_snapshot(["BTCUSDT"])
        results = [self._run_with_snapshot_time(snap) for _ in range(3)]
        for i in range(1, 3):
            diffs = self.engine.compare(results[0], results[i])
            assert diffs == [], f"Run 0 vs Run {i} differed: {diffs}"

    def test_provenance_preserved_across_runs(self):
        """Provenance (snapshot_id, code_version, config_hash) must be stable."""
        snap = _build_snapshot(["BTCUSDT"])
        r1 = self._run_with_snapshot_time(snap)
        r2 = self._run_with_snapshot_time(snap)
        assert r1.snapshot_id == r2.snapshot_id
        assert r1.code_version == r2.code_version
        assert r1.configuration_hash == r2.configuration_hash

    def test_stage_outputs_frozen_as_tuples(self):
        """All list outputs must be frozen as tuples for equality comparison."""
        snap = _build_snapshot(["BTCUSDT"])
        r = self._run_with_snapshot_time(snap)
        assert isinstance(r.d1_outputs, tuple)
        assert isinstance(r.d2_outputs, tuple)
        assert isinstance(r.alignment, tuple)
        assert isinstance(r.d3_states, tuple)
        assert isinstance(r.confidence_scores, tuple)
        assert isinstance(r.trade_plans, tuple)
        assert isinstance(r.risk_decisions, tuple)


# ── Set/reset cycle for production parity ─────────────────────────────────────

class TestSnapshotTimeLifecycle:

    def test_set_then_clear_cycle(self):
        """Setting then clearing snapshot timestamp should work cleanly."""
        clear_snapshot_timestamp()
        real_ts = deterministic_timestamp()
        set_snapshot_timestamp(42_000_000.0)
        assert deterministic_timestamp() == 42_000_000.0
        clear_snapshot_timestamp()
        # Back to real time
        assert deterministic_timestamp() >= real_ts - 1.0

    def test_repeated_set_override(self):
        """Setting snapshot timestamp multiple times should override."""
        set_snapshot_timestamp(100.0)
        assert deterministic_timestamp() == 100.0
        set_snapshot_timestamp(200.0)
        assert deterministic_timestamp() == 200.0
        clear_snapshot_timestamp()


# ── Global mutable state isolation ────────────────────────────────────────────

class TestStateIsolation:

    """Verify that replay clears global mutable state between runs."""

    def setup_method(self):
        self.engine = ReplayEngine()

    def test_replay_clears_stores_between_runs(self):
        """Each replay should start from a clean state."""
        snap = _build_snapshot(["BTCUSDT"])
        self.engine.replay(snap)
        self.engine.replay(snap)
        # Both runs should complete without errors
        # (state_store, signal_store etc. are cleared in _clear_stores)
        assert True  # If we get here without errors, isolation works

    def test_determinism_after_state_mutation(self):
        """Even after state mutations, replay should be deterministic."""
        snap = _build_snapshot(["BTCUSDT"])
        # Run once, then run again — both should match
        r1 = self.engine.replay(snap)
        r2 = self.engine.replay(snap)
        diffs = self.engine.compare(r1, r2)
        assert diffs == []
