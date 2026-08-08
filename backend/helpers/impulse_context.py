"""Shared impulse context helpers.

Both D1 (engine.py) and D2 (ltf_pipeline.py) need to synthesize CRT-style
context for impulse coins that lack a consolidation/range pattern. These
functions were duplicated in both files — now centralized here.
"""
from backend.vsp_helpers import detect_swing_points
from backend.helpers.candle_math import atr


def synth_crt_score(direction: str, msb_level, candles: list) -> int:
    """Award synthetic CRT points for confirmed impulse structure.

    Returns up to 40 pts for impulse coins so they can reach
    OPPORTUNITY/SNIPER tiers when SMC confirms MSB + OB + FVG.

    Awards:
    - MSB break:            +25 (baseline)
    - Consecutive impulse:  5+ same-body = +10, 3+ = +5
    - Volume surge:         last 5 bars above avg = +5
    """
    if not msb_level:
        return 0

    score = 25

    consecutive = 0
    for c in reversed(candles[-8:]):
        if direction == "BULLISH" and c.close > c.open:
            consecutive += 1
        elif direction == "BEARISH" and c.close < c.open:
            consecutive += 1
        else:
            break

    if consecutive >= 5:
        score += 10
    elif consecutive >= 3:
        score += 5

    if len(candles) >= 25:
        recent_avg = sum(c.volume for c in candles[-5:]) / 5
        prior_avg = sum(c.volume for c in candles[-25:-5]) / 20
        if prior_avg > 0 and recent_avg >= prior_avg * 1.5:
            score += 5

    return min(score, 25)


def build_smc_only_context(candles: list) -> dict | None:
    """Build a minimal CRT-shaped context dict so signal_builder can run
    on a coin where CRT pattern is absent (strong impulse / fresh trend).

    Direction is inferred from the most recent MSB. Displacement is synthesized
    from the dominant impulse leg so scoring, scenario detection, and entry
    logic all have something to work with.
    """
    if len(candles) < 25:
        return None

    swings = detect_swing_points(candles)
    total_swings = len(swings["swing_highs"]) + len(swings["swing_lows"])
    if total_swings < 2:
        return None

    last = candles[-1].close
    recent_highs = sorted(swings["swing_highs"], key=lambda s: s["index"])[-3:]
    recent_lows = sorted(swings["swing_lows"], key=lambda s: s["index"])[-3:]

    direction = None
    level = None
    if recent_highs and last > recent_highs[-1]["price"]:
        direction = "BULLISH"
        level = recent_highs[-1]["price"]
    elif recent_lows and last < recent_lows[-1]["price"]:
        direction = "BEARISH"
        level = recent_lows[-1]["price"]

    if not direction:
        return None

    if direction == "BULLISH":
        swing_low = min((s["price"] for s in recent_lows), default=last)
        body_high = max((c.close for c in candles[-10:] if c.close > c.open), default=last)
        disp_low, disp_high = swing_low, body_high
    else:
        swing_high = max((s["price"] for s in recent_highs), default=last)
        body_low = min((c.close for c in candles[-10:] if c.close < c.open), default=last)
        disp_low, disp_high = body_low, swing_high

    rng_low = min(c.low for c in candles[-20:])
    rng_high = max(c.high for c in candles[-20:])

    return {
        "crt_score": synth_crt_score(direction, level, candles),
        "displacement": {
            "direction": direction,
            "crt_trade_direction": direction,
            "high": round(disp_high, 8),
            "low": round(disp_low, 8),
            "candle_index": len(candles) - 1,
            "msb_level": level,
            "synthesized": True,
        },
        "range": {
            "low": round(rng_low, 8),
            "high": round(rng_high, 8),
            "midpoint": round((rng_low + rng_high) / 2, 8),
            "synthesized": True,
        },
        "fill": None,
        "consolidation": None,
        "in_optimal_ote": False,
        "retracement_percent": 0.0,
        "premium_discount": "EQUILIBRIUM",
        "price_position_pct": 50.0,
        "synthesized": True,
        "atr_value": atr(candles),
    }