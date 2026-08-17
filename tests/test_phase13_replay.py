"""Phase 13 — Replayability Engine acceptance tests.

Acceptance criterion:
  Identical snapshot + identical code/configuration must produce identical output.
  If not, emit a REPLAY_MISMATCH diagnostic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.decision_snapshot import DecisionSnapshot, _CODE_VERSION, _CONFIG_HASH
from backend.data_quality_gate import validate_candles, QualityResult
from backend.replay_engine import (
    ReplayEngine,
    ReplayResult,
    ReplayMismatchError,
    replay_engine,
    _deep_equal,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_candle(t, o, h, l, c, v=1000):
    """Create a mock candle with valid OHLCV."""
    candle = MagicMock()
    candle.time = t
    candle.open = o
    candle.high = h
    candle.low = l
    candle.close = c
    candle.volume = v
    return candle


def _fresh_candles(n=25, tf="1H"):
    """Create n fresh mock candles that pass the quality gate."""
    now = datetime.now(timezone.utc).timestamp()
    tf_sec = {"1H": 3600, "15M": 900, "4H": 14400}.get(tf, 3600)
    return tuple(
        _make_candle(now - (n - i) * tf_sec, 100 + i * 0.1, 105 + i * 0.1, 95 + i * 0.1, 102 + i * 0.1)
        for i in range(n)
    )


def _build_snapshot(symbols=("BTCUSDT",), htf_tfs=("1H",), ltf_tfs=("15M",), snap_id="test-snap-001"):
    """Build a DecisionSnapshot with fresh candles for testing."""
    import hashlib
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

    snap_id_final = f"{snap_id}-{symbols[0]}" if len(symbols) == 1 else snap_id

    return DecisionSnapshot(
        snapshot_id=snap_id_final,
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


# ── ReplayResult tests ────────────────────────────────────────────────────────

class TestReplayResult:

    def test_result_has_all_stage_fields(self):
        """ReplayResult must have fields for every pipeline stage."""
        r = ReplayResult(
            snapshot_id="snap-1",
            code_version="v1",
            configuration_hash="cfg",
            d1_outputs=(),
            d2_outputs=(),
            evidence_ids=(),
            alignment=(),
            d3_states=(),
            confidence_scores=(),
            trade_plans=(),
            risk_decisions=(),
            stage_timings={},
        )
        assert r.snapshot_id == "snap-1"
        assert r.d1_outputs == ()
        assert r.d2_outputs == ()
        assert r.evidence_ids == ()
        assert r.alignment == ()
        assert r.d3_states == ()
        assert r.confidence_scores == ()
        assert r.trade_plans == ()
        assert r.risk_decisions == ()
        assert r.mismatches == ()

    def test_result_has_timestamp_auto_set(self):
        """timestamp should auto-populate if not given."""
        r = ReplayResult(
            snapshot_id="snap",
            code_version="v1",
            configuration_hash="cfg",
            d1_outputs=(),
            d2_outputs=(),
            evidence_ids=(),
            alignment=(),
            d3_states=(),
            confidence_scores=(),
            trade_plans=(),
            risk_decisions=(),
        )
        assert r.timestamp > 0

    def test_result_is_frozen(self):
        """ReplayResult must be immutable (frozen=True)."""
        r = ReplayResult(
            snapshot_id="snap",
            code_version="v1",
            configuration_hash="cfg",
            d1_outputs=(),
            d2_outputs=(),
            evidence_ids=(),
            alignment=(),
            d3_states=(),
            confidence_scores=(),
            trade_plans=(),
            risk_decisions=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            r.snapshot_id = "new"

    def test_has_mismatches_false_when_empty(self):
        r = ReplayResult(
            snapshot_id="snap", code_version="v1", configuration_hash="cfg",
            d1_outputs=(), d2_outputs=(), evidence_ids=(), alignment=(),
            d3_states=(), confidence_scores=(), trade_plans=(), risk_decisions=(),
        )
        assert r.has_mismatches() is False

    def test_has_mismatches_true_when_set(self):
        r = ReplayResult(
            snapshot_id="snap", code_version="v1", configuration_hash="cfg",
            d1_outputs=(), d2_outputs=(), evidence_ids=(), alignment=(),
            d3_states=(), confidence_scores=(), trade_plans=(), risk_decisions=(),
            mismatches=("diff: x vs y",),
        )
        assert r.has_mismatches() is True

    def test_to_dict_roundtrip(self):
        """to_dict() should return a plain dict with all fields."""
        r = ReplayResult(
            snapshot_id="snap", code_version="v1", configuration_hash="cfg",
            d1_outputs=({"tier": "A"},), d2_outputs=(), evidence_ids=("e1",),
            alignment=({"level": "STRONG",}), d3_states=({"state": "S1"},),
            confidence_scores=(85,), trade_plans=(), risk_decisions=(),
            stage_timings={"d1": 1.0},
            mismatches=(),
        )
        d = r.to_dict()
        assert d["snapshot_id"] == "snap"
        assert d["code_version"] == "v1"
        assert d["d1_outputs"] == [{"tier": "A"}]
        assert d["confidence_scores"] == [85]
        assert d["stage_timings"] == {"d1": 1.0}


# ── Deep equal helper tests ────────────────────────────────────────────────────

class TestDeepEqual:

    def test_equal_simple_values(self):
        assert _deep_equal(1, 1) == []
        assert _deep_equal("a", "a") == []
        assert _deep_equal(True, True) == []

    def test_unequal_simple_values(self):
        diffs = _deep_equal(1, 2)
        assert len(diffs) == 1
        assert "1" in diffs[0]

    def test_type_mismatch(self):
        diffs = _deep_equal(1, "1")
        assert len(diffs) == 1
        assert "type mismatch" in diffs[0]

    def test_float_tolerance(self):
        assert _deep_equal(1.0, 1.0000000001) == []
        diffs = _deep_equal(1.0, 1.001)
        assert len(diffs) > 0

    def test_dict_equal(self):
        assert _deep_equal({"a": 1}, {"a": 1}) == []

    def test_dict_missing_key(self):
        diffs = _deep_equal({"a": 1}, {})
        assert any("missing" in d for d in diffs)

    def test_dict_extra_key(self):
        diffs = _deep_equal({}, {"a": 1})
        assert any("missing" in d for d in diffs)

    def test_dict_nested_diff(self):
        diffs = _deep_equal({"a": {"b": 1}}, {"a": {"b": 2}})
        assert len(diffs) == 1
        assert "b" in diffs[0]

    def test_list_equal(self):
        assert _deep_equal([1, 2], [1, 2]) == []

    def test_list_length_mismatch(self):
        diffs = _deep_equal([1], [1, 2])
        assert any("length" in d for d in diffs)

    def test_list_nested_diff(self):
        diffs = _deep_equal([1, 2], [1, 3])
        assert len(diffs) == 1
        assert "[1]" in diffs[0]


# ── ReplayMismatchError tests ──────────────────────────────────────────────────

class TestReplayMismatchError:

    def test_exception_carries_diffs(self):
        err = ReplayMismatchError(["diff 1", "diff 2"])
        assert len(err.diffs) == 2
        assert "REPLAY_MISMATCH" in str(err)

    def test_empty_diffs(self):
        err = ReplayMismatchError([])
        assert "0 difference" in str(err)

    def test_is_exception(self):
        with pytest.raises(ReplayMismatchError):
            raise ReplayMismatchError(["x"])


# ── ReplayEngine tests ────────────────────────────────────────────────────────

class TestReplayEngine:

    def setup_method(self):
        """Clear stores and reset engine before each test."""
        self.engine = ReplayEngine()
        self.engine._clear_stores()

    def test_replay_returns_replay_result(self):
        """replay() must return a ReplayResult with matching provenance."""
        snap = _build_snapshot(["BTCUSDT"])
        result = self.engine.replay(snap)
        assert isinstance(result, ReplayResult)
        assert result.snapshot_id == snap.snapshot_id
        assert result.code_version == snap.code_version
        assert result.configuration_hash == snap.configuration_hash

    def test_replay_produces_timings(self):
        """ReplayResult must include stage_timings."""
        snap = _build_snapshot(["BTCUSDT"])
        result = self.engine.replay(snap)
        assert "d1" in result.stage_timings
        assert "d2" in result.stage_timings
        assert "total" in result.stage_timings

    def test_replay_emits_stage_outputs(self):
        """ReplayResult must have stage outputs (may be empty for no signals)."""
        snap = _build_snapshot(["BTCUSDT"])
        result = self.engine.replay(snap)
        assert hasattr(result, "d1_outputs")
        assert hasattr(result, "d2_outputs")
        assert hasattr(result, "alignment")
        assert hasattr(result, "d3_states")

    def test_determinism_two_runs_match(self):
        """Two identical replays must produce identical output."""
        snap = _build_snapshot(["BTCUSDT"])
        r1 = self.engine.replay(snap)
        r2 = self.engine.replay(snap)
        diffs = self.engine.compare(r1, r2)
        assert diffs == [], f"Expected identical output, got diffs: {diffs}"

    def test_verify_determinism_passes(self):
        """verify_determinism should succeed for stable data."""
        snap = _build_snapshot(["BTCUSDT"])
        result = self.engine.verify_determinism(snap, runs=3)
        assert isinstance(result, ReplayResult)
        assert result.has_mismatches() is False

    def test_verify_determinism_raises_on_mismatch(self):
        """verify_determinism must raise ReplayMismatchError if runs differ."""
        snap = _build_snapshot(["BTCUSDT"])

        with patch.object(self.engine, '_run_d1', side_effect=[
            [{"tier": "SNIPER"}], [{"tier": "OPPORTUNITY"}]
        ]):
            with patch.object(self.engine, '_run_d2', return_value=[]):
                with patch.object(self.engine, '_collect_evidence', return_value=[]):
                    with patch.object(self.engine, '_run_alignment', return_value=[]):
                        with patch.object(self.engine, '_run_d3', return_value=[]):
                            with patch.object(self.engine, '_compute_confidence', return_value=[50]):
                                with patch.object(self.engine, '_run_trade_plan', return_value=[]):
                                    with patch.object(self.engine, '_run_risk', return_value=[]):
                                        with pytest.raises(ReplayMismatchError) as exc_info:
                                            self.engine.verify_determinism(snap, runs=2)
                                        assert len(exc_info.value.diffs) > 0
                                        assert "Run 0 vs Run 1" in exc_info.value.diffs[0]

    def test_replay_mismatch_error_diagnostic(self):
        """REPLAY_MISMATCH must be emitted and visible in error message."""
        snap = _build_snapshot(["BTCUSDT"])
        try:
            with patch.object(self.engine, '_run_d1', side_effect=[
                [{"tier": "A"}], [{"tier": "B"}]
            ]):
                with patch.object(self.engine, '_run_d2', return_value=[]):
                    with patch.object(self.engine, '_collect_evidence', return_value=[]):
                        with patch.object(self.engine, '_run_alignment', return_value=[]):
                            with patch.object(self.engine, '_run_d3', return_value=[]):
                                with patch.object(self.engine, '_compute_confidence', return_value=[50]):
                                    with patch.object(self.engine, '_run_trade_plan', return_value=[]):
                                        with patch.object(self.engine, '_run_risk', return_value=[]):
                                            self.engine.verify_determinism(snap, runs=2)
        except ReplayMismatchError as e:
            assert "REPLAY_MISMATCH" in str(e)
            assert "difference" in str(e)

    def test_different_snapshots_independent(self):
        """Two different snapshots should both produce valid ReplayResults."""
        snap1 = _build_snapshot(["BTCUSDT"], snap_id="snap-btc")
        snap2 = _build_snapshot(["ETHUSDT"], snap_id="snap-eth")
        r1 = self.engine.replay(snap1)
        r2 = self.engine.replay(snap2)
        assert r1.snapshot_id == snap1.snapshot_id
        assert r2.snapshot_id == snap2.snapshot_id

    def test_replay_result_fields_all_tuples(self):
        """All list outputs should be frozen as tuples."""
        snap = _build_snapshot(["BTCUSDT"])
        result = self.engine.replay(snap)
        assert isinstance(result.d1_outputs, tuple)
        assert isinstance(result.d2_outputs, tuple)
        assert isinstance(result.alignment, tuple)
        assert isinstance(result.d3_states, tuple)
        assert isinstance(result.confidence_scores, tuple)
        assert isinstance(result.trade_plans, tuple)
        assert isinstance(result.risk_decisions, tuple)
        assert isinstance(result.evidence_ids, tuple)

    def test_compare_identical_results(self):
        """compare() returns empty list for identical results."""
        snap = _build_snapshot(["BTCUSDT"])
        r1 = self.engine.replay(snap)
        r2 = self.engine.replay(snap)
        diffs = self.engine.compare(r1, r2)
        assert diffs == []

    def test_compare_different_provenance(self):
        """compare() detects different snapshot_id."""
        r1 = ReplayResult(
            snapshot_id="snap-1", code_version="v1", configuration_hash="cfg",
            d1_outputs=(), d2_outputs=(), evidence_ids=(), alignment=(),
            d3_states=(), confidence_scores=(), trade_plans=(), risk_decisions=(),
        )
        r2 = ReplayResult(
            snapshot_id="snap-2", code_version="v1", configuration_hash="cfg",
            d1_outputs=(), d2_outputs=(), evidence_ids=(), alignment=(),
            d3_states=(), confidence_scores=(), trade_plans=(), risk_decisions=(),
        )
        diffs = self.engine.compare(r1, r2)
        assert any("snapshot_id" in d for d in diffs)

    def test_verify_determinism_requires_min_two_runs(self):
        """verify_determinism should reject runs < 2."""
        snap = _build_snapshot(["BTCUSDT"])
        with pytest.raises(ValueError):
            self.engine.verify_determinism(snap, runs=1)


# ── Module-level singleton tests ──────────────────────────────────────────────

class TestReplayEngineSingleton:

    def test_module_singleton_exists(self):
        """replay_engine module singleton must exist."""
        from backend.replay_engine import replay_engine as re
        assert isinstance(re, ReplayEngine)

    def test_convenience_replay_snapshot(self):
        """replay_snapshot() convenience function must work."""
        from backend.replay_engine import replay_snapshot
        snap = _build_snapshot(["BTCUSDT"])
        result = replay_snapshot(snap, runs=2)
        assert isinstance(result, ReplayResult)

    def test_convenience_compare_replays(self):
        """compare_replays() convenience function must work."""
        from backend.replay_engine import compare_replays
        snap = _build_snapshot(["BTCUSDT"])
        r1 = replay_engine.replay(snap)
        r2 = replay_engine.replay(snap)
        diffs = compare_replays(r1, r2)
        assert diffs == []


# ── End-to-end determinism with real pipeline ──────────────────────────────────

class TestEndToEndDeterminism:

    """Run the real pipeline (no mocks) to verify determinism.

    Uses snapshot with injected candles. Pipeline stages that produce no
    signals return empty lists — that's fine; we test that empty outputs
    are also deterministic.
    """

    def setup_method(self):
        self.engine = ReplayEngine()

    def test_full_pipeline_replay_deterministic(self):
        """Full pipeline replay must be deterministic."""
        snap = _build_snapshot(["BTCUSDT", "ETHUSDT"], htf_tfs=("1H",), ltf_tfs=("15M",))
        r1 = self.engine.replay(snap)
        r2 = self.engine.replay(snap)
        diffs = self.engine.compare(r1, r2)
        assert diffs == [], f"Full pipeline not deterministic: {diffs}"

    def test_three_run_determinism(self):
        """Three runs must all match."""
        snap = _build_snapshot(["BTCUSDT"])
        results = [self.engine.replay(snap) for _ in range(3)]
        for i in range(1, 3):
            diffs = self.engine.compare(results[0], results[i])
            assert diffs == [], f"Run 0 vs Run {i} differed: {diffs}"


# ── __all__ exports ────────────────────────────────────────────────────────────

class TestModuleExports:
    def test_all_exports_present(self):
        from backend.replay_engine import __all__
        assert "ReplayEngine" in __all__
        assert "ReplayResult" in __all__
        assert "ReplayMismatchError" in __all__
        assert "replay_engine" in __all__
        assert "replay_snapshot" in __all__
        assert "compare_replays" in __all__
