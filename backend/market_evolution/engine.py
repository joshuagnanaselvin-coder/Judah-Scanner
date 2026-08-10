"""Market Evolution Engine — Main Entry Point.

Single source of truth for the frontend.
Every coin gets a MarketEvolutionState.

Pipeline:
  D1 (tier, score) + D2 (tier, score) + previous -> MarketEvolutionState

This module does NOT modify D1, D2, or D3 logic.
It only consumes their output and produces a unified lifecycle object.
"""
import logging
from typing import Dict, Optional

from .constants import (
    MARKET_EVOLUTION_MATRIX,
    SPIRALS,
    EVOLUTION_LABELS,
    EVOLUTION_SHORT,
    get_state,
)
from .mapper import (
    map_state, d1_tier_from_score, d2_tier_from_score,
    get_institutional_category, get_trading_decision, evolution_velocity,
)
from .transitions import compute_evolution, momentum_velocity, evolution_label
from .confidence import get_confidence
from .recommendations import get_recommendation
from .history import history_store, HistoryStore
from .models import MarketEvolutionState

logger = logging.getLogger("judah.market_evolution")

# Singleton history store
_history = history_store

# Per-coin previous state cache (so we can compute evolution)
_prev_state: Dict[str, str] = {}
_prev_d1_score: Dict[str, float] = {}
_prev_d2_score: Dict[str, float] = {}
# V5.2: track same-state-cycle count for stable detection
_same_state_count: Dict[str, int] = {}


def evaluate_for_fusion(ctx: "FusionContext") -> MarketEvolutionState:
    """Evaluate Market Evolution from a FusionContext object.

    Phase 7.3 - explicit input contract between Fusion and Market Evolution.
    Fusion builds the context; ME consumes it. This decouples ME from
    Fusion's internal variable unpacking.
    """
    from .models import FusionContext
    return evaluate(
        ctx.coin,
        ctx.d1_tier, ctx.d1_score,
        ctx.d2_tier, ctx.d2_score,
        ctx.direction,
        ctx.alignment_score,
    )


def evaluate(coin: str,
             d1_tier: str, d1_score: float,
             d2_tier: str, d2_score: float,
             direction: str = "BULLISH",
             alignment_score: int = 0,
             signal_type: str = "") -> MarketEvolutionState:
    """Evaluate Market Evolution for one coin.

    V5.2 - Institutional Frontend:
      - institutionalCategory: TREND / RE-ENTRY / REVERSAL / DORMANT
      - tradingDecision:       Trade With Trend / Wait For Confirmation /
                               Prepare Pullback Entry / Prepare Reversal /
                               Avoid / No Edge
      - evolutionVelocity:     improving / stable / degrading
      - evolutionConfidence:   blended (matrix + D1/D2 + alignment)

    V5.1 - alignment_score (0-20) is consumed as context, not as a score boost.

    Returns a fully populated MarketEvolutionState.
    """
    state_def = map_state(d1_tier, d2_tier)
    state_name = state_def["name"]

    # Previous state for evolution
    prev_state = _prev_state.get(coin, "Dormant")
    evolution = compute_evolution(prev_state, state_name)

    # V5.2 - same-state-cycle count for stable detection
    if prev_state == state_name:
        _same_state_count[coin] = _same_state_count.get(coin, 1) + 1
    else:
        _same_state_count[coin] = 1
    same_state_cycles = _same_state_count[coin]

    # Momentum velocity
    prev_d1 = _prev_d1_score.get(coin, 0.0)
    prev_d2 = _prev_d2_score.get(coin, 0.0)
    velocity = momentum_velocity(prev_d1, d1_score, prev_d2, d2_score)

    # V5.2 - Blended confidence (matrix + scores + alignment)
    state_name_for_bayes = state_def.get("name", state_name)
    confidence = get_confidence(d1_tier, d2_tier, d1_score, d2_score, alignment_score,
                                state_name=state_name_for_bayes, signal_type=signal_type)
    rec = get_recommendation(d1_tier, d2_tier)

    # V5.2 - Institutional category + trading decision (derived from state)
    institutional_category = get_institutional_category(state_name)
    trading_decision = get_trading_decision(state_name)

    # V5.2 - Evolution velocity from MATRIX transitions, not scores
    vel = evolution_velocity(prev_state, state_name, same_state_cycles)

    # Record history
    _history.record(coin, state_name, state_def["spiral"], direction,
                    d1_score, d2_score, velocity, evolution, alignment_score,
                    institutional_category, trading_decision, vel)

    # Cache for next call
    _prev_state[coin] = state_name
    _prev_d1_score[coin] = d1_score
    _prev_d2_score[coin] = d2_score

    return MarketEvolutionState(
        state=state_name,
        description=state_def["description"],
        tradeStyle=rec["tradeStyle"],
        action=rec["action"],
        confidence=confidence,
        risk=rec["risk"],
        evolution=evolution,
        momentumVelocity=velocity,
        previousState=prev_state,
        nextProbableState=state_def.get("nextProbableState", "Dormant"),
        spiral=state_def["spiral"],
        transitionHistory=_history.get_history(coin),
        alignmentScore=alignment_score,
        institutionalCategory=institutional_category,
        tradingDecision=trading_decision,
        evolutionVelocity=vel,
        evolutionConfidence=confidence,
    )


def evaluate_from_scores(coin: str,
                         d1_score: float, d2_score: float,
                         direction: str = "BULLISH") -> MarketEvolutionState:
    """Convenience: evaluate from raw scores only."""
    return evaluate(
        coin,
        d1_tier_from_score(d1_score),
        d1_score,
        d2_tier_from_score(d2_score),
        d2_score,
        direction,
    )


def evaluate_many(coins_data: Dict[str, dict]) -> Dict[str, MarketEvolutionState]:
    """Evaluate many coins.

    Input: {coin: {"d1_tier", "d1_score", "d2_tier", "d2_score", "direction"}}
    """
    results = {}
    for coin, data in coins_data.items():
        results[coin] = evaluate(
            coin,
            data.get("d1_tier", "REJECT"),
            data.get("d1_score", 0),
            data.get("d2_tier", "REJECT"),
            data.get("d2_score", 0),
            data.get("direction", "BULLISH"),
        )
    return results


def get_dashboard_stats(all_states: list) -> dict:
    """Aggregate counter stats for the dashboard."""
    spiral_counts: Dict[str, int] = {s: 0 for s in SPIRALS}
    state_counts: Dict[str, int] = {}

    for state in all_states:
        spiral = state.spiral
        if spiral in spiral_counts:
            spiral_counts[spiral] += 1
        name = state.state
        state_counts[name] = state_counts.get(name, 0) + 1

    return {
        "spiral": spiral_counts,
        "states": state_counts,
    }
