"""Market Evolution Engine — 16-State Matrix Configuration.

Driven entirely by configuration. No hardcoded if/else logic.
Each (D1_tier, D2_tier) combination maps to a market evolution state.
"""
from typing import Dict, Tuple

# Tier values as used by D1/D2 scanners
TIERS = ("REJECT", "WEAK", "WATCH", "OPPORTUNITY", "SNIPER")

# The 16-state matrix: (D1_tier, D2_tier) -> state definition
MARKET_EVOLUTION_MATRIX: Dict[Tuple[str, str], dict] = {
    # Row: D1 = REJECT
    ("REJECT", "REJECT"): {
        "name": "Dormant",
        "description": "Nothing is happening. No trend. No momentum. No trade.",
        "spiral": "Neutral",
        "tradeStyle": "Ignore",
        "action": "Ignore",
        "confidence": 0,
        "risk": "Very High",
        "trend": False,
        "reversal": False,
        "nextProbableState": "Awakening",
    },
    ("REJECT", "WEAK"): {
        "name": "Dormant",
        "description": "Both timeframes rejected. No actionable signal.",
        "spiral": "Neutral",
        "tradeStyle": "Ignore",
        "action": "Ignore",
        "confidence": 10,
        "risk": "Very High",
        "trend": False,
        "reversal": False,
        "nextProbableState": "Dormant",
    },
    ("REJECT", "WATCH"): {
        "name": "Awakening",
        "description": "Lower timeframe notices activity. Higher timeframe still ignores it.",
        "spiral": "Expansion",
        "tradeStyle": "Observe",
        "action": "Observe",
        "confidence": 20,
        "risk": "High",
        "trend": False,
        "reversal": False,
        "nextProbableState": "Context Building",
    },
    ("REJECT", "OPPORTUNITY"): {
        "name": "Emerging",
        "description": "LTF is strong but HTF has no context. Possible early move.",
        "spiral": "Expansion",
        "tradeStyle": "Observe",
        "action": "Monitor",
        "confidence": 30,
        "risk": "Very High",
        "trend": False,
        "reversal": True,
        "nextProbableState": "Expansion Setup",
    },
    ("REJECT", "SNIPER"): {
        "name": "LTF Spike",
        "description": "LTF extreme without HTF confirmation. Likely a spike or trap.",
        "spiral": "Failure",
        "tradeStyle": "Reversal",
        "action": "Trap Watch",
        "confidence": 15,
        "risk": "Very High",
        "trend": False,
        "reversal": True,
        "nextProbableState": "Dormant",
    },

    # Row: D1 = WATCH
    ("WATCH", "REJECT"): {
        "name": "Context Building",
        "description": "HTF improving, LTF still weak. Possible institutional accumulation.",
        "spiral": "Expansion",
        "tradeStyle": "Observe",
        "action": "Watch",
        "confidence": 25,
        "risk": "High",
        "trend": False,
        "reversal": False,
        "nextProbableState": "Compression",
    },
    ("WATCH", "WATCH"): {
        "name": "Compression",
        "description": "Both timeframes alive. Market compressing. Usually range-bound.",
        "spiral": "Correction",
        "tradeStyle": "Reversal",
        "action": "Mean Reversion",
        "confidence": 40,
        "risk": "Medium",
        "trend": False,
        "reversal": True,
        "nextProbableState": "Expansion Watch",
    },
    ("WATCH", "WEAK"): {
        "name": "Consolidation",
        "description": "HTF watching, LTF weak. No clear directional bias. Wait.",
        "spiral": "Neutral",
        "tradeStyle": "Observe",
        "action": "Wait",
        "confidence": 25,
        "risk": "Medium",
        "trend": False,
        "reversal": False,
        "nextProbableState": "Context Building",
    },
    ("WATCH", "OPPORTUNITY"): {
        "name": "Expansion Watch",
        "description": "HTF watching, LTF building. Prepare for breakout.",
        "spiral": "Expansion",
        "tradeStyle": "Observe",
        "action": "Monitor",
        "confidence": 55,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Expansion Setup",
    },
    ("WATCH", "SNIPER"): {
        "name": "Trap Zone",
        "description": "LTF extreme with weak HTF. Counter-trend trap likely.",
        "spiral": "Failure",
        "tradeStyle": "Reversal",
        "action": "Counter Trend",
        "confidence": 35,
        "risk": "Very High",
        "trend": False,
        "reversal": True,
        "nextProbableState": "Context Building",
    },

    # Row: D1 = OPPORTUNITY
    ("OPPORTUNITY", "REJECT"): {
        "name": "Pullback",
        "description": "HTF strong but LTF rejecting. Temporary pullback within trend.",
        "spiral": "Correction",
        "tradeStyle": "Observe",
        "action": "Wait",
        "confidence": 50,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Expansion Setup",
    },
    ("OPPORTUNITY", "WATCH"): {
        "name": "Expansion Setup",
        "description": "HTF aligned, LTF preparing. Wait for confirmation.",
        "spiral": "Expansion",
        "tradeStyle": "Trend Following",
        "action": "Wait Trigger",
        "confidence": 55,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Trend Building",
    },
    ("OPPORTUNITY", "WEAK"): {
        "name": "Pullback",
        "description": "HTF aligned, LTF weak. Trend intact, waiting for LTF to recover.",
        "spiral": "Correction",
        "tradeStyle": "Trend Following",
        "action": "Wait",
        "confidence": 35,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Expansion Setup",
    },
    ("OPPORTUNITY", "OPPORTUNITY"): {
        "name": "Trend Building",
        "description": "Both timeframes building. Trend developing, momentum increasing.",
        "spiral": "Expansion",
        "tradeStyle": "Trend Following",
        "action": "Early Entry",
        "confidence": 65,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Trend Confirmation",
    },
    ("OPPORTUNITY", "SNIPER"): {
        "name": "Trend Confirmation",
        "description": "HTF aligned, LTF nearly aligned. High probability trend.",
        "spiral": "Expansion",
        "tradeStyle": "Trend Following",
        "action": "Trend Entry",
        "confidence": 80,
        "risk": "Low",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Institutional Entry",
    },

    # Row: D1 = SNIPER
    ("SNIPER", "REJECT"): {
        "name": "Deep Pullback",
        "description": "HTF extreme, LTF rejected. Deep correction in strong trend.",
        "spiral": "Correction",
        "tradeStyle": "Observe",
        "action": "Wait",
        "confidence": 45,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Expansion Setup",
    },
    ("SNIPER", "WATCH"): {
        "name": "Momentum Cooling",
        "description": "HTF still strong, LTF losing strength. Trend may continue.",
        "spiral": "Correction",
        "tradeStyle": "Trend Following",
        "action": "Manage Trade",
        "confidence": 50,
        "risk": "Medium",
        "trend": True,
        "reversal": True,
        "nextProbableState": "Institutional Flow",
    },
    ("SNIPER", "WEAK"): {
        "name": "Momentum Cooling",
        "description": "HTF strong, LTF weak. Trend intact but LTF losing conviction.",
        "spiral": "Correction",
        "tradeStyle": "Trend Following",
        "action": "Manage Trade",
        "confidence": 45,
        "risk": "Medium",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Expansion Setup",
    },
    ("SNIPER", "OPPORTUNITY"): {
        "name": "Institutional Flow",
        "description": "HTF extreme, LTF building. Strong institutional alignment.",
        "spiral": "Expansion",
        "tradeStyle": "Trend Following",
        "action": "Strong Trend",
        "confidence": 88,
        "risk": "Low",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Institutional Entry",
    },
    ("SNIPER", "SNIPER"): {
        "name": "Institutional Entry",
        "description": "Highest quality setup. Both dimensions fully aligned.",
        "spiral": "Expansion",
        "tradeStyle": "Trend Following",
        "action": "Highest Conviction",
        "confidence": 95,
        "risk": "Very Low",
        "trend": True,
        "reversal": False,
        "nextProbableState": "Momentum Cooling",
    },
}

# Spiral definitions - for dashboard grouping
SPIRALS = {
    "Expansion": {
        "color": "#22c55e",
        "description": "Trend-following opportunities",
        "states": [
            "Awakening", "Context Building", "Expansion Watch",
            "Expansion Setup", "Trend Building", "Trend Confirmation",
            "Institutional Flow", "Institutional Entry", "Emerging",
        ],
    },
    "Correction": {
        "color": "#f97316",
        "description": "Pullbacks and mean reversion",
        "states": [
            "Compression", "Pullback", "Deep Pullback", "Momentum Cooling",
        ],
    },
    "Failure": {
        "color": "#ef4444",
        "description": "Traps and false breakouts",
        "states": [
            "LTF Spike", "Trap Zone",
        ],
    },
    "Neutral": {
        "color": "#6b7280",
        "description": "No actionable setup",
        "states": ["Dormant"],
    },
}

# V5.2 - Institutional Market Categories
INSTITUTIONAL_CATEGORIES = {
    "TREND": {
        "label": "Institutional Trend",
        "color": "#22c55e",
        "description": "HTF + LTF alignment. Trade with the prevailing direction.",
        "filter_label": "Institutional Trend",
        "states": [
            "Awakening", "Emerging", "Context Building", "Expansion Watch",
            "Expansion Setup", "Trend Building", "Trend Confirmation",
            "Institutional Flow", "Institutional Entry",
        ],
    },
    "RE_ENTRY": {
        "label": "Institutional Re-Entry",
        "color": "#f59e0b",
        "description": "Trend intact, LTF pulling back. Wait for re-entry at HTF OB / discount.",
        "filter_label": "Institutional Re-Entry",
        "states": [
            "Compression", "Pullback", "Deep Pullback", "Momentum Cooling",
        ],
    },
    "REVERSAL": {
        "label": "Institutional Reversal",
        "color": "#ef4444",
        "description": "HTF/LTF divergence or trap. Counter-trend setup - handle with caution.",
        "filter_label": "Institutional Reversal",
        "states": [
            "LTF Spike", "Trap Zone",
        ],
    },
    "DORMANT": {
        "label": "Dormant",
        "color": "#6b7280",
        "description": "No actionable setup. No trade.",
        "filter_label": "Dormant",
        "states": ["Dormant"],
    },
}

# V5.2 - Trading Decision tags
TRADING_DECISIONS = {
    "Awakening":            "Wait For Confirmation",
    "Emerging":             "Wait For Confirmation",
    "LTF Spike":            "Prepare Reversal",
    "Context Building":     "Wait For Confirmation",
    "Compression":          "Prepare Pullback Entry",
    "Expansion Watch":      "Wait For Confirmation",
    "Trap Zone":            "Avoid",
    "Pullback":             "Prepare Pullback Entry",
    "Expansion Setup":      "Wait For Confirmation",
    "Trend Building":       "Trade With Trend",
    "Trend Confirmation":   "Trade With Trend",
    "Deep Pullback":        "Prepare Pullback Entry",
    "Momentum Cooling":     "Prepare Pullback Entry",
    "Institutional Flow":   "Trade With Trend",
    "Institutional Entry":  "Trade With Trend",
    "Dormant":              "No Edge",
}

# V5.2 - Institutional Category per state
STATE_TO_CATEGORY = {
    state: cat
    for cat, info in INSTITUTIONAL_CATEGORIES.items()
    for state in info["states"]
}

# Evolution labels
EVOLUTION_LABELS = {
    "strong_improving": "Strong Improving",
    "improving": "Improving",
    "stable": "Stable",
    "weakening": "Weakening",
    "strong_weakening": "Strong Weakening",
}

EVOLUTION_SHORT = {
    "strong_improving": "++",
    "improving": "+",
    "stable": "=",
    "weakening": "-",
    "strong_weakening": "--",
}


def get_state(d1_tier: str, d2_tier: str) -> dict:
    """Look up the 16-state matrix. Falls back to Dormant."""
    key = (d1_tier.upper(), d2_tier.upper())
    entry = MARKET_EVOLUTION_MATRIX.get(key)
    if entry:
        return dict(entry)
    return dict(MARKET_EVOLUTION_MATRIX[("REJECT", "REJECT")])
