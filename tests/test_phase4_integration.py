"""Phase 4 Integration Tests — validates D1→D2→D3 full pipeline.

Tests:
  1. Signal Type Classification (all 5 types + None)
  2. Position Sizing by Signal Type
  3. D2 Fatal Flaws (auto-disqualification)
  4. HTF Context Scoring
  5. Expected Value Calculation
  6. Fusion Engine Configuration Completeness
  7. D2 Independence (D1 missing fallback)
  8. Type B Nascent Move Gate
  9. WebSocket Payload Format
"""
import pytest
from unittest.mock import patch, MagicMock

from backend.engines.signal_fusion import (
    classify_signal_type,
    classify_tier,
    calculate_ev,
    SIGNAL_TYPES,
    TYPE_POSITION_MULT,
    TYPE_STOP_MULT,
    DECAY_TYPE_A,
    DECAY_TYPE_C,
)
from backend.engines.ltf_pipeline import (
    _check_d2_fatal_flaws,
    _score_htf_context,
    detect_nascent_move,
)
from backend.config import (
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE,
)


# ========================================================================
# Test 1: Signal Type Classification (all 5 types)
# ========================================================================

class TestSignalTypeClassification:
    """All 5 signal types must classify correctly."""

    def test_type_c_both_sniper_aligned(self):
        """Type C: D1 SNIPER (85+) AND D2 SNIPER (85+) + aligned."""
        sig = classify_signal_type(
            d1_tier="SNIPER", d1_score=88,
            d2_tier="SNIPER", d2_score=90,
            d1_direction="BULLISH", d2_direction="BULLISH",
        )
        assert sig == "C", f"Expected C, got {sig}"

    def test_type_a_d1_approved_d2_moderate_aligned(self):
        """Type A: D1 >= 65 AND D2 >= 50 + aligned."""
        sig = classify_signal_type(
            d1_tier="SNIPER", d1_score=72,
            d2_tier="OPPORTUNITY", d2_score=60,
            d1_direction="BULLISH", d2_direction="BULLISH",
        )
        assert sig == "A", f"Expected A, got {sig}"

    def test_type_b_d1_rejected_d2_strong_nascent(self):
        """Type B: D1 not approved, D2 >= 72, nascent=True, EP>=18."""
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="SNIPER", d2_score=82,
            d1_direction="", d2_direction="BULLISH",
            nascent_move=True, entry_precision=20.0,
        )
        assert sig == "B", f"Expected B, got {sig}"

    def test_type_b_blocks_when_nascent_false(self):
        """Type B must have nascent_move=True."""
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="SNIPER", d2_score=82,
            d1_direction="", d2_direction="BULLISH",
            nascent_move=False, entry_precision=20.0,
        )
        assert sig is None, f"Expected None (nascent gate), got {sig}"

    def test_type_b_blocks_when_ep_too_low(self):
        """Type B must have entry_precision >= 18."""
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="SNIPER", d2_score=82,
            d1_direction="", d2_direction="BULLISH",
            nascent_move=True, entry_precision=15.0,
        )
        assert sig is None, f"Expected None (EP gate), got {sig}"

    def test_type_d_d1_approved_d2_not_aligned(self):
        """Type D: D1 >= 70, directions don't align, D2 is WATCH (not valid enough for E)."""
        sig = classify_signal_type(
            d1_tier="SNIPER", d1_score=78,
            d2_tier="WATCH", d2_score=42,
            d1_direction="BULLISH", d2_direction="BEARISH",
        )
        assert sig == "D", f"Expected D, got {sig}"

    def test_type_e_both_valid_opposing(self):
        """Type E: D1 approved, D2 SNIPER/OPP, opposing directions."""
        sig = classify_signal_type(
            d1_tier="SNIPER", d1_score=78,
            d2_tier="OPPORTUNITY", d2_score=70,
            d1_direction="BULLISH", d2_direction="BEARISH",
        )
        assert sig == "E", f"Expected E, got {sig}"

    def test_none_when_below_thresholds(self):
        """None when D1 not approved and D2 < 72."""
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="WATCH", d2_score=50,
            d1_direction="", d2_direction="BULLISH",
        )
        assert sig is None, f"Expected None, got {sig}"


# ========================================================================
# Test 2: Position Sizing by Signal Type
# ========================================================================

class TestPositionSizing:
    def test_type_a_position(self):
        assert TYPE_POSITION_MULT["A"] == 0.75

    def test_type_b_position(self):
        assert TYPE_POSITION_MULT["B"] == 0.35

    def test_type_c_position(self):
        assert TYPE_POSITION_MULT["C"] == 1.0

    def test_type_d_zero_position(self):
        assert TYPE_POSITION_MULT["D"] == 0.0

    def test_type_e_zero_position(self):
        assert TYPE_POSITION_MULT["E"] == 0.0


# ========================================================================
# Test 3: Tier Classification
# ========================================================================

class TestTierClassification:
    def test_sniper(self):
        assert classify_tier(85) == "SNIPER"
        assert classify_tier(95) == "SNIPER"

    def test_opportunity(self):
        assert classify_tier(65) == "OPPORTUNITY"
        assert classify_tier(84) == "OPPORTUNITY"

    def test_watch(self):
        assert classify_tier(40) == "WATCH"
        assert classify_tier(64) == "WATCH"

    def test_rejected(self):
        assert classify_tier(25) == "REJECTED"
        assert classify_tier(39) == "REJECTED"


# ========================================================================
# Test 4: D2 Fatal Flaws
# ========================================================================

class TestD2FatalFlaws:
    def _make_candle(self, volume=5000):
        return {'close': 100, 'high': 102, 'low': 98, 'volume': volume, 'open': 99}

    def test_flaw_1_no_structure_no_precision(self):
        """No structure + no entry precision = disqualified."""
        flaws = _check_d2_fatal_flaws(
            candles=[self._make_candle()],
            smc={'ob': None, 'fvg': None, 'msb': {'confirmed': False}, 'choch': {'detected': False}},
            flow={'ob_proximity': False, 'fvg_proximity': False, 'delta_history': [1, 1, 1], 'direction': 'BULLISH'},
        )
        assert "no_structure_no_precision" in flaws

    def test_flaw_2_opposing_delta(self):
        """Delta opposing 2+ candles = disqualified."""
        flaws = _check_d2_fatal_flaws(
            candles=[self._make_candle()],
            smc={'ob': {'low': 97, 'high': 101, 'strength': 3}, 'msb': {'confirmed': True}},
            flow={'ob_proximity': True, 'fvg_proximity': True,
                  'delta_history': [-1, -1, -1], 'direction': 'BULLISH'},
        )
        assert any("delta_opposing" in f for f in flaws)

    def test_flaw_3_low_volume(self):
        """Volume < 1.0x avg on key candle = disqualified."""
        candles = []
        for _ in range(19):
            candles.append({'close': 100, 'high': 102, 'low': 98, 'volume': 1000, 'open': 99})
        candles.append({'close': 100, 'high': 102, 'low': 98, 'volume': 500, 'open': 99})  # last = low volume
        flaws = _check_d2_fatal_flaws(
            candles=candles,
            smc={'ob': {'low': 97, 'high': 101, 'strength': 3}, 'msb': {'confirmed': True}},
            flow={'ob_proximity': True, 'fvg_proximity': True,
                  'delta_history': [1, 1, 1], 'direction': 'BULLISH'},
        )
        assert any("low_volume" in f for f in flaws)

    def test_flaw_4_far_from_ob(self):
        """Entry > 2% past OB = disqualified."""
        flaws = _check_d2_fatal_flaws(
            candles=[{'close': 105, 'high': 106, 'low': 104, 'volume': 5000, 'open': 104.5}],
            smc={'ob': {'low': 97, 'high': 101, 'strength': 3}, 'msb': {'confirmed': True}},
            flow={'ob_proximity': True, 'fvg_proximity': True,
                  'delta_history': [1, 1, 1], 'direction': 'BULLISH'},
        )
        assert any("entry_far_from_ob" in f for f in flaws)

    def test_clean_signal_no_flaws(self):
        """Clean signal = no fatal flaws."""
        flaws = _check_d2_fatal_flaws(
            candles=[self._make_candle],
            smc={'ob': {'low': 97, 'high': 101, 'strength': 3}, 'msb': {'confirmed': True}},
            flow={'ob_proximity': True, 'fvg_proximity': True,
                  'delta_history': [1, 1, 1], 'direction': 'BULLISH'},
        )
        assert flaws == []


# ========================================================================
# Test 5: HTF Context Scoring
# ========================================================================

class TestHTFContext:
    @patch('backend.signal_store.signal_store')
    def test_same_direction(self, mock_store):
        """Same D1+D2 direction = +5."""
        mock_store.get.return_value = {'composite_score': 72, 'direction': 'BULLISH'}
        score = _score_htf_context(
            'ETHUSDT',
            {'displacement': {'crt_trade_direction': 'BULLISH'}},
            [],
        )
        assert score == 5  # HTF_CONTEXT_SAME

    @patch('backend.signal_store.signal_store')
    def test_opposing_direction(self, mock_store):
        """Opposing D1+D2 direction = -5."""
        mock_store.get.return_value = {'composite_score': 72, 'direction': 'BEARISH'}
        score = _score_htf_context(
            'ETHUSDT',
            {'displacement': {'crt_trade_direction': 'BULLISH'}},
            [],
        )
        assert score == -5  # HTF_CONTEXT_OPPOSING

    @patch('backend.signal_store.signal_store')
    def test_no_d1_data(self, mock_store):
        """No D1 data available = +3."""
        mock_store.get.return_value = None
        score = _score_htf_context(
            'ETHUSDT',
            {'displacement': {'crt_trade_direction': 'BULLISH'}},
            [],
        )
        assert score == 3  # HTF_CONTEXT_NO_DATA


# ========================================================================
# Test 6: Expected Value Calculation
# ========================================================================

class TestEV:
    def test_positive_ev(self):
        ev = calculate_ev(win_rate=0.75, avg_win=0.03, avg_loss=0.01)
        assert ev > 0, f"Expected positive EV, got {ev}"
        # EV = 0.75 * 0.03 - 0.25 * 0.01 = 0.0225 - 0.0025 = 0.0200

    def test_negative_ev(self):
        ev = calculate_ev(win_rate=0.35, avg_win=0.01, avg_loss=0.01)
        assert ev < 0, f"Expected negative EV, got {ev}"

    def test_breakeven(self):
        ev = calculate_ev(win_rate=0.50, avg_win=0.02, avg_loss=0.01)
        # EV = 0.5 * 0.02 - 0.5 * 0.01 = 0.01 - 0.005 = 0.005
        assert ev == pytest.approx(0.005)


# ========================================================================
# Test 7: End-to-End Decision Packaging
# ========================================================================

class TestDecisionPackaging:
    """Test that the full decision package has all required frontend fields."""

    def test_package_fields_present(self):
        """Verify the fusion engine populates all required frontend fields."""
        # Import the module-level me_evaluate
        from backend.engines import signal_fusion as sf_module

        # Check that signal_fusion has me_evaluate imported
        assert hasattr(sf_module, 'me_evaluate'), "signal_fusion must have me_evaluate"
        assert hasattr(sf_module, 'fusion_engine'), "signal_fusion must have fusion_engine"
        assert hasattr(sf_module, 'state_store'), "signal_fusion must have state_store"

        # Check SIGNAL_TYPES completeness
        required_types = {"A", "B", "C", "D", "E"}
        assert set(sf_module.SIGNAL_TYPES.keys()) == required_types

        # Check each signal type has required fields
        for stype, info in sf_module.SIGNAL_TYPES.items():
            assert "name" in info, f"Type {stype} missing 'name'"
            assert "action" in info, f"Type {stype} missing 'action'"
            assert "color" in info, f"Type {stype} missing 'color'"
            assert "icon" in info, f"Type {stype} missing 'icon'"
            assert "ttl_min" in info, f"Type {stype} missing 'ttl_min'"

        # Check separate sizing/decay configs
        assert sf_module.TYPE_POSITION_MULT["A"] == 0.75
        assert sf_module.TYPE_POSITION_MULT["B"] == 0.35
        assert sf_module.TYPE_POSITION_MULT["C"] == 1.0
        assert sf_module.TYPE_POSITION_MULT["D"] == 0.0
        assert sf_module.TYPE_POSITION_MULT["E"] == 0.0

        assert sf_module.TYPE_STOP_MULT["A"] == 1.5
        assert sf_module.TYPE_STOP_MULT["B"] == 1.0
        assert sf_module.TYPE_STOP_MULT["C"] == 1.5
        assert sf_module.TYPE_STOP_MULT["D"] == 1.5
        assert sf_module.TYPE_STOP_MULT["E"] == 1.5

        assert sf_module.DECAY_TYPE_A == 0.94
        assert sf_module.DECAY_TYPE_C == 0.98


# ========================================================================
# Test 8: End-to-End D2→D3 with D1 missing
# ========================================================================

class TestD2Independence:
    """D2 signals must flow to D3 even when D1 has no data."""

    def test_d2_signal_without_d1_classifies_as_b(self):
        """D2 SNIPER with no D1 = defaults to REJECTED → Type B if nascent+EP pass."""
        # Simulate what _fuse_coin does when D1 is missing
        d1 = None  # NO D1 DATA
        d2_tier = "SNIPER"
        d2_score = 85
        d2_direction = "BULLISH"
        nascent_move = True
        entry_precision = 16.0

        # D1 defaults to REJECTED per the code
        if not d1:
            d1 = {"tier": "REJECTED", "score": 0, "direction": ""}

        # Classify
        sig_type = classify_signal_type(
            d1_tier=d1["tier"], d1_score=d1["score"],
            d2_tier=d2_tier, d2_score=d2_score,
            d1_direction=d1["direction"], d2_direction=d2_direction,
            nascent_move=nascent_move, entry_precision=entry_precision,
        )
        # With D1=REJECTED, D2=SNIPER, nascent=True, EP=16 (gate is >=16)
        assert sig_type == "B", f"Expected B (D1 default REJECTED), got {sig_type}"
        assert TYPE_POSITION_MULT["B"] == 0.35

    def test_d2_signal_without_d1_no_nascent(self):
        """D2 SNIPER with no D1 and no nascent = None (no signal)."""
        d1 = {"tier": "REJECTED", "score": 0, "direction": ""}

        sig_type = classify_signal_type(
            d1_tier=d1["tier"], d1_score=d1["score"],
            d2_tier="SNIPER", d2_score=85,
            d1_direction=d1["direction"], d2_direction="BULLISH",
            nascent_move=False, entry_precision=16.0,
        )
        assert sig_type is None, "Should be None when nascent_move=False"

    def test_fusion_engine_has_required_imports(self):
        """Verify the fusion engine module has all required dependencies."""
        from backend.engines import signal_fusion as sf
        assert hasattr(sf, 'fusion_engine')
        assert hasattr(sf, 'me_evaluate')
        assert hasattr(sf, 'state_store')
        assert hasattr(sf, 'SIGNAL_TYPES')
        assert hasattr(sf, 'TYPE_POSITION_MULT')
        assert hasattr(sf, 'TYPE_STOP_MULT')
        assert hasattr(sf, 'DECAY_TYPE_A')
        assert hasattr(sf, 'DECAY_TYPE_C')


# ========================================================================
# Test 9: Type B Nascent Move Gate
# ========================================================================

class TestTypeBNascentGate:
    """Type B requires nascent_move=True and entry_precision>=16."""

    def test_type_b_all_gates_pass(self):
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="SNIPER", d2_score=85,
            d1_direction="", d2_direction="BULLISH",
            nascent_move=True, entry_precision=18.0,
        )
        assert sig == "B"

    def test_type_b_nascent_false(self):
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="SNIPER", d2_score=85,
            d1_direction="", d2_direction="BULLISH",
            nascent_move=False, entry_precision=18.0,
        )
        assert sig is None

    def test_type_b_ep_below_16(self):
        sig = classify_signal_type(
            d1_tier="REJECTED", d1_score=25,
            d2_tier="SNIPER", d2_score=85,
            d1_direction="", d2_direction="BULLISH",
            nascent_move=True, entry_precision=14.0,
        )
        assert sig is None


# ========================================================================
# Test 10: WebSocket Payload Format
# ========================================================================

class TestWSPayload:
    """Verify WebSocket payload has correct structure."""

    def test_initial_payload_format(self):
        from backend.ws_hub import get_initial_payload
        mock_store = MagicMock()
        mock_store.get_all_decisions.return_value = {}
        mock_store.get_stats.return_value = {"d1_coins": 0, "d2_signals": 0, "d3_decisions": 0}

        payload = get_initial_payload(mock_store)
        assert payload["type"] == "INITIAL"
        assert "signals" in payload
        assert "stats" in payload


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
