"""Dimension 2 — LTF Pipeline (15M timeframe).

D2's OWN 4-layer pipeline — no imports from D1's engine.py.
Uses the same layer libraries (CRT, SMC, Flow, Momentum) but
has its own scan() function with D2-specific parameters and flow.

Pipeline order:
  1. Flow gate:      Is there volume + sweep + VWAP reclaim or RS vs BTC?
  2. Structure:      CRT + SMC (OB, FVG, MSB, VSP)
  3. Time:           CRT timing (OTE, displacement, session)
  4. Signal:         Combine into entry / SL / TP / RR
"""
from backend.engines.crt_engine import run_crt
from backend.engines.smc_engine import run_smc
from backend.engines.signal_builder import build_signal
from backend.engines.fast_mover import detect_fast_mover
from backend.engines.flow_analyzer import analyze_flow
from backend.market_data import market_data
from backend.helpers.candle_math import atr, calc_envelope, _get
from backend.vsp_helpers import detect_swing_points
from backend.config import (
    MIN_ATR_PERCENT, ADAPTIVE_ATR_MIN_ABSOLUTE, MIN_RANGE_MULTIPLIER,
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE,
)
import logging

logger = logging.getLogger("judah.ltf_pipeline")

_FALLBACK_MIN_SMC_SCORE = 15
_FALLBACK_REQUIRED_MSB = True


def _synth_crt_score(direction: str, msb_level, candles: list) -> int:
    """Award synthetic CRT points for impulse structure (up to 40)."""
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
    return min(score, 40)


def _build_smc_only_context(candles: list) -> dict | None:
    """Synthesize CRT context for impulse coins (no consolidation/range candle)."""
    if len(candles) < 30:
        return None
    swings = detect_swing_points(candles)
    if len(swings["swing_highs"]) + len(swings["swing_lows"]) < 2:
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
        "crt_score": _synth_crt_score(direction, level, candles),
        "displacement": {
            "direction": direction, "crt_trade_direction": direction,
            "high": round(disp_high, 8), "low": round(disp_low, 8),
            "candle_index": len(candles) - 1, "msb_level": level, "synthesized": True,
        },
        "range": {
            "low": round(rng_low, 8), "high": round(rng_high, 8),
            "midpoint": round((rng_low + rng_high) / 2, 8), "synthesized": True,
        },
        "fill": None, "consolidation": None, "in_optimal_ote": False,
        "retracement_percent": 0.0, "premium_discount": "EQUILIBRIUM",
        "price_position_pct": 50.0, "synthesized": True, "atr_value": atr(candles),
    }


def scan_ltf_pipeline(symbol: str, timeframe: str = "15M") -> dict | None:
    """D2's own 4-layer pipeline — independent from D1.

    Flow → CRT → SMC → Momentum → Signal Builder
    Falls back to SMC-only for impulse coins.
    """
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < 25:
        logger.debug(f"[ltf_pipeline] SKIP {symbol} {timeframe}: insufficient candles")
        return None

    last_price = _get(candles[-1], 'close')
    atr_val = atr(candles)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0
    if atr_pct < MIN_ATR_PERCENT or atr_val < ADAPTIVE_ATR_MIN_ABSOLUTE:
        logger.debug(f"[ltf_pipeline] SKIP {symbol} {timeframe}: ATR below threshold")
        return None

    env = calc_envelope(candles, 50)
    range_size = env.get('range_size', 0)
    if range_size < atr_val * MIN_RANGE_MULTIPLIER:
        logger.debug(f"[ltf_pipeline] SKIP {symbol} {timeframe}: range too small")
        return None

    # FLOW GATE
    swings = detect_swing_points(candles[-30:])
    btc_candles = market_data.get_candles("BTCUSDT", timeframe)
    flow = analyze_flow(symbol, candles, swings, timeframe, btc_candles)
    fast = detect_fast_mover(candles, swings)

    if not flow["is_flowing"] and not fast["is_fast_mover"]:
        logger.debug(f"[ltf_pipeline] SKIP {symbol} {timeframe}: no flow triggers")
        return None

    logger.debug(f"[ltf_pipeline] FLOW {symbol} {timeframe}: boost=+{flow['boost']} "
                 f"triggers={[t['name'] for t in flow['triggers']]} kz={flow['killzone']['zone']} "
                 f"dir={flow['direction']} fast_mover={fast['is_fast_mover']}")

    # PRIMARY PATH: CRT + SMC
    logger.debug(f"[ltf_pipeline] Running CRT for {symbol} {timeframe} ({len(candles)} candles)")
    crt = run_crt(candles)

    if crt:
        logger.debug(f"[ltf_pipeline] CRT passed {symbol}: score={crt.get('crt_score',0)}")
        smc = run_smc(candles, crt)
        if not smc:
            logger.debug(f"[ltf_pipeline] SKIP {symbol}: SMC returned None")
            return None
        logger.debug(f"[ltf_pipeline] SMC passed {symbol}: score={smc.get('smc_score',0)}")
        path = "CRT+SMC"
    else:
        # FALLBACK: SMC-only for impulse coins
        logger.debug(f"[ltf_pipeline] CRT missing for {symbol} — trying SMC-only fallback")
        fallback_crt = _build_smc_only_context(candles)
        if not fallback_crt:
            logger.debug(f"[ltf_pipeline] SKIP {symbol}: no CRT and no MSB direction")
            return None
        smc = run_smc(candles, fallback_crt)
        if not smc:
            logger.debug(f"[ltf_pipeline] SKIP {symbol}: SMC-only fallback returned None")
            return None
        if _FALLBACK_REQUIRED_MSB and not (smc.get("msb") or {}).get("confirmed"):
            logger.debug(f"[ltf_pipeline] SKIP {symbol}: SMC-only fallback missing MSB")
            return None
        if smc.get("smc_score", 0) < _FALLBACK_MIN_SMC_SCORE:
            logger.debug(f"[ltf_pipeline] SKIP {symbol}: SMC score too low")
            return None
        crt = fallback_crt
        path = "SMC-ONLY"

    # SIGNAL BUILDER
    # D2 (execution-heavy): CRT(10) + SMC(10) + Flow(35) + Momentum(35) = 90 max
    fm = detect_fast_mover(candles, swings)
    crt["crt_score"] = min(crt.get("crt_score", 0), 10)
    smc["smc_score"] = min(smc.get("smc_score", 0), 10)
    flow_score = min(flow["boost"], 35)
    momentum_score = min(fm["score"] if fm["is_fast_mover"] else 0, 35)

    logger.debug(f"[ltf_pipeline] Building {symbol} ({path}) flow={flow_score} momentum={momentum_score}")
    signal = build_signal(symbol, timeframe, crt, smc, candles, flow_score, momentum_score)

    if signal:
        signal["flow_score"] = flow_score
        signal["fast_mover_boost"] = momentum_score
        signal["momentum_score"] = momentum_score
        composite = signal["composite_score"]
        if composite >= TIER_SNIPER_SCORE:
            signal["tier"] = "SNIPER"
        elif composite >= TIER_OPPORTUNITY_SCORE:
            signal["tier"] = "OPPORTUNITY"
        elif composite >= TIER_WATCH_SCORE:
            signal["tier"] = "WATCH"
        else:
            signal["tier"] = "REJECTED"
        signal["engine_path"] = path
        signal["flow_direction"] = flow["direction"]
        signal["killzone"] = flow["killzone"]
        logger.info(f"[ltf_pipeline] SIGNAL {symbol} {timeframe}: {signal['tier']} score={signal['composite_score']} "
                     f"dir={signal['direction']} rr={signal['rr']:.1f} path={path} "
                     f"crt={signal['crt_score']} smc={signal['smc_score']} flow={flow_score} mom={momentum_score}")
    else:
        logger.debug(f"[ltf_pipeline] SKIP {symbol}: build_signal returned None")
    return signal
