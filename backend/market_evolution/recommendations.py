"""Market Evolution - Recommendations Engine.

Resolves trade style and action per state.
Currently static from the matrix.
Ready for AI-driven recommendations.
"""
from .constants import MARKET_EVOLUTION_MATRIX


def get_recommendation(d1_tier: str, d2_tier: str) -> dict:
    """Return trade style, action, and risk for a state."""
    key = (d1_tier.upper(), d2_tier.upper())
    entry = MARKET_EVOLUTION_MATRIX.get(key, {})
    return {
        "tradeStyle": entry.get("tradeStyle", "Ignore"),
        "action": entry.get("action", "Ignore"),
        "risk": entry.get("risk", "Very High"),
    }
