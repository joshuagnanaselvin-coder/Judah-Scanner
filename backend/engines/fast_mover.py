"""Fast-Mover Detector — identifies coins that are about to explode.

Institutional pre-signal detection. Runs BEFORE CRT/SMC and tags impulse
moves that have NO consolidation pattern (the pattern CRT requires).

Triggers (any 2+ → flag as FAST_MOVER):
  1. Volume surge:    last 1-3 bars > 2.5x avg of prior 20
  2. Consecutive:     3+ same-direction candles (body close > open)
  3. Range breakout:  current price broke last-20-bar high/low
  4. ATR expansion:   current ATR > 1.5x prior 20-bar ATR
  5. Sweep + reclaim: last candle swept swing low/high and closed back

Score boost:
  +20 base if 2 triggers fire
  +30 base if 3+ triggers fire
  +40 base if 4+ triggers fire
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("judah.fast_mover")


# ── Triggers ─────────────────────────────────────────────────────────────

def _trigger_volume_surge(candles: list) -> Optional[dict]:
    """Last 1-3 bars have volume > 2.5× the prior 20-bar average."""
    if len(candles) < 24:
        return None
    prior_avg = sum(c.volume for c in candles[-23:-3]) / 20
    if prior_avg <= 0:
        return None
    last3_avg = sum(c.volume for c in candles[-3:]) / 3
    ratio = last3_avg / prior_avg
    if ratio >= 3.0:
        return {"name": "volume_surge", "ratio": round(ratio, 2), "weight": 2}
    if ratio >= 2.5:
        return {"name": "volume_surge", "ratio": round(ratio, 2), "weight": 1}
    return None


def _trigger_consecutive(candles: list, lookback: int = 8) -> Optional[dict]:
    """3+ consecutive same-direction bodies."""
    if len(candles) < lookback:
        return None
    consec = 0
    direction = None
    for c in reversed(candles[-lookback:]):
        if c.close > c.open:
            body_dir = "BULLISH"
        elif c.close < c.open:
            body_dir = "BEARISH"
        else:
            break
        if direction is None:
            direction = body_dir
            consec = 1
        elif body_dir == direction:
            consec += 1
        else:
            break
    if consec >= 5:
        return {"name": "consecutive", "count": consec, "direction": direction, "weight": 2}
    if consec >= 3:
        return {"name": "consecutive", "count": consec, "direction": direction, "weight": 1}
    return None


def _trigger_range_breakout(candles: list) -> Optional[dict]:
    """Current price broke the last-20-bar range high or low."""
    if len(candles) < 21:
        return None
    prior_high = max(c.high for c in candles[-21:-1])
    prior_low = min(c.low for c in candles[-21:-1])
    last = candles[-1].close
    if last > prior_high:
        return {"name": "range_breakout", "direction": "BULLISH",
                "level": prior_high, "weight": 2}
    if last < prior_low:
        return {"name": "range_breakout", "direction": "BEARISH",
                "level": prior_low, "weight": 2}
    return None


def _trigger_atr_expansion(candles: list) -> Optional[dict]:
    """Current ATR > 1.5× the average ATR of the prior 20 bars."""
    if len(candles) < 25:
        return None

    def _atr(window):
        if not window or len(window) < 2:
            return 0.0
        trs = []
        for i in range(1, len(window)):
            c = window[i]
            prev_close = window[i - 1].close
            trs.append(max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close)))
        return sum(trs) / len(trs) if trs else 0.0

    # Current bar ATR (single candle true range)
    last = candles[-1]
    prev = candles[-2]
    current_tr = max(
        last.high - last.low,
        abs(last.high - prev.close),
        abs(last.low - prev.close),
    )
    # Average true range of prior 20 bars
    prior_atr = _atr(candles[-21:-1])
    if prior_atr <= 0 or current_tr <= 0:
        return None
    ratio = current_tr / prior_atr
    if ratio >= 2.0:
        return {"name": "atr_expansion", "ratio": round(ratio, 2), "weight": 2}
    if ratio >= 1.5:
        return {"name": "atr_expansion", "ratio": round(ratio, 2), "weight": 1}
    return None


def _trigger_sweep_reclaim(candles: list, swings: dict) -> Optional[dict]:
    """Last candle swept a recent swing low/high and closed back through it."""
    if not swings or len(candles) < 5:
        return None
    last = candles[-1]
    lookback_highs = swings.get("swing_highs", [])[-3:]
    lookback_lows = swings.get("swing_lows", [])[-3:]

    for sl in lookback_lows:
        # Bullish sweep: low went below swing, close reclaimed above
        if last.low < sl["price"] and last.close > sl["price"]:
            return {"name": "sweep_reclaim", "direction": "BULLISH",
                    "level": sl["price"], "weight": 2}
    for sh in lookback_highs:
        # Bearish sweep: high went above swing, close fell back below
        if last.high > sh["price"] and last.close < sh["price"]:
            return {"name": "sweep_reclaim", "direction": "BEARISH",
                    "level": sh["price"], "weight": 2}
    return None


# ── Public API ────────────────────────────────────────────────────────────

def detect_fast_mover(candles: list, swings: Optional[dict] = None) -> dict:
    """Returns a fast-mover context dict.

    Shape:
      {
        "is_fast_mover": bool,
        "score": int,            # boost to apply to CRT score
        "triggers": list[dict],
        "direction": str | None, # inferred dominant direction
        "confidence": float,     # 0..1
      }
    """
    if not candles or len(candles) < 25:
        return {"is_fast_mover": False, "score": 0, "triggers": [],
                "direction": None, "confidence": 0.0}

    if swings is None:
        from backend.vsp_helpers import detect_swing_points
        swings = detect_swing_points(candles[-30:])

    triggers = []
    for fn in (
        _trigger_volume_surge,
        _trigger_consecutive,
        _trigger_range_breakout,
        _trigger_atr_expansion,
        lambda c: _trigger_sweep_reclaim(c, swings),
    ):
        try:
            t = fn(candles)
            if t:
                triggers.append(t)
        except Exception as e:
            logger.debug(f"fast_mover trigger error: {e}")

    if len(triggers) < 2:
        return {"is_fast_mover": False, "score": 0, "triggers": [],
                "direction": None, "confidence": 0.0}

    # Direction: prefer sweep_reclaim or consecutive, fall back to breakout
    direction = None
    for t in triggers:
        if t["name"] == "sweep_reclaim":
            direction = t["direction"]; break
        if t["name"] == "range_breakout":
            direction = t["direction"]; break
        if t["name"] == "consecutive" and t.get("direction"):
            direction = t["direction"]; break

    # Score: weighted by trigger weights
    weight_total = sum(t.get("weight", 1) for t in triggers)
    if weight_total >= 7:
        score = 40
    elif weight_total >= 5:
        score = 30
    else:
        score = 20

    confidence = min(weight_total / 8.0, 1.0)

    return {
        "is_fast_mover": True,
        "score": score,
        "triggers": triggers,
        "direction": direction,
        "confidence": round(confidence, 2),
    }