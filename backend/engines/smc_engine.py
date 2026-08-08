"""SMC Engine — Smart Money Concepts (ICT-aligned). Max SMC score: 25."""
import logging
from typing import Optional
from backend.vsp_helpers import detect_swing_points, detect_fvg
from backend.liquidity_map import detect_liquidity_pools
from backend.helpers.candle_math import atr
from backend.config import (
    VSP_BODY_RATIO_MIN, OB_PROXIMITY_PERCENT, OB_TOUCH_PENALTY,
    MSB_LOOKBACK, FVG_LOOKBACK, FVG_PROXIMITY_PERCENT, LIQUIDITY_SWEEP_PERCENT,
)

logger = logging.getLogger("judah.smc")


def run_smc(candles: list, crt: dict) -> Optional[dict]:
    """SMC Engine — strict ICT scoring. Max SMC score: 25."""
    if not candles or len(candles) < 25 or not crt or not crt.get("displacement"):
        return None

    score = 0
    result = {}

    # 1. Swing Points gate
    swings = detect_swing_points(candles)
    total_swings = len(swings["swing_highs"]) + len(swings["swing_lows"])
    if total_swings < 2:
        return None
    result["swing_count"] = {
        "highs": len(swings["swing_highs"]),
        "lows": len(swings["swing_lows"]),
    }

    # 2. Market Structure (max 5)
    m, msb = _score_msb(candles, swings)
    result["msb"] = msb
    score += m

    # 3. Order Block (max 5)
    o, ob = _score_ob(candles, crt, swings)
    if ob:
        result["ob"] = ob
    score += o

    # 4. Fair Value Gap (max 5)
    f, fvg = _score_fvg(candles, crt)
    if fvg:
        result["fvg"] = fvg
    score += f

    # 5. Liquidity Sweep (max 5)
    l, liq = _score_liquidity(candles, swings)
    if liq:
        result["liquidity"] = liq
    score += l

    smc_score = min(score, 20)

    # Scale to max 25 (components unchanged, final scale factor)
    smc_score = int(smc_score * 1.25)

    logger.debug(f"SMC: msb={m} ob={o} fvg={f} liq={l} = {smc_score}/25")

    return {
        **result,
        "smc_score": smc_score,
    }


# ─── SMC SUB-SCORERS (per spec: max 20 total) ────────────────────────────────

def _score_msb(candles, swings) -> tuple[int, dict]:
    """Max 5: CHOCH confirmed = 5, BOS = 2, none = 0."""
    msb = _detect_msb(candles, swings)
    if not msb or not msb.get("confirmed"):
        return 0, {"confirmed": False, "type": None, "level": None}

    msb_type = msb.get("type", "")
    if msb_type == "CHOCH":
        return 5, msb
    if msb_type == "BOS":
        return 2, msb
    return 1, msb


def _score_ob(candles, crt, swings) -> tuple[int, dict | None]:
    """Max 5: swing-point OB. 0 touches = 0, 1 touch = 5, 2+ = max(1, 5 - (touches-1))."""
    ob = _detect_ob(candles, crt)
    if not ob:
        return 0, None

    retests = _count_ob_touches(candles, ob)
    ob["touches"] = retests
    ob["strength"] = min(retests + 1, 5)

    # Premium/discount zone
    rng = crt.get("range", {})
    rng_mid = rng.get("midpoint", 0)
    if rng_mid > 0:
        ob["zone"] = "PREMIUM" if ob["low"] > rng_mid else ("DISCOUNT" if ob["high"] < rng_mid else "EQUILIBRIUM")
    else:
        ob["zone"] = "UNKNOWN"

    if retests == 0:
        return 0, ob
    # 1st touch = full 5 pts, each additional touch costs 1 pt, floor at 1
    score = max(1, 5 - (retests - 1))
    return score, ob


def _score_fvg(candles, crt) -> tuple[int, dict | None]:
    """Max 5: >=1.5x ATR unfilled = 5, >=1.0x = 3, >=0.5x = 1, partial = 0."""
    all_fvgs = detect_fvg(candles)
    fvg = _find_relevant_fvg(all_fvgs, crt, candles)
    if not fvg:
        return 0, None

    atr_val = atr(candles)
    size_atr = fvg["size"] / atr_val if atr_val > 0 else 0

    # Fill percentage
    filled_pct = 0.0
    if fvg.get("filled"):
        filled_pct = 100.0
    else:
        last_close = candles[-1].close
        fvg_range = fvg["top"] - fvg["bottom"]
        if fvg["type"] == "BULLISH":
            filled_pct = max(0, min(100, ((last_close - fvg["bottom"]) / fvg_range * 100))) if fvg_range > 0 else 0
        else:
            filled_pct = max(0, min(100, ((fvg["top"] - last_close) / fvg_range * 100))) if fvg_range > 0 else 0

    fvg["size_atr"] = round(size_atr, 2)
    fvg["filled_pct"] = round(filled_pct, 1)

    if not fvg.get("filled", False) and size_atr >= 1.5:
        return 5, fvg
    if not fvg.get("filled", False) and size_atr >= 1.0:
        return 3, fvg
    if not fvg.get("filled", False) and size_atr >= 0.5:
        return 1, fvg
    if filled_pct > 0 and filled_pct < 100:
        return 0, fvg
    return 0, fvg


def _score_liquidity(candles, swings) -> tuple[int, dict | None]:
    """Max 5: significant sweep (2+ touches) + reversal = 5, single + reversal = 2, no reversal = 0."""
    liq = _detect_liquidity(candles, swings)
    if not liq or not liq.get("swept"):
        return 0, None

    last = candles[-1]
    level = liq.get("level", 0)

    # Check if sweep was of a significant level (2+ touches)
    significant = _is_significant_level(swings, level)

    # Reversal confirmed?
    if liq["direction"] == "BULLISH" and last.close > level:
        return 5 if significant else 2, liq
    if liq["direction"] == "BEARISH" and last.close < level:
        return 5 if significant else 2, liq

    # No reversal
    return 0, liq


# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────

def _count_ob_touches(candles, ob) -> int:
    """Count how many times price has touched the OB zone."""
    touches = 0
    ob_high = ob["high"]
    ob_low = ob["low"]
    ob_index = ob.get("index", 0)

    for i in range(ob_index + 1, len(candles)):
        c = candles[i]
        if c.low <= ob_high and c.high >= ob_low:
            touches += 1
    return touches


def _is_significant_level(swings, level) -> bool:
    """Check if a level has 2+ swing touches (equal highs/lows)."""
    if not swings:
        return False

    high_count = sum(1 for s in swings.get("swing_highs", [])
                     if abs(s["price"] - level) / level * 100 < 0.3)
    low_count = sum(1 for s in swings.get("swing_lows", [])
                    if abs(s["price"] - level) / level * 100 < 0.3)

    return (high_count + low_count) >= 2


def _detect_ob(candles, crt):
    di = crt["displacement"]["candle_index"]
    dd = crt["displacement"]["direction"]
    last = candles[-1].close
    limit = max(0, di - 15)

    for i in range(di - 1, limit, -1):
        c = candles[i]
        is_opp = (dd == "BULLISH" and c.close < c.open) or \
                 (dd == "BEARISH" and c.close > c.open)
        if is_opp:
            dist = (abs(c.low - last) / last * 100) if dd == "BULLISH" \
                   else (abs(c.high - last) / last * 100)
            if dist <= OB_PROXIMITY_PERCENT:
                return {
                    "type": "BULLISH_OB" if dd == "BULLISH" else "BEARISH_OB",
                    "direction": dd,
                    "high": round(c.high, 2),
                    "low": round(c.low, 2),
                    "open": round(c.open, 2),
                    "close": round(c.close, 2),
                    "index": i,
                    "touches": 0,
                    "broken": False,
                    "proximity": round(dist, 2),
                    "body_ratio": round(abs(c.close - c.open) / (c.high - c.low), 3) if c.high > c.low else 0,
                }
    return None


def _detect_msb(candles, swings):
    """Detect Market Structure Break with type: CHOCH, BOS, or MSS."""
    if not swings:
        return {"confirmed": False, "type": None, "level": None}
    last = candles[-1].close
    lookback = MSB_LOOKBACK

    recent_highs = [s for s in swings["swing_highs"] if s["index"] >= len(candles) - lookback]
    recent_lows = [s for s in swings["swing_lows"] if s["index"] >= len(candles) - lookback]

    if not recent_highs or not recent_lows:
        return {"confirmed": False, "type": None, "level": None}

    last_high = max(recent_highs, key=lambda s: s["index"])
    last_low = max(recent_lows, key=lambda s: s["index"])

    prev_high = max((s for s in recent_highs if s["index"] < last_high["index"]),
                    key=lambda s: s["index"], default=None)
    prev_low = max((s for s in recent_lows if s["index"] < last_low["index"]),
                   key=lambda s: s["index"], default=None)

    # Bullish break above recent high
    if last > last_high["price"]:
        # CHOCH if breaking a low->high structure (trend change)
        if prev_high and prev_low and prev_low["index"] > prev_high["index"]:
            return {"confirmed": True, "type": "CHOCH", "direction": "BULLISH", "level": last_high["price"]}
        return {"confirmed": True, "type": "BOS", "direction": "BULLISH", "level": last_high["price"]}

    # Bearish break below recent low
    if last < last_low["price"]:
        if prev_high and prev_low and prev_high["index"] > prev_low["index"]:
            return {"confirmed": True, "type": "CHOCH", "direction": "BEARISH", "level": last_low["price"]}
        return {"confirmed": True, "type": "BOS", "direction": "BEARISH", "level": last_low["price"]}

    return {"confirmed": False, "type": None, "level": None}


def _find_relevant_fvg(fvgs, crt, candles):
    if not fvgs or not crt.get("displacement"):
        return None
    dd = crt["displacement"]["direction"]
    last = candles[-1].close
    unfilled = [f for f in fvgs if not f.get("filled", False)]

    if dd == "BULLISH":
        candidates = [f for f in unfilled if f["type"] == "BULLISH"]
        candidates.sort(key=lambda f: abs(f["top"] - last))
    else:
        candidates = [f for f in unfilled if f["type"] == "BEARISH"]
        candidates.sort(key=lambda f: abs(f["bottom"] - last))

    if not candidates:
        return None
    fvg = candidates[0]
    prox = abs(fvg["top"] - last) / last * 100
    return {"type": fvg["type"], "top": fvg["top"], "bottom": fvg["bottom"],
            "size": fvg["size"], "proximity": round(prox, 2)}


def _detect_liquidity(candles, swings):
    if not swings:
        return {"swept": False}
    last = candles[-1]
    thresh = LIQUIDITY_SWEEP_PERCENT

    for sl in swings["swing_lows"][-5:]:
        if last.low < sl["price"] * (1 - thresh / 100) and last.close > sl["price"]:
            return {"swept": True, "direction": "BULLISH", "level": sl["price"]}

    for sh in swings["swing_highs"][-5:]:
        if last.high > sh["price"] * (1 + thresh / 100) and last.close < sh["price"]:
            return {"swept": True, "direction": "BEARISH", "level": sh["price"]}

    return {"swept": False}
