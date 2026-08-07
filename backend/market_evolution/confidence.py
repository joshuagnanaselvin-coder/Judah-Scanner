"""Market Evolution - Confidence Calculator.

V5.2: Blends static matrix confidence with D1/D2 scores + alignment
into a single Evolution Confidence (0-100).

Formula:
  1. Base = matrix confidence (0-95)
  2. D1/D2 boost = (D1_score + D2_score) * 0.1  (capped at +10)
  3. Alignment boost = alignment_score * 0.1     (capped at +2)
  4. Final = clamp(base + d1d2_boost + alignment_boost, 0, 100)
"""
from .constants import MARKET_EVOLUTION_MATRIX, STATE_TO_CATEGORY


def get_confidence(d1_tier: str, d2_tier: str,
                   d1_score: float = 0.0, d2_score: float = 0.0,
                   alignment_score: int = 0) -> int:
    """Return blended confidence for a state."""
    key = (d1_tier.upper(), d2_tier.upper())
    entry = MARKET_EVOLUTION_MATRIX.get(key)
    base = entry.get("confidence", 0) if entry else 0

    # D1/D2 score boost (0-10)
    d1d2_boost = min((float(d1_score) + float(d2_score)) * 0.1, 10.0)

    # Alignment boost (0-2)
    alignment_boost = min(float(alignment_score) * 0.1, 2.0)

    return int(max(0, min(100, base + d1d2_boost + alignment_boost)))


def get_institutional_category(state_name: str) -> str:
    """Return institutional market category for a state."""
    return STATE_TO_CATEGORY.get(state_name, "DORMANT")


def get_trading_decision(state_name: str) -> str:
    """Return trading decision label for a state."""
    from .constants import TRADING_DECISIONS
    return TRADING_DECISIONS.get(state_name, "No Edge")


__all__ = [
    "get_confidence",
    "get_institutional_category",
    "get_trading_decision",
]
