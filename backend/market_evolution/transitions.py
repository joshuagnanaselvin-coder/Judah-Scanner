"""Market Evolution - Transition Engine.

Computes evolution direction between two states.
Non-linear: any state can transition to any other state.
Markets do not always evolve linearly.
"""
from .constants import EVOLUTION_LABELS

# Generic order: roughly by quality/strength. Used only for delta direction.
STATE_ORDER = [
    "Dormant",
    "Consolidation",
    "Awakening",
    "LTF Spike",
    "Context Building",
    "Emerging",
    "Trap Zone",
    "Compression",
    "Expansion Watch",
    "Pullback",
    "Deep Pullback",
    "Expansion Setup",
    "Trend Building",
    "Momentum Cooling",
    "Trend Confirmation",
    "Institutional Flow",
    "Institutional Entry",
]


def compute_evolution(prev_state: str, curr_state: str) -> str:
    """Compute evolution label between two states.

    Returns one of: strong_improving, improving, stable, weakening, strong_weakening
    """
    if prev_state == curr_state:
        return "stable"

    try:
        pi = STATE_ORDER.index(prev_state)
    except ValueError:
        pi = 0
    try:
        ci = STATE_ORDER.index(curr_state)
    except ValueError:
        ci = 0

    delta = ci - pi
    if delta >= 3:
        return "strong_improving"
    if delta >= 1:
        return "improving"
    if delta <= -3:
        return "strong_weakening"
    if delta <= -1:
        return "weakening"
    return "stable"


def evolution_label(evolution: str) -> str:
    return EVOLUTION_LABELS.get(evolution, "Stable")


def momentum_velocity(prev_d1: float, curr_d1: float,
                      prev_d2: float, curr_d2: float) -> float:
    """Momentum velocity = (delta D1) x (delta D2)."""
    d1_delta = curr_d1 - prev_d1
    d2_delta = curr_d2 - prev_d2
    return round(d1_delta * d2_delta, 1)


def momentum_label(velocity: float) -> str:
    if velocity > 0:
        return "Positive"
    if velocity < 0:
        return "Negative"
    return "Neutral"
