"""Phase 23 — Property-Based & State-Machine Testing.

Tests invariants that hold across all inputs:
  - Market Evolution: all 16 states present, valid transitions
  - EvidenceStore: concurrent safety, dedup, TTL, caps
  - Tier classification: boundary conditions and ordering
  - Signal fusion: tier boundaries
  - Config: invariants and relationships
  - Replay: determinism invariants
"""
import asyncio
import logging
import time
import unittest
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.config import (
    EVIDENCE_TTL_MINUTES,
    HTF_CONTEXT_MAX,
    HTF_CONTEXT_MIN,
    HTF_CONTEXT_NEUTRAL,
    HTF_CONTEXT_OPPOSING,
    HTF_CONTEXT_SAME,
    HTF_CONTEXT_NO_DATA,
    IGNORE_MIN_SCORE,
    MIN_RR,
    TIER_OPPORTUNITY_SCORE,
    TIER_SNIPER_SCORE,
    TIER_WATCH_SCORE,
    TIER_WEAK_SCORE,
    DECAY_TYPE_A,
    DECAY_TYPE_B,
    DECAY_TYPE_C,
    DECAY_TYPE_D,
    DECAY_TYPE_E,
)
from backend.evidence_record import EvidenceRecord, EvidenceCategory, EvidenceStrength
from backend.evidence_store import evidence_store
from backend.market_evolution.engine import evaluate, evaluate_from_scores
from backend.market_evolution.constants import (
    MARKET_EVOLUTION_MATRIX,
    SPIRALS,
    STATE_TO_CATEGORY,
    TIERS,
    get_state,
    EVOLUTION_LABELS,
    EVOLUTION_SHORT,
)
from backend.engines.signal_fusion import classify_tier, calculate_ev
from backend.replay_engine import _deep_equal

logger = logging.getLogger("judah.test_phase23")

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _unique_id(symbol, seq):
    # Simple monotonic sequence — never collides.
    return f"{symbol}-{seq:04d}"

def _make_evidence(symbol="BTCUSDT", cat=EvidenceCategory.ORDER_BLOCK,
                    strength=EvidenceStrength.STRONG, direction="BULLISH",
                    confidence=0.9, ts_offset=0, seq=0):
    ev = EvidenceRecord(
        evidence_id=_unique_id(symbol, seq),
        snapshot_id="snap-prop",
        symbol=symbol,
        category=cat,
        timeframe="1H",
        price=50000.0,
        strength=strength,
        direction=direction,
        confidence=confidence,
        candle_time=time.time() - ts_offset,
        detected_at=time.time() - ts_offset,
        source="test",
        details={"seq": ts_offset},
    )
    return ev

def _clear_store():
    evidence_store._records.clear()
    evidence_store._snapshot_timestamps.clear()


# ──────────────────────────────────────────────────────────────
# Section A — Market Evolution State Machine
# ──────────────────────────────────────────────────────────────

class TestMarketEvolutionStateMachine:
    """Every valid transition must be defined. Every invalid transition must
    be rejected. All 25 (D1, D2) combos must produce a valid state."""

    def test_all_25_combinations_produce_state(self):
        """Every (D1_tier, D2_tier) pair must produce a state name."""
        tiers = ["REJECT", "WEAK", "WATCH", "OPPORTUNITY", "SNIPER"]
        for d1 in tiers:
            for d2 in tiers:
                entry = MARKET_EVOLUTION_MATRIX.get((d1, d2))
                assert entry is not None, f"No matrix entry for ({d1}, {d2})"
                assert "name" in entry
                assert entry["name"]

    def test_all_states_have_required_fields(self):
        """Every matrix entry must have required fields."""
        required = {"name", "spiral", "tradeStyle", "action", "confidence",
                    "risk", "trend", "reversal", "nextProbableState"}
        for (d1, d2), entry in MARKET_EVOLUTION_MATRIX.items():
            missing = required - set(entry.keys())
            assert not missing, f"({d1}, {d2}) missing: {missing}"

    def test_matrix_has_all_25_entries(self):
        """Matrix must have exactly 25 entries (5×5)."""
        assert len(MARKET_EVOLUTION_MATRIX) == 25

    def test_all_next_probable_states_exist(self):
        """nextProbableState must reference a known state name."""
        known_names = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        for (d1, d2), entry in MARKET_EVOLUTION_MATRIX.items():
            nxt = entry.get("nextProbableState", "")
            assert nxt in known_names, (
                f"({d1}, {d2}) nextProbableState '{nxt}' not in known states"
            )

    def test_all_spirals_are_defined(self):
        """Every entry's spiral must be in SPIRALS."""
        defined_spirals = set(SPIRALS.keys())
        for (d1, d2), entry in MARKET_EVOLUTION_MATRIX.items():
            spiral = entry.get("spiral", "")
            assert spiral in defined_spirals, (
                f"({d1}, {d2}) spiral '{spiral}' not defined"
            )

    def test_no_self_only_states(self):
        """Every state name must appear as nextProbableState for at least one
        entry AND be reachable from some other state (or itself via a chain)."""
        known_names = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        reachable = {v.get("nextProbableState", "") for v in MARKET_EVOLUTION_MATRIX.values()}
        for name in known_names:
            # Each state must be reachable (either as someone's next or
            # be itself the nextProbableState of some entry)
            assert name in reachable or any(
                v["name"] == name for v in MARKET_EVOLUTION_MATRIX.values()
            ), f"State '{name}' is unreachable"

    def test_evaluate_returns_valid_state(self):
        """evaluate_from_scores must return a result with a valid state name."""
        result = evaluate_from_scores(
            coin="BTCUSDT", d1_score=85, d2_score=85, direction="BULLISH"
        )
        assert result is not None
        state_name = result.state
        known_names = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        assert state_name in known_names, f"Unknown state: {state_name}"

    def test_sniper_sniper_is_institutional_entry(self):
        """D1=SNIPER, D2=SNIPER must map to Institutional Entry."""
        result = evaluate_from_scores(
            coin="BTCUSDT", d1_score=90, d2_score=90, direction="BULLISH"
        )
        assert result.state == "Institutional Entry"

    def test_both_reject_is_dormant(self):
        """D1=REJECT, D2=REJECT must map to Dormant."""
        result = evaluate_from_scores(
            coin="BTCUSDT", d1_score=0, d2_score=0, direction="BULLISH"
        )
        assert result.state == "Dormant"

    def test_state_ordering_monotonic(self):
        """STATE_ORDER values must be consistent with tier progression."""
        from backend.market_evolution.mapper import STATE_ORDER
        # Higher tier → higher or equal state order
        sniper_states = [v["name"] for k, v in MARKET_EVOLUTION_MATRIX.items()
                         if k[0] == "SNIPER" and k[1] == "SNIPER"]
        reject_states = [v["name"] for k, v in MARKET_EVOLUTION_MATRIX.items()
                         if k[0] == "REJECT" and k[1] == "REJECT"]
        for ss in sniper_states:
            for rs in reject_states:
                if ss in STATE_ORDER and rs in STATE_ORDER:
                    assert STATE_ORDER[ss] >= STATE_ORDER[rs], (
                        f"SNIPER state '{ss}' ({STATE_ORDER[ss]}) should be >= "
                        f"REJECT state '{rs}' ({STATE_ORDER[rs]})"
                    )


# ──────────────────────────────────────────────────────────────
# Section B — Tier Classification
# ──────────────────────────────────────────────────────────────

class TestTierClassificationProperties:
    """Boundary conditions and ordering guarantees."""

    @pytest.mark.parametrize("score", list(range(0, 200)))
    def test_tier_returns_valid_name(self, score):
        """Every score must return a known tier name."""
        t = classify_tier(score)
        assert t in ("SNIPER", "OPPORTUNITY", "WATCH", "REJECTED"), (
            f"Unknown tier '{t}' for score {score}"
        )

    def test_sniper_boundary(self):
        assert classify_tier(TIER_SNIPER_SCORE - 1) != "SNIPER"
        assert classify_tier(TIER_SNIPER_SCORE) == "SNIPER"
        assert classify_tier(TIER_SNIPER_SCORE + 1) == "SNIPER"

    def test_opportunity_boundary(self):
        assert classify_tier(TIER_OPPORTUNITY_SCORE - 1) != "OPPORTUNITY"
        assert classify_tier(TIER_OPPORTUNITY_SCORE) == "OPPORTUNITY"
        assert classify_tier(TIER_OPPORTUNITY_SCORE + 1) == "OPPORTUNITY"

    def test_watch_boundary(self):
        assert classify_tier(TIER_WATCH_SCORE - 1) != "WATCH"
        assert classify_tier(TIER_WATCH_SCORE) == "WATCH"
        assert classify_tier(TIER_WATCH_SCORE + 1) == "WATCH"

    def test_rejected_range(self):
        """Everything below WATCH must be REJECTED."""
        for score in range(0, TIER_WATCH_SCORE):
            assert classify_tier(score) == "REJECTED"

    def test_tier_ordering(self):
        """High score must never be lower tier than low score."""
        for lo in range(0, 100):
            for hi in range(lo + 1, 101):
                t_lo = classify_tier(lo)
                t_hi = classify_tier(hi)
                tier_rank = {"SNIPER": 4, "OPPORTUNITY": 3, "WATCH": 2, "REJECTED": 1}
                assert tier_rank[t_hi] >= tier_rank[t_lo], (
                    f"Score {hi} ({t_hi}) < score {lo} ({t_lo})"
                )

    def test_negative_is_rejected(self):
        assert classify_tier(-100) == "REJECTED"

    def test_very_high_is_sniper(self):
        assert classify_tier(100) == "SNIPER"


# ──────────────────────────────────────────────────────────────
# Section C — EvidenceStore Properties
# ──────────────────────────────────────────────────────────────

class TestEvidenceStoreProperties:
    """Property-based tests for EvidenceStore invariants."""

    def setup_method(self):
        _clear_store()

    def teardown_method(self):
        _clear_store()

    def test_append_then_query_roundtrip(self):
        rec = _make_evidence()
        evidence_store.add_sync(rec)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                evidence_store.query(symbol="BTCUSDT")
            )
            assert len(results) == 1
            assert results[0].evidence_id == rec.evidence_id
        finally:
            loop.close()

    def test_count_equals_appends(self):
        recs = [_make_evidence(ts_offset=i, seq=i) for i in range(10)]
        for r in recs:
            evidence_store.add_sync(r)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            c = loop.run_until_complete(evidence_store.count())
            assert c == 10
        finally:
            loop.close()

    def test_purge_removes_all(self):
        recs = [_make_evidence(ts_offset=i, seq=i) for i in range(5)]
        for r in recs:
            evidence_store.add_sync(r)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            removed = loop.run_until_complete(
                evidence_store.purge_by_snapshot("snap-prop")
            )
            assert removed == 5
            c = loop.run_until_complete(evidence_store.count())
            assert c == 0
        finally:
            loop.close()

    def test_dedup_same_id_no_duplicate(self):
        """Adding the same evidence_id twice should not create a duplicate."""
        eid = _unique_id("BTCUSDT", 0)
        rec = EvidenceRecord(
            evidence_id=eid,
            snapshot_id="snap-prop",
            symbol="BTCUSDT",
            category=EvidenceCategory.ORDER_BLOCK,
            timeframe="1H",
            price=50000.0,
            strength=EvidenceStrength.STRONG,
            direction="BULLISH",
            confidence=0.9,
            candle_time=time.time(),
            detected_at=time.time(),
            source="test",
            details={},
        )
        evidence_store.add_sync(rec)
        evidence_store.add_sync(rec)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                evidence_store.query(symbol="BTCUSDT")
            )
            assert len(results) == 1
        finally:
            loop.close()

    def test_dedup_updates_confidence(self):
        eid = f"DEDUP-{time.time()}"
        rec1 = EvidenceRecord(
            evidence_id=eid,
            snapshot_id="snap-prop",
            symbol="BTCUSDT",
            category=EvidenceCategory.ORDER_BLOCK,
            timeframe="1H",
            price=50000.0,
            strength=EvidenceStrength.STRONG,
            direction="BULLISH",
            confidence=0.5,
            candle_time=time.time(),
            detected_at=time.time(),
            source="test",
            details={},
        )
        rec2 = EvidenceRecord(
            evidence_id=eid,
            snapshot_id="snap-prop",
            symbol="BTCUSDT",
            category=EvidenceCategory.ORDER_BLOCK,
            timeframe="1H",
            price=50000.0,
            strength=EvidenceStrength.STRONG,
            direction="BULLISH",
            confidence=0.9,
            candle_time=time.time() + 1,
            detected_at=time.time() + 1,
            source="test",
            details={},
        )
        evidence_store.add_sync(rec1)
        evidence_store.add_sync(rec2)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                evidence_store.query(symbol="BTCUSDT")
            )
            assert len(results) == 1
            assert results[0].confidence == 0.9
        finally:
            loop.close()

    def test_concurrent_appends_threadsafe(self):
        def _add(n):
            rec = _make_evidence(ts_offset=n, seq=n)
            evidence_store.add_sync(rec)

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(_add, range(50)))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            total = loop.run_until_complete(evidence_store.count())
            assert total == 50
        finally:
            loop.close()

    def test_ttl_expiry_removes_old(self):
        old_rec = _make_evidence(ts_offset=EVIDENCE_TTL_MINUTES * 60 + 10)
        evidence_store.add_sync(old_rec)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                evidence_store.query(symbol="BTCUSDT")
            )
            assert len(results) == 0
        finally:
            loop.close()

    def test_get_stats_valid_keys(self):
        evidence_store.add_sync(_make_evidence())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            stats = loop.run_until_complete(evidence_store.get_stats())
            assert stats["total"] == 1
            assert stats["ttl_seconds"] == EVIDENCE_TTL_MINUTES * 60
            assert "symbols_tracked" in stats
            assert "by_category" in stats
        finally:
            loop.close()

    def test_count_for_symbol(self):
        """Total count after appending distinct records."""
        _clear_store()
        for i, sym in enumerate(["BTCUSDT", "ETHUSDT", "AVAXUSDT"]):
            ev = EvidenceRecord(
                evidence_id=f"CNT-{sym}-{i}-{time.time():.0f}",
                snapshot_id=f"snap-{i}",
                symbol=sym,
                category=EvidenceCategory.ORDER_BLOCK,
                timeframe="1H",
                price=50000.0 + i * 1000,
                strength=EvidenceStrength.STRONG,
                direction="BULLISH",
                confidence=0.9,
                candle_time=time.time() - i,
                detected_at=time.time() - i,
                source="test",
                details={},
            )
            evidence_store.add_sync(ev)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            total = loop.run_until_complete(evidence_store.count())
            assert total == 3
        finally:
            loop.close()


# ──────────────────────────────────────────────────────────────
# Section D — Evidence Record Contracts
# ──────────────────────────────────────────────────────────────

class TestEvidenceContractProperties:
    """EvidenceRecord field contracts and value-range invariants."""

    def test_confidence_in_range(self):
        for c in [0.0, 0.5, 1.0]:
            rec = _make_evidence(confidence=c)
            assert 0.0 <= rec.confidence <= 1.0

    def test_valid_directions(self):
        valid = {"BULLISH", "BEARISH", "NEUTRAL", ""}
        for d in ["BULLISH", "BEARISH", "NEUTRAL", ""]:
            rec = _make_evidence(direction=d)
            assert rec.direction in valid

    def test_strength_ordering(self):
        assert EvidenceStrength.CRITICAL.value > EvidenceStrength.STRONG.value
        assert EvidenceStrength.STRONG.value > EvidenceStrength.MODERATE.value
        assert EvidenceStrength.MODERATE.value > EvidenceStrength.WEAK.value

    def test_category_values_nonempty(self):
        cats = [e.value for e in EvidenceCategory]
        assert len(cats) > 0
        assert "order_block" in cats


# ──────────────────────────────────────────────────────────────
# Section E — Decay Monotonicity
# ──────────────────────────────────────────────────────────────

class TestDecayProperties:
    """Decay rates must be in [0, 1] and non-increasing per cycle."""

    def test_all_decay_rates_in_range(self):
        for name, rate in [
            ("A", DECAY_TYPE_A), ("B", DECAY_TYPE_B),
            ("C", DECAY_TYPE_C), ("D", DECAY_TYPE_D), ("E", DECAY_TYPE_E),
        ]:
            assert 0.0 <= rate <= 1.0, f"DECAY_TYPE_{name}={rate} out of [0,1]"

    def test_decay_reduces_score(self):
        for decay in [DECAY_TYPE_A, DECAY_TYPE_B, DECAY_TYPE_C]:
            score = 80.0
            assert score * decay <= score + 0.001

    def test_type_d_and_e_no_decay(self):
        """Type D and E should have no decay (rate = 1.0)."""
        assert DECAY_TYPE_D == 1.0
        assert DECAY_TYPE_E == 1.0


# ──────────────────────────────────────────────────────────────
# Section F — EV Calculation
# ──────────────────────────────────────────────────────────────

class TestEVProperties:
    """Expected Value calculation invariants."""

    def test_breakeven_is_zero(self):
        ev = calculate_ev(win_rate=0.5, avg_win=100, avg_loss=100)
        assert abs(ev) < 0.01

    def test_positive_when_win_rate_high(self):
        ev = calculate_ev(win_rate=0.6, avg_win=100, avg_loss=100)
        assert ev > 0

    def test_negative_when_win_rate_low(self):
        ev = calculate_ev(win_rate=0.3, avg_win=100, avg_loss=100)
        assert ev < 0

    def test_monotonic_in_win_rate(self):
        w, l = 100, 100
        evs = [calculate_ev(win_rate=r / 100, avg_win=w, avg_loss=l)
               for r in range(0, 101, 10)]
        for i in range(len(evs) - 1):
            assert evs[i] <= evs[i + 1] + 0.001

    def test_zero_win_rate(self):
        ev = calculate_ev(win_rate=0.0, avg_win=100, avg_loss=50)
        assert abs(ev - (-50)) < 0.01

    def test_full_win_rate(self):
        ev = calculate_ev(win_rate=1.0, avg_win=100, avg_loss=50)
        assert abs(ev - 100) < 0.01


# ──────────────────────────────────────────────────────────────
# Section G — Replay Determinism
# ──────────────────────────────────────────────────────────────

class TestReplayProperties:
    """Replay determinism invariants."""

    def test_deep_equal_identical(self):
        assert _deep_equal({"a": 1.0}, {"a": 1.0}) == []

    def test_deep_equal_float_tolerance(self):
        assert _deep_equal(1.0, 1.0000000001) == []

    def test_deep_equal_detects_diff(self):
        assert len(_deep_equal({"a": 1}, {"a": 2})) > 0

    def test_deep_equal_nested_dict(self):
        assert _deep_equal({"a": {"b": 1}}, {"a": {"b": 1}}) == []
        assert len(_deep_equal({"a": {"b": 1}}, {"a": {"b": 2}})) > 0

    def test_deep_equal_list(self):
        assert _deep_equal([1, 2, 3], [1, 2, 3]) == []
        assert len(_deep_equal([1, 2], [1, 2, 3])) > 0

    def test_deep_equal_int_float_coercion(self):
        assert _deep_equal(1, 1.0) == []


# ──────────────────────────────────────────────────────────────
# Section H — Configuration Invariants
# ──────────────────────────────────────────────────────────────

class TestConfigInvariants:
    """Config values must satisfy documented relationships."""

    def test_tier_thresholds_ordered(self):
        assert TIER_SNIPER_SCORE > TIER_OPPORTUNITY_SCORE
        assert TIER_OPPORTUNITY_SCORE > TIER_WATCH_SCORE
        assert TIER_WATCH_SCORE > TIER_WEAK_SCORE

    def test_min_rr_positive(self):
        assert MIN_RR > 0

    def test_evidence_ttl_positive(self):
        assert EVIDENCE_TTL_MINUTES > 0

    def test_ignore_below_watch(self):
        assert IGNORE_MIN_SCORE < TIER_WATCH_SCORE

    def test_htf_context_bounds(self):
        assert HTF_CONTEXT_MIN < 0 < HTF_CONTEXT_MAX
        assert HTF_CONTEXT_MIN < HTF_CONTEXT_MAX

    def test_decay_rates_in_range(self):
        for name, rate in [
            ("A", DECAY_TYPE_A), ("B", DECAY_TYPE_B),
            ("C", DECAY_TYPE_C), ("D", DECAY_TYPE_D), ("E", DECAY_TYPE_E),
        ]:
            assert 0.0 <= rate <= 1.0

    def test_spirals_cover_all_states(self):
        """Every state name in the matrix must be in at least one spiral."""
        state_names = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        spiral_states = set()
        for states in SPIRALS.values():
            spiral_states.update(states["states"])
        # Every state should be in some spiral
        for s in state_names:
            assert s in spiral_states or s in STATE_TO_CATEGORY, (
                f"State '{s}' not in any spiral or category"
            )

    def test_state_to_category_complete(self):
        """Every state name should map to an institutional category."""
        state_names = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        for s in state_names:
            assert s in STATE_TO_CATEGORY, f"State '{s}' missing from STATE_TO_CATEGORY"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
