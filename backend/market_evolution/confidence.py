"""Market Evolution - Confidence Calculator.

V5.2: Blends static matrix confidence with D1/D2 scores + alignment
into a single Evolution Confidence (0-100).

V5.3: Bayesian posterior updating — confidence adjusts based on actual
signal outcomes using Beta distribution.

Formula:
  1. Base = matrix confidence (0-95)
  2. D1/D2 boost = (D1_score + D2_score) * 0.1  (capped at +10)
  3. Alignment boost = alignment_score * 0.1     (capped at +2)
  4. Bayesian adjustment = posterior_mean - 0.5  (centered, capped at +-5)
  5. Final = clamp(base + d1d2_boost + alignment_boost + bayesian_adj, 0, 100)
"""
import logging
from collections import defaultdict
from .constants import MARKET_EVOLUTION_MATRIX, STATE_TO_CATEGORY

logger = logging.getLogger("judah.confidence")

# ── Bayesian Posterior Tracker ───────────────────────────────────────────────
# Each (state_name, signal_type) pair gets a Beta(alpha, beta) posterior.
# Prior: Beta(1, 1) — uniform (no prior belief).
# On WIN:  alpha += 1
# On LOSS: beta  += 1
# Posterior mean = alpha / (alpha + beta) — this is the calibrated win rate.
# Adjustment = (posterior_mean - 0.5) * 10, capped at +-5.

_bayes_tracker: dict[str, dict[str, dict]] = defaultdict(lambda: {"alpha": 1.0, "beta": 1.0})


def _get_bayes_key(state_name: str, signal_type: str) -> str:
    return f"{state_name}:{signal_type}"


def record_outcome(state_name: str, signal_type: str, won: bool) -> None:
    """Record a trade outcome for Bayesian updating."""
    key = _get_bayes_key(state_name, signal_type)
    _bayes_tracker[key]["alpha" if won else "beta"] += 1.0
    logger.debug(f"[bayes] {key} → a={_bayes_tracker[key]['alpha']:.0f} "
                 f"b={_bayes_tracker[key]['beta']:.0f}")


def get_bayesian_adjustment(state_name: str, signal_type: str) -> float:
    """Return Bayesian confidence adjustment (-5 to +5).

    Positive = historical setups of this type in this state tend to win.
    Negative = historical setups tend to lose.
    Zero = not enough data or neutral.
    """
    key = _get_bayes_key(state_name, signal_type)
    entry = _bayes_tracker[key]
    total = entry["alpha"] + entry["beta"]
    if total <= 2.0:
        return 0.0  # still at prior, no adjustment
    posterior_mean = entry["alpha"] / total
    # Center around 0.5, scale to +-5
    adjustment = (posterior_mean - 0.5) * 10.0
    return max(-5.0, min(5.0, adjustment))


def get_bayesian_stats() -> dict:
    """Return tracker stats for debugging."""
    return {
        key: {
            "alpha": round(v["alpha"], 1),
            "beta": round(v["beta"], 1),
            "win_rate": round(v["alpha"] / (v["alpha"] + v["beta"]) * 100, 1),
            "samples": int(v["alpha"] + v["beta"] - 2),
        }
        for key, v in sorted(_bayes_tracker.items())
        if (v["alpha"] + v["beta"]) > 2.0
    }


def get_confidence(d1_tier: str, d2_tier: str,
                   d1_score: float = 0.0, d2_score: float = 0.0,
                   alignment_score: int = 0,
                   state_name: str = "",
                   signal_type: str = "") -> int:
    """Return blended confidence for a state (0-100)."""
    key = (d1_tier.upper(), d2_tier.upper())
    entry = MARKET_EVOLUTION_MATRIX.get(key)
    base = entry.get("confidence", 0) if entry else 0

    # D1/D2 score boost (0-10)
    d1d2_boost = min((float(d1_score) + float(d2_score)) * 0.1, 10.0)

    # Alignment boost (0-2)
    alignment_boost = min(float(alignment_score) * 0.1, 2.0)

    # Bayesian adjustment from historical outcomes (-5 to +5)
    bayesian_adj = 0.0
    if state_name and signal_type:
        bayesian_adj = get_bayesian_adjustment(state_name, signal_type)

    return int(max(0, min(100, base + d1d2_boost + alignment_boost + bayesian_adj)))


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
