"""Market Evolution - Confidence Calculator.

V5.2: Blends static matrix confidence with D1/D2 scores + alignment
into a single Evolution Confidence (0-100).

V5.3: Bayesian posterior updating — confidence adjusts based on actual
signal outcomes using Beta distribution.

V5.4: Evidence quality/freshness and evolution stability.

V5.5: DB persistence — alpha/beta per (state, type) saved to SQLite so
calibration survives restarts.

Formula:
  1. Base = matrix confidence (0-95)
  2. D1/D2 boost = (D1_score + D2_score) * 0.1  (capped at +10)
  3. Alignment boost = alignment_score * 0.1     (capped at +2)
  4. Evidence quality boost = CRITICAL/STRONG ratio (capped at +3)
  5. Evidence freshness boost = fresh evidence ratio (capped at +2)
  6. Evolution stability boost = same-state-cycle credit (capped at +3)
  7. Bayesian adjustment = posterior_mean - 0.5  (centered, capped at +-5)
  8. Final = clamp(base + all boosts + bayesian_adj, 0, 100)
"""
import asyncio
import logging
from collections import defaultdict
from .constants import MARKET_EVOLUTION_MATRIX, STATE_TO_CATEGORY

logger = logging.getLogger("judah.confidence")

# Phase 16: Memory safety — max Bayesian entries
_BAYES_MAX_ENTRIES = 500

# ── Bayesian Posterior Tracker ───────────────────────────────────────────────
# Each (state_name, signal_type) pair gets a Beta(alpha, beta) posterior.
# Prior: Beta(1, 1) — uniform (no prior belief).
# On WIN:  alpha += 1
# On LOSS: beta  += 1
# Posterior mean = alpha / (alpha + beta) — this is the calibrated win rate.
# Adjustment = (posterior_mean - 0.5) * 10, capped at +-5.

_bayes_tracker: dict[str, dict[str, dict]] = defaultdict(lambda: {"alpha": 1.0, "beta": 1.0})
_bayes_loaded: bool = False


def _trim_bayes_tracker():
    """Phase 16: Enforce MAX cap on Bayesian tracker entries."""
    if len(_bayes_tracker) <= _BAYES_MAX_ENTRIES:
        return
    excess = len(_bayes_tracker) - _BAYES_MAX_ENTRIES
    for key in sorted(_bayes_tracker.keys())[:excess]:
        del _bayes_tracker[key]
    logger.debug(f"[bayes] Trimmed {excess} entries (cap {_BAYES_MAX_ENTRIES})")


def _get_bayes_key(state_name: str, signal_type: str) -> str:
    return f"{state_name}:{signal_type}"


async def _load_bayes_from_db() -> None:
    """Load Bayesian calibration from SQLite into the in-memory tracker.

    Safe to call multiple times — guarded by _bayes_loaded flag.
    """
    global _bayes_loaded
    if _bayes_loaded:
        return
    try:
        from backend import db
        rows = await db.load_all_bayes()
        for key, entry in rows.items():
            _bayes_tracker[key] = {"alpha": entry["alpha"], "beta": entry["beta"]}
            logger.debug(f"[bayes] loaded {key} a={entry['alpha']:.1f} b={entry['beta']:.1f}")
        _bayes_loaded = True
        logger.info(f"[bayes] Loaded {len(rows)} calibration entries from DB")
    except Exception:
        logger.exception("[bayes] Failed to load calibration from DB")
        _bayes_loaded = True  # don't retry on every call


def record_outcome(state_name: str, signal_type: str, won: bool) -> None:
    """Record a trade outcome for Bayesian updating."""
    key = _get_bayes_key(state_name, signal_type)
    _bayes_tracker[key]["alpha" if won else "beta"] += 1.0
    # Phase 16: trim if over cap
    _trim_bayes_tracker()

    # Phase 22: persist to SQLite (fire-and-forget)
    entry = _bayes_tracker[key]
    try:
        from backend import db
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(db.upsert_bayes(key, entry["alpha"], entry["beta"]))
        else:
            loop.run_until_complete(db.upsert_bayes(key, entry["alpha"], entry["beta"]))
    except Exception:
        logger.exception("[bayes] DB persist failed for %s", key)

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
                   signal_type: str = "",
                   evidence_strengths: list = None,
                   evidence_fresh_ratio: float = 1.0,
                   evolution_stability: int = 0) -> int:
    """Return blended confidence for a state (0-100).

    Args:
        d1_tier, d2_tier: scanner tier classifications
        d1_score, d2_score: raw scanner scores (0-100)
        alignment_score: D1/D2 convergence score (0-20)
        state_name: current market evolution state name
        signal_type: A/B/C/D classification
        evidence_strengths: list of EvidenceStrength enum values
        evidence_fresh_ratio: fraction of evidence that is fresh (0.0-1.0)
        evolution_stability: consecutive same-state cycles (0+)

    Returns:
        Confidence as int 0-100.
    """
    key = (d1_tier.upper(), d2_tier.upper())
    entry = MARKET_EVOLUTION_MATRIX.get(key)
    base = entry.get("confidence", 0) if entry else 0

    # Component 1: D1/D2 score boost (0-10)
    d1d2_boost = min((float(d1_score) + float(d2_score)) * 0.1, 10.0)

    # Component 2: Alignment boost (0-2)
    alignment_boost = min(float(alignment_score) * 0.1, 2.0)

    # Component 3: Evidence quality boost — reward CRITICAL/STRONG evidence (0-3)
    evidence_quality_boost = 0.0
    if evidence_strengths:
        _STRENGTH_VALUE = {"CRITICAL": 3.0, "STRONG": 2.0,
                           "MODERATE": 1.0, "WEAK": 0.0}
        values = [_STRENGTH_VALUE.get(s.name if hasattr(s, "name") else str(s), 0.0)
                  for s in evidence_strengths]
        if values:
            avg_quality = sum(values) / len(values)
            # avg quality / max(3.0) -> ratio, scaled to 0-3
            evidence_quality_boost = min(avg_quality, 3.0)

    # Component 4: Evidence freshness boost — reward fresh evidence (0-2)
    evidence_freshness_boost = min(float(evidence_fresh_ratio) * 2.0, 2.0)

    # Component 5: Evolution stability boost — reward persistent states (0-3)
    # Same state for 3+ cycles is stable; credit tapers after that.
    evolution_stability_boost = 0.0
    if evolution_stability >= 3:
        evolution_stability_boost = min(evolution_stability * 0.5, 3.0)

    # Component 6: Bayesian adjustment from historical outcomes (-5 to +5)
    bayesian_adj = 0.0
    if state_name and signal_type:
        bayesian_adj = get_bayesian_adjustment(state_name, signal_type)

    return int(max(0, min(100, base + d1d2_boost + alignment_boost
                           + evidence_quality_boost + evidence_freshness_boost
                           + evolution_stability_boost + bayesian_adj)))


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
