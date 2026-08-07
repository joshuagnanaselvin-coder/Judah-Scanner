"""Market Evolution - Mapper.

Resolves (D1, D2) tier strings -> state definition from the 16-state matrix.
No hardcoded if/else - driven entirely by constants.py.

V5.2: STATE_ORDER provides monotonic progression index for evolution velocity.
"""
from .constants import (
    get_state as _matrix_lookup,
    STATE_TO_CATEGORY,
    TRADING_DECISIONS,
    INSTITUTIONAL_CATEGORIES,
)

# Monotonic ordering of states from weakest to strongest (used for velocity).
STATE_ORDER = {
    "Dormant":           0,
    "Awakening":         1,
    "Context Building":  2,
    "Compression":       3,
    "Expansion Watch":   4,
    "Expansion Setup":   5,
    "Trend Building":    6,
    "Trend Confirmation":7,
    "Institutional Flow":8,
    "Institutional Entry":9,
    "Pullback":          3.5,
    "Deep Pullback":     3.0,
    "Momentum Cooling":  5.5,
    "Emerging":          1.5,
    "LTF Spike":         -2,
    "Trap Zone":         -3,
}


def d1_tier_from_score(score: float) -> str:
    """Classify a D1 score into a tier label."""
    if score >= 70: return "SNIPER"
    if score >= 55: return "OPPORTUNITY"
    if score >= 40: return "WATCH"
    return "REJECT"


def d2_tier_from_score(score: float) -> str:
    """Same thresholds for D2."""
    if score >= 70: return "SNIPER"
    if score >= 55: return "OPPORTUNITY"
    if score >= 40: return "WATCH"
    return "REJECT"


def map_state(d1_tier: str, d2_tier: str) -> dict:
    """Map (D1, D2) tier names to a state definition from the matrix."""
    return _matrix_lookup(d1_tier, d2_tier)


def map_state_from_scores(d1_score: float, d2_score: float) -> dict:
    """Convenience: resolve directly from raw scores."""
    return _matrix_lookup(d1_tier_from_score(d1_score),
                          d2_tier_from_score(d2_score))


def get_institutional_category(state_name: str) -> str:
    """Return institutional market category for a state (V5.2)."""
    return STATE_TO_CATEGORY.get(state_name, "DORMANT")


def get_trading_decision(state_name: str) -> str:
    """Return trading decision label for a state (V5.2)."""
    return TRADING_DECISIONS.get(state_name, "No Edge")


def evolution_velocity(prev_state: str, curr_state: str, same_state_cycles: int = 1) -> str:
    """Derive evolution velocity from STATE_ORDER transitions (V5.2).

    Improving  = curr index > prev index (state moving toward institutional)
    Degrading   = curr index < prev index (state deteriorating)
    Stable      = same state for 3+ cycles or no change
    """
    if same_state_cycles >= 3:
        return "stable"

    if not prev_state or prev_state == curr_state:
        return "stable"

    prev_idx = STATE_ORDER.get(prev_state, -1)
    curr_idx = STATE_ORDER.get(curr_state, -1)
    if prev_idx < 0 or curr_idx < 0:
        return "stable"

    if curr_idx > prev_idx:
        return "improving"
    if curr_idx < prev_idx:
        return "degrading"
    return "stable"
