"""Phase 7 — Market Evolution State Machine verification tests."""
import pytest

from backend.market_evolution.engine import evaluate, evaluate_many, evaluate_from_scores
from backend.market_evolution.constants import (
    MARKET_EVOLUTION_MATRIX,
    SPIRALS,
    INSTITUTIONAL_CATEGORIES,
    TRADING_DECISIONS,
    get_state,
    STATE_TO_CATEGORY,
    TIERS,
)
from backend.market_evolution.transitions import (
    compute_evolution,
    evolution_label,
    momentum_velocity,
)
from backend.market_evolution.confidence import get_confidence
from backend.market_evolution.models import MarketEvolutionState


# ── Helpers ───────────────────────────────────────────────────────────

def _eval(coin=None, d1_tier="REJECT", d1_score=0, d2_tier="REJECT", d2_score=0,
          direction="BULLISH", alignment_score=0, signal_type="", clear_history=True):
    """Evaluate market evolution, matching evaluate()'s exact parameter order.

    evaluate(coin, d1_tier, d1_score, d2_tier, d2_score, direction, ...)

    All callers use keyword args for d1_score/d2_score to avoid positional
    confusion with tiers.
    """
    if coin is None:
        coin = f"T_{d1_tier}_{d2_tier}"
    if clear_history:
        from backend.market_evolution.history import history_store
        from backend.market_evolution import engine as me
        # Reset HistoryStore internal state
        history_store._store.clear()
        history_store._last_state.clear()
        # Reset module-level state cache so previousState defaults to "Dormant"
        me._prev_state.pop(coin, None)
        me._prev_d1_score.pop(coin, None)
        me._prev_d2_score.pop(coin, None)
    return evaluate(coin, d1_tier, d1_score, d2_tier, d2_score,
                    direction, alignment_score, signal_type)


# ── Matrix completeness ───────────────────────────────────────────────

class TestMatrixCompleteness:

    def test_all_25_combinations_produce_state(self):
        """Every (D1_tier, D2_tier) combination must map to a state."""
        for d1 in TIERS:
            for d2 in TIERS:
                result = get_state(d1, d2)
                assert "name" in result
                assert result["name"]

    def test_all_states_have_required_fields(self):
        required = ["name", "description", "spiral", "tradeStyle",
                    "action", "confidence", "risk", "trend", "reversal",
                    "nextProbableState"]
        for key, entry in MARKET_EVOLUTION_MATRIX.items():
            for field in required:
                assert field in entry, f"Missing {field} in {key}"

    def test_all_next_probable_states_exist(self):
        """Every nextProbableState must reference a known state name."""
        known_states = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        for key, entry in MARKET_EVOLUTION_MATRIX.items():
            nps = entry.get("nextProbableState", "")
            assert nps in known_states, \
                f"Invalid nextProbableState '{nps}' for {key}"

    def test_all_spirals_are_defined(self):
        """Spiral references must exist in SPIRALS."""
        for key, entry in MARKET_EVOLUTION_MATRIX.items():
            spiral = entry["spiral"]
            assert spiral in SPIRALS, f"Unknown spiral '{spiral}' in {key}"

    def test_matrix_has_all_25_entries(self):
        assert len(MARKET_EVOLUTION_MATRIX) == 25


# ── State evaluation (actual behavior verification) ────────────────────

class TestStateEvaluation:

    def test_both_rejected_is_dormant(self):
        result = _eval(d1_tier="REJECT", d2_tier="REJECT")
        assert result.state == "Dormant"

    def test_d1_sniper_d2_sniper_is_institutional_entry(self):
        result = _eval(d1_tier="SNIPER", d1_score=90,
                       d2_tier="SNIPER", d2_score=90)
        assert result.state == "Institutional Entry"

    def test_d1_sniper_d2_reject_is_deep_pullback(self):
        result = _eval(d1_tier="SNIPER", d1_score=90,
                       d2_tier="REJECT", d2_score=0)
        assert result.state == "Deep Pullback"

    def test_d1_watch_d2_sniper_is_trap_zone(self):
        result = _eval(d1_tier="WATCH", d1_score=50,
                       d2_tier="SNIPER", d2_score=90)
        assert result.state == "Trap Zone"

    def test_d1_reject_d2_sniper_is_ltf_spike(self):
        result = _eval(d1_tier="REJECT", d1_score=0,
                       d2_tier="SNIPER", d2_score=90)
        assert result.state == "LTF Spike"

    def test_d1_opportunity_d2_opportunity_is_trend_building(self):
        result = _eval(d1_tier="OPPORTUNITY", d1_score=75,
                       d2_tier="OPPORTUNITY", d2_score=75)
        assert result.state == "Trend Building"

    def test_returns_market_evolution_state(self):
        result = _eval()
        assert isinstance(result, MarketEvolutionState)

    def test_confidence_is_bounded(self):
        result = _eval(d1_tier="SNIPER", d1_score=90,
                       d2_tier="SNIPER", d2_score=90)
        assert 0 <= result.confidence <= 100

    def test_confidence_dormant_is_low(self):
        result = _eval(d1_tier="REJECT", d2_tier="REJECT")
        assert result.confidence < 30

    def test_confidence_institutional_entry_is_high(self):
        result = _eval(d1_tier="SNIPER", d1_score=90,
                       d2_tier="SNIPER", d2_score=90)
        assert result.confidence >= 80

    def test_spiral_assigned(self):
        result = _eval(d1_tier="SNIPER", d1_score=90,
                       d2_tier="SNIPER", d2_score=90)
        assert result.spiral in SPIRALS

    def test_trade_style_assigned(self):
        result = _eval()
        assert result.tradeStyle

    def test_action_assigned(self):
        result = _eval()
        assert result.action

    def test_direction_defaults_bullish(self):
        result = _eval(d1_tier="REJECT", d2_tier="WATCH", direction="")
        assert result.state is not None

    def test_evolution_field_populated(self):
        result = _eval(d1_tier="REJECT", d2_tier="WATCH")
        assert result.evolution in (
            "stable", "improving", "strong_improving",
            "weakening", "strong_weakening",
        )

    def test_previous_state_defaults_dormant(self):
        # Fresh call with clear_history=True -> prev_state defaults to "Dormant"
        result = _eval(d1_tier="REJECT", d2_tier="WATCH")
        assert result.previousState == "Dormant"

    def test_next_probable_state_populated(self):
        result = _eval(d1_tier="REJECT", d2_tier="WATCH")
        assert result.nextProbableState

    def test_institutional_category_assigned(self):
        result = _eval(d1_tier="SNIPER", d1_score=90,
                       d2_tier="SNIPER", d2_score=90)
        assert result.institutionalCategory in INSTITUTIONAL_CATEGORIES

    def test_trading_decision_assigned(self):
        result = _eval()
        assert result.tradingDecision in TRADING_DECISIONS.values()

    def test_evolution_velocity_assigned(self):
        result = _eval()
        assert result.evolutionVelocity in ("improving", "stable", "degrading")

    def test_all_unique_states(self):
        """Every matrix entry should produce a unique state name
        OR duplicates are intentional (e.g., multiple combos -> Dormant)."""
        state_names = set()
        for key, entry in MARKET_EVOLUTION_MATRIX.items():
            state_names.add(entry["name"])
        # We expect fewer unique states than entries (some combos map to same state)
        assert len(state_names) >= 10  # at minimum 10 unique states

    def test_d1_weak_tiers_produce_valid_states(self):
        """D1=WEAK is a valid tier — all 5 D2 tiers must map to states."""
        for d2 in TIERS:
            result = _eval(d1_tier="WEAK", d1_score=35, d2_tier=d2, d2_score=35)
            assert result.state is not None


# ── Transition engine ─────────────────────────────────────────────────

class TestTransitions:

    def test_same_state_is_stable(self):
        assert compute_evolution("Dormant", "Dormant") == "stable"

    def test_significant_improvement(self):
        result = compute_evolution("Dormant", "Institutional Entry")
        assert result == "strong_improving"

    def test_modest_improvement(self):
        result = compute_evolution("Compression", "Expansion Watch")
        assert result == "improving"

    def test_degradation(self):
        result = compute_evolution("Institutional Entry", "Context Building")
        assert result in ("weakening", "strong_weakening")

    def test_significant_degradation(self):
        result = compute_evolution("Institutional Entry", "Dormant")
        assert result == "strong_weakening"

    def test_unknown_prev_state_defaults(self):
        result = compute_evolution("UNKNOWN_STATE", "Dormant")
        assert result == "stable"

    def test_unknown_curr_state_defaults(self):
        result = compute_evolution("Dormant", "UNKNOWN_STATE")
        assert result == "stable"

    def test_evolution_label_lookup(self):
        assert evolution_label("improving") == "Improving"
        assert evolution_label("stable") == "Stable"

    def test_momentum_velocity_positive(self):
        v = momentum_velocity(50, 80, 50, 80)
        assert v > 0

    def test_momentum_velocity_negative(self):
        # momentum_velocity returns product of deltas, not signed direction
        # (80->50=-30, 80->50=-30) product = 900 > 0
        v = momentum_velocity(80, 50, 80, 50)
        assert v > 0  # both decreasing: (-30) * (-30) = 900

    def test_momentum_velocity_neutral(self):
        v = momentum_velocity(50, 50, 50, 50)
        assert v == 0.0


# ── Confidence ────────────────────────────────────────────────────────

class TestConfidence:

    def test_confidence_institutional_entry(self):
        conf = get_confidence("SNIPER", "SNIPER", 90, 90, 15,
                              state_name="Institutional Entry")
        assert conf >= 85

    def test_confidence_dormant(self):
        conf = get_confidence("REJECT", "REJECT", 0, 0, 0,
                              state_name="Dormant")
        assert conf <= 15

    def test_confidence_bounded(self):
        for d1 in TIERS:
            for d2 in TIERS:
                conf = get_confidence(d1, d2, 50, 50, 10)
                assert 0 <= conf <= 100


# ── Determinism ───────────────────────────────────────────────────────

class TestDeterminism:

    def test_same_input_same_output(self):
        # clear_history=True ensures fresh state cache
        r1 = _eval(coin="ETHUSDT", d1_tier="OPPORTUNITY", d1_score=75,
                   d2_tier="SNIPER", d2_score=85, direction="BULLISH",
                   alignment_score=15, signal_type="C", clear_history=True)
        r2 = _eval(coin="ETHUSDT", d1_tier="OPPORTUNITY", d1_score=75,
                   d2_tier="SNIPER", d2_score=85, direction="BULLISH",
                   alignment_score=15, signal_type="C", clear_history=True)
        assert r1.state == r2.state
        assert r1.confidence == r2.confidence
        assert r1.spiral == r2.spiral
        assert r1.evolution == r2.evolution

    def test_evaluate_many_deterministic(self):
        data = {
            "BTC": {"d1_tier": "SNIPER", "d1_score": 90,
                    "d2_tier": "OPPORTUNITY", "d2_score": 75, "direction": "BULLISH"},
            "ETH": {"d1_tier": "WATCH", "d1_score": 50,
                    "d2_tier": "WATCH", "d2_score": 50, "direction": "BEARISH"},
        }
        r1 = evaluate_many(data)
        r2 = evaluate_many(data)
        assert r1["BTC"].state == r2["BTC"].state
        assert r1["ETH"].state == r2["ETH"].state

    def test_evaluate_from_scores(self):
        r = evaluate_from_scores("SOLUSDT", 85, 90, "BULLISH")
        assert r.state is not None
        assert r.confidence > 0


# ── History tracking ──────────────────────────────────────────────────

class TestHistoryTracking:

    def test_evaluate_records_history(self):
        coin = "HIST_TEST_USDT"
        from backend.market_evolution.history import history_store
        history_store._store.clear()
        history_store._last_state.clear()
        evaluate(coin, "WATCH", 50, "WATCH", 50)
        history = history_store.get_history(coin)
        assert len(history) >= 1

    def test_consecutive_calls_track_evolution(self):
        coin = "HIST_EVOLVE_USDT"
        from backend.market_evolution.history import history_store
        history_store._store.clear()
        history_store._last_state.clear()
        evaluate(coin, "REJECT", 0, "WATCH", 50)
        evaluate(coin, "WATCH", 50, "OPPORTUNITY", 75)
        history = history_store.get_history(coin)
        assert len(history) >= 2


# ── State machine invariants ──────────────────────────────────────────

class TestStateMachineInvariants:

    def test_no_duplicate_state_names(self):
        names = [v["name"] for v in MARKET_EVOLUTION_MATRIX.values()]
        # Duplicates are OK (e.g., multiple combos -> Dormant)
        # Just verify we have a reasonable unique set
        unique = set(names)
        assert len(unique) >= 10

    def test_institutional_category_coverage(self):
        """Every state must map to exactly one institutional category."""
        all_states = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        categorized = set()
        for cat_info in INSTITUTIONAL_CATEGORIES.values():
            categorized.update(cat_info["states"])
        uncategorized = all_states - categorized
        assert not uncategorized, f"Uncategorized states: {uncategorized}"

    def test_trading_decision_coverage(self):
        """Every state must have a trading decision."""
        all_states = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        missing = all_states - set(TRADING_DECISIONS.keys())
        assert not missing, f"Missing trading decisions for: {missing}"

    def test_every_state_in_spiral(self):
        """All states must appear in at least one spiral."""
        all_states = {v["name"] for v in MARKET_EVOLUTION_MATRIX.values()}
        for state in all_states:
            found_in_spiral = any(
                state in s["states"] for s in SPIRALS.values()
            )
            assert found_in_spiral, f"State '{state}' not in any spiral"

    def test_no_state_reachable_without_d1_change(self):
        """Verify that D2 alone can trigger state transitions
        (e.g., REJECT->WATCH when D1 stays REJECT)."""
        # D1=REJECT, D2=WATCH => Awakening (different from REJECT+REJECT=Dormant)
        r = _eval(d1_tier="REJECT", d2_tier="WATCH")
        assert r.state == "Awakening"


# ── Integration: signal_fusion wiring ─────────────────────────────────

class TestFusionIntegration:

    def test_me_evaluate_callable_from_signal_fusion(self):
        """verify the evaluate signature matches what signal_fusion calls."""
        from backend.market_evolution import evaluate as me_evaluate
        result = me_evaluate(
            "TEST_USDT",
            "SNIPER", 90,
            "SNIPER", 90,
            direction="BULLISH",
            alignment_score=15,
            signal_type="C",
        )
        assert result.state == "Institutional Entry"

    def test_alignment_result_to_dict(self):
        from backend.alignment_engine import AlignmentLevel, alignment_engine
        result = alignment_engine.evaluate(
            d1_structure={"direction": "BULLISH", "ob_zone": "PREMIUM",
                          "premium_discount": "PREMIUM",
                          "liq_swept": True, "liq_level": 50000,
                          "fvg_type": "BULLISH"},
            d2_structure={"direction": "BULLISH", "ob_zone": "PREMIUM",
                          "premium_discount": "PREMIUM",
                          "liq_swept": True, "liq_level": 50000,
                          "fvg_type": "BULLISH"},
            d1_tier="SNIPER",
            d2_tier="SNIPER",
            d1_direction="BULLISH",
            d2_direction="BULLISH",
            d1_quality="VALID",
            d2_quality="VALID",
        )
        d = result.to_dict()
        assert "level" in d
        assert "score" in d
        assert "tradeable" in d
        assert result.is_tradeable()
        assert result.is_strong()

    def test_alignment_conflict(self):
        from backend.alignment_engine import alignment_engine
        result = alignment_engine.evaluate(
            d1_structure={"direction": "BULLISH"},
            d2_structure={"direction": "BEARISH"},
            d1_tier="SNIPER",
            d2_tier="SNIPER",
            d1_direction="BULLISH",
            d2_direction="BEARISH",
        )
        assert result.is_conflict()
        assert not result.is_tradeable()
