"""Single engine file — flow-first, structure-confirm, CRT-time.

Pipeline order (institutional):
  1. Flow gate:      Is there volume + sweep + VWAP reclaim or RS vs BTC?
                     If no flow → SKIP (don't show neutral coins)
  2. Structure:      CRT + SMC (OB, FVG, MSB, VSP)
  3. Time:           CRT timing (OTE, displacement, session)
  4. Signal:         Combine into entry / SL / TP / RR
"""
from backend.engines.crt_engine import run_crt
from backend.engines.smc_engine import run_smc
from backend.engines.signal_builder import build_signal
from backend.engines.fast_mover import detect_fast_mover
from backend.engines.flow_analyzer import analyze_flow, detect_vwap_reclaim, compute_session_vwap
from backend.market_data import market_data
from backend.helpers.candle_math import atr, atr_percent, calc_envelope, _get
from backend.vsp_helpers import detect_swing_points
from backend.helpers.session import get_session_at, session_score, get_session_label
from backend.config import (
    MIN_ATR_PERCENT, ADAPTIVE_ATR_MIN_ABSOLUTE, MIN_RANGE_MULTIPLIER,
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE,
)
import logging

logger = logging.getLogger("judah.engine")

# Thresholds for the SMC-only fallback path (used when CRT fails on impulse moves)
_FALLBACK_MIN_SMC_SCORE = 15  # need at least MSB + OB / FVG
_FALLBACK_REQUIRED_MSB = True


def _synth_crt_score(direction: str, msb_level, candles: list) -> int:
    """Award synthetic CRT points for confirmed impulse structure.

    Returns up to 40 pts for impulse coins so they can reach
    OPPORTUNITY/SNIPER tiers when SMC confirms MSB + OB + FVG.

    Awards:
    - MSB break:            +25 (baseline — structural confirmation)
    - Consecutive impulse:  5+ same-body = +10, 3+ = +5
    - Volume surge:         last 5 bars above avg = +5
    """
    if not msb_level:
        return 0

    score = 25  # MSB break is the baseline for the impulse setup

    # Consecutive same-direction candles in the last 8 = momentum confirmation
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

    # Volume surge on the last 5 bars vs the prior 20
    if len(candles) >= 25:
        recent_avg = sum(c.volume for c in candles[-5:]) / 5
        prior_avg = sum(c.volume for c in candles[-25:-5]) / 20
        if prior_avg > 0 and recent_avg >= prior_avg * 1.5:
            score += 5

    return min(score, 40)


def _build_smc_only_context(candles: list) -> dict | None:
    """Build a minimal CRT-shaped context dict so signal_builder can run
    on a coin where CRT pattern is absent (strong impulse / fresh trend).

    Direction is inferred from the most recent MSB. Displacement is synthesized
    from the dominant impulse leg so scoring, scenario detection, and entry
    logic all have something to work with.
    """
    if len(candles) < 30:
        return None

    swings = detect_swing_points(candles)
    total_swings = len(swings["swing_highs"]) + len(swings["swing_lows"])
    if total_swings < 2:
        return None

    # Determine direction from the most recent swing break
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

    # Synthesize displacement from the most recent impulse leg
    if direction == "BULLISH":
        swing_low = min((s["price"] for s in recent_lows), default=last)
        body_high = max((c.close for c in candles[-10:] if c.close > c.open), default=last)
        disp_low = swing_low
        disp_high = body_high
    else:
        swing_high = max((s["price"] for s in recent_highs), default=last)
        body_low = min((c.close for c in candles[-10:] if c.close < c.open), default=last)
        disp_low = body_low
        disp_high = swing_high

    rng_low = min(c.low for c in candles[-20:])
    rng_high = max(c.high for c in candles[-20:])

    return {
        # Synthesized CRT context — score represents the structural quality of the impulse,
        # not a real consolidation/range pattern. Awarded so impulse coins can reach
        # OPPORTUNITY/SNIPER when SMC confirms MSB + OB + FVG.
        "crt_score": _synth_crt_score(direction, level, candles),
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


def scan(symbol: str, timeframe: str) -> dict | None:
    """Run full CRT -> SMC -> Signal pipeline for one coin on one timeframe.

    Falls back to SMC-only path if CRT returns None (impulse / fast-moving coins
    where the strict 5-step CRT consolidation pattern doesn't exist yet).
    """
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < 50:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: no candles ({len(candles) if candles else 0})")
        return None

    last_price = _get(candles[-1], 'close')

    # Volatility gate
    atr_val = atr(candles)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0
    if atr_pct < MIN_ATR_PERCENT or atr_val < ADAPTIVE_ATR_MIN_ABSOLUTE:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: ATR {atr_val:.6f} ({atr_pct:.3f}%) below threshold")
        return None

    # Range size gate
    env = calc_envelope(candles, 50)
    range_size = env.get('range_size', 0)
    if range_size < atr_val * MIN_RANGE_MULTIPLIER:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: range {range_size:.6f} < {MIN_RANGE_MULTIPLIER}x ATR")
        return None

    # === FLOW GATE: skip neutral / coiled coins ===
    # No flow = no signal. We only show coins where volume/sweep/VWAP/RS
    # confirm that real money is moving.
    swings = detect_swing_points(candles[-30:])
    btc_candles = market_data.get_candles("BTCUSDT", timeframe)
    flow = analyze_flow(symbol, candles, swings, timeframe, btc_candles)
    fast = detect_fast_mover(candles, swings)

    # === FLOW GATE: only skip if ZERO flow triggers (completely neutral) ===
    # On D1, rarely get multiple triggers — a single meaningful trigger is enough.
    if not flow["is_flowing"] and not fast["is_fast_mover"]:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: no flow triggers (flat market)")
        return None

    logger.debug(f"[engine] FLOW {symbol} {timeframe}: boost=+{flow['boost']} "
                 f"triggers={[t['name'] for t in flow['triggers']]} kz={flow['killzone']['zone']} "
                 f"dir={flow['direction']} fast_mover={fast['is_fast_mover']}")
    # ============================================

    # === PRIMARY PATH: CRT + SMC ===
    logger.debug(f"[engine] Running CRT for {symbol} {timeframe} ({len(candles)} candles, last={last_price:.5f})")
    crt = run_crt(candles)

    if crt:
        logger.debug(f"[engine] CRT passed {symbol} {timeframe}: score={crt.get('crt_score',0)} dir={crt.get('displacement',{}).get('crt_trade_direction','?')}")
        smc = run_smc(candles, crt)
        if not smc:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: SMC returned None")
            return None
        logger.debug(f"[engine] SMC passed {symbol} {timeframe}: score={smc.get('smc_score',0)}")
        path = "CRT+SMC"
    else:
        # === FALLBACK PATH: SMC-only (impulse / fast-moving coins) ===
        logger.debug(f"[engine] CRT missing for {symbol} {timeframe} — trying SMC-only fallback")
        fallback_crt = _build_smc_only_context(candles)
        if not fallback_crt:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: no CRT and no MSB direction")
            return None
        smc = run_smc(candles, fallback_crt)
        if not smc:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: SMC-only fallback returned None")
            return None
        if _FALLBACK_REQUIRED_MSB and not (smc.get("msb") or {}).get("confirmed"):
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: SMC-only fallback missing MSB confirmation")
            return None
        if smc.get("smc_score", 0) < _FALLBACK_MIN_SMC_SCORE:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: SMC-only fallback score {smc.get('smc_score',0)} < {_FALLBACK_MIN_SMC_SCORE}")
            return None
        crt = fallback_crt
        path = "SMC-ONLY"

    # Signal builder
    # D1 (structure-heavy): CRT(30) + SMC(25) + Flow(20) + Momentum(15) = 90 max
    fm = detect_fast_mover(candles, swings)
    crt["crt_score"] = min(crt.get("crt_score", 0), 30)
    smc["smc_score"] = min(smc.get("smc_score", 0), 25)
    flow_score = min(flow["boost"], 20)
    momentum_score = min(fm["score"] if fm["is_fast_mover"] else 0, 15)

    logger.debug(f"[engine] Building signal for {symbol} {timeframe} ({path}) "
                 f"flow={flow_score} momentum={momentum_score}")
    signal = build_signal(symbol, timeframe, crt, smc, candles, flow_score, momentum_score)

    if signal:
        signal["flow"] = flow
        signal["flow_score"] = flow_score
        signal["flow_boost"] = flow_score
        signal["fast_mover"] = fm
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
        logger.info(f"[engine] SIGNAL {symbol} {timeframe}: {signal['tier']} score={signal['composite_score']} "
                     f"dir={signal['direction']} rr={signal['rr']:.1f} entry={signal['entry']:.5f} "
                     f"sl={signal['stop_loss']:.5f} tp1={signal.get('take_profit_1', signal.get('take_profit', 0)):.5f} "
                     f"path={path} crt={signal['crt_score']} smc={signal['smc_score']} "
                     f"flow={flow_score} mom={momentum_score} "
                     f"triggers={[t['name'] for t in flow['triggers']]}")
    else:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: build_signal returned None")
    return signal

