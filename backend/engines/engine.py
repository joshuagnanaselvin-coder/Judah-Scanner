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
from backend.helpers.impulse_context import synth_crt_score, build_smc_only_context
from backend.vsp_helpers import detect_swing_points
from backend.helpers.session import get_session_at, session_score, get_session_label
from backend.config import (
    MIN_ATR_PERCENT, ADAPTIVE_ATR_MIN_ABSOLUTE, MIN_RANGE_MULTIPLIER,
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE, TIER_WEAK_SCORE,
    SMC_SCORE_MAX, MIN_RR,
    TIMING_KILLZONE_MAX, TIMING_SESSION_MAX, TIMING_DAYS_MAX,
    RR_SCORE_MAX, SL_QUALITY_MAX, CONFLUENCE_MAX,
    SL_RELEVANCE_PCT, SL_MAX_STRUCTURAL_DISTANCE_PCT, SL_ATR_FALLBACK_MULT,
    KILLZONE_LONDON_START, KILLZONE_LONDON_END,
    KILLZONE_NY_START, KILLZONE_NY_END,
    KILLZONE_LONDON_CLOSE_START, KILLZONE_LONDON_CLOSE_END,
)
import logging

logger = logging.getLogger("judah.engine")

# Thresholds for the SMC-only fallback path (used when CRT fails on impulse moves)
_FALLBACK_MIN_CONFIDENCE = 30  # weighted fallback threshold (replaces binary MSB + SMC≥15)


def scan(symbol: str, timeframe: str) -> dict | None:
    """Run full CRT -> SMC -> Signal pipeline for one coin on one timeframe.

    Falls back to SMC-only path if CRT returns None (impulse / fast-moving coins
    where the strict 5-step CRT consolidation pattern doesn't exist yet).
    """
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < 25:
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
        fallback_crt = build_smc_only_context(candles)
        if not fallback_crt:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: no CRT and no MSB direction")
            return None
        smc = run_smc(candles, fallback_crt)
        if not smc:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: SMC-only fallback returned None")
            return None

        # === WEIGHTED FALLBACK CONFIDENCE (no all-or-nothing gates) ===
        fallback_score = 0
        msb_confirmed = (smc.get("msb") or {}).get("confirmed", False)
        ob = smc.get("ob")
        fvg = smc.get("fvg")
        liq_swept = (smc.get("liquidity") or {}).get("swept", False)

        if msb_confirmed:
            fallback_score += 8
        if ob and ob.get("strength", 0) >= 3:
            fallback_score += 5
        if fvg and fvg.get("proximity", 999) <= 1.0:
            fallback_score += 4
        if liq_swept:
            fallback_score += 5
        if flow.get("boost", 0) > 18:
            fallback_score += 8
        if fast.get("is_fast_mover") and fast.get("score", 0) > 15:
            fallback_score += 8

        logger.debug(f"[engine] Fallback confidence: {fallback_score}/{_FALLBACK_MIN_CONFIDENCE} "
                     f"(msb={8 if msb_confirmed else 0} ob={5 if ob and ob.get('strength',0)>=3 else 0} "
                     f"fvg={4 if fvg and fvg.get('proximity',999)<=1.0 else 0} "
                     f"liq={5 if liq_swept else 0} flow={8 if flow.get('boost',0)>18 else 0} "
                     f"mom={8 if fast.get('is_fast_mover') and fast.get('score',0)>15 else 0})")

        if fallback_score < _FALLBACK_MIN_CONFIDENCE:
            logger.debug(f"[engine] SKIP {symbol} {timeframe}: fallback confidence {fallback_score} < {_FALLBACK_MIN_CONFIDENCE}")
            return None

        crt = fallback_crt
        path = "SMC-ONLY"

    # === D1 100-POINT SCORING ===
    # D1: CRT(20) + SMC(25) + Flow(15) + Momentum(15) + Timing(10) + R/R(10) + Confluence(5) = 100
    fm = detect_fast_mover(candles, swings)
    crt["crt_score"] = min(crt.get("crt_score", 0), 20)  # D1 CRT capped at 20
    smc["smc_score"] = min(smc.get("smc_score", 0), SMC_SCORE_MAX)
    flow_score = min(flow["boost"], 15)
    momentum_score = min(fm["score"] if fm["is_fast_mover"] else 0, 15)

    # Build base signal first (includes CRT+SMC+Flow+Momentum in composite)
    logger.debug(f"[engine] Building signal for {symbol} {timeframe} ({path}) "
                 f"flow={flow_score} momentum={momentum_score}")
    signal = build_signal(symbol, timeframe, crt, smc, candles, flow_score, momentum_score)

    if signal:
        # --- Timing (10 pts) ---
        timing_score = _score_timing(candles)
        # --- Risk/Reward (10 pts) ---
        rr_score, sl_quality = _score_rr(signal, smc, crt, candles)
        # --- Confluence Bonus (5 pts) ---
        confluence_score = _confluence_bonus(crt, smc, flow_score, momentum_score, timing_score, rr_score)

        # Fatal flaw check
        if _check_fatal_flaws(signal, flow, smc):
            logger.info(f"[engine] FATAL FLAW {symbol} {timeframe}: signal disqualified")
            signal["composite_score"] = 0
            signal["tier"] = "REJECTED"
            signal["scoring_breakdown"] = {
                "crt": crt.get("crt_score", 0),
                "smc": smc.get("smc_score", 0),
                "flow": flow_score,
                "momentum": momentum_score,
                "timing": timing_score,
                "rr": rr_score,
                "confluence": confluence_score,
                "fatal_flaw": True,
            }
        else:
            # Add Timing + R/R + Confluence to composite
            signal["composite_score"] = min(
                signal.get("composite_score", 0) + timing_score + rr_score + confluence_score,
                100
            )
            signal["scoring_breakdown"] = {
                "crt": crt.get("crt_score", 0),
                "smc": smc.get("smc_score", 0),
                "flow": flow_score,
                "momentum": momentum_score,
                "timing": timing_score,
                "rr": rr_score,
                "confluence": confluence_score,
                "fatal_flaw": False,
            }

        # Metadata
        signal["flow"] = flow
        signal["flow_score"] = flow_score
        signal["flow_boost"] = flow_score
        signal["fast_mover"] = fm
        signal["fast_mover_boost"] = momentum_score
        signal["momentum_score"] = momentum_score

        # Tier assignment based on new 100-pt composite
        composite = signal["composite_score"]
        if composite >= TIER_SNIPER_SCORE:
            signal["tier"] = "SNIPER"
        elif composite >= TIER_OPPORTUNITY_SCORE:
            signal["tier"] = "OPPORTUNITY"
        elif composite >= TIER_WATCH_SCORE:
            signal["tier"] = "WATCH"
        elif composite >= TIER_WEAK_SCORE:
            signal["tier"] = "WEAK"
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


# ── D1 Scoring Helpers ──────────────────────────────────────────────────

def _score_timing(candles: list) -> int:
    """Institutional Timing — 10 pts max.

    Killzone alignment (4 pts):
      - London open (08:00-11:00 UTC) OR NY open (13:30-16:30 UTC) = 4 pts
      - London close (10:30-12:00 UTC) = 2 pts
      - Asian session = 0 pts

    Session quality (3 pts):
      - High volatility (first hour of session, macro news overlap) = 3 pts
      - Normal = 2 pts
      - Low volatility = 1 pt

    Recency (3 pts):
      - Setup formed within last 1-2 bars = 3 pts
      - Setup 3-5 bars old = 2 pts
      - Setup 6-10 bars old = 1 pt
      - Setup 10+ bars old = 0 pts
    """
    from backend.helpers.session import get_session_at, session_score
    from datetime import datetime, timezone

    ts = int(datetime.now(timezone.utc).timestamp())
    session = get_session_at(ts)
    utc_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour + datetime.fromtimestamp(ts, tz=timezone.utc).minute / 60.0

    # Killzone alignment (4 pts max)
    if (KILLZONE_LONDON_START <= utc_hour < KILLZONE_LONDON_END or
        KILLZONE_NY_START <= utc_hour < KILLZONE_NY_END):
        killzone_score = TIMING_KILLZONE_MAX  # 4
    elif KILLZONE_LONDON_CLOSE_START <= utc_hour < KILLZONE_LONDON_CLOSE_END:
        killzone_score = 2
    else:
        killzone_score = 0

    # Session quality (3 pts max)
    raw_session_score = session_score(session)
    session_quality_score = min(raw_session_score, TIMING_SESSION_MAX)  # capped at 3

    # Recency factor (TIMING_DAYS_MAX pts) — how recent is the setup?
    recency_score = 0
    if candles and len(candles) >= 2:
        bars_since_setup = _estimate_setup_age(candles, session)
        if bars_since_setup <= 2:
            recency_score = TIMING_DAYS_MAX  # 3 pts — fresh setup
        elif bars_since_setup <= 5:
            recency_score = 2
        elif bars_since_setup <= 10:
            recency_score = 1
        # else 0

    return min(killzone_score + session_quality_score + recency_score, 10)


def _estimate_setup_age(candles: list, session: str) -> int:
    """Estimate how many bars ago the current setup began.

    Heuristic: count bars since the last significant directional move
    in the current session. A fresh setup (< 3 bars) is more reliable.
    """
    if not candles or len(candles) < 3:
        return 999

    recent = candles[-10:]  # look at last 10 bars
    directional_bars = 0
    for i in range(len(recent) - 1, 0, -1):
        body = abs(recent[i].close - recent[i].open)
        body_pct = body / recent[i].close * 100 if recent[i].close > 0 else 0
        if body_pct >= 0.02:  # meaningful body movement
            directional_bars += 1
        else:
            break

    return directional_bars


def _score_rr(signal: dict, smc: dict, crt: dict, candles: list) -> tuple:
    """Risk/Reward scoring — 10 pts max.

    R:R ratio (6 pts):
      - 3.0:1+ = 6, 2.5:1 = 5, 2.0:1 = 3, 1.5:1 = 1, <1.5:1 = 0

    Structural stop quality (4 pts):
      - Stop beyond OB + FVG = 4
      - Stop beyond OB or FVG = 3
      - Stop beyond swing point = 2
      - Stop is arbitrary = 0
    """
    if not signal:
        return 0, 0

    rr = signal.get("rr", 0)
    if rr >= 3.0:
        rr_score = RR_SCORE_MAX
    elif rr >= 2.5:
        rr_score = 5
    elif rr >= 2.0:
        rr_score = 3
    elif rr >= 1.5:
        rr_score = 1
    else:
        rr_score = 0

    # Structural stop quality
    sl_quality = 0
    ob = smc.get("ob") or crt.get("ob")
    fvg = smc.get("fvg") or crt.get("fvg")
    sl = signal.get("stop_loss", 0)
    entry = signal.get("entry", 0)
    direction = signal.get("direction", "BULLISH")

    if ob and fvg and sl > 0 and entry > 0:
        # Check if SL is beyond both OB and FVG
        ob_zone = ob.get("zone", "")
        ob_low = ob.get("low", 0)
        ob_high = ob.get("high", 0)
        fvg_top = fvg.get("top", 0)
        fvg_bot = fvg.get("bottom", 0)

        if direction == "BULLISH":
            beyond_ob = sl < ob_low if ob_low > 0 else False
            beyond_fvg = sl < fvg_bot if fvg_bot > 0 else False
        else:
            beyond_ob = sl > ob_high if ob_high > 0 else False
            beyond_fvg = sl > fvg_top if fvg_top > 0 else False

        if beyond_ob and beyond_fvg:
            sl_quality = 4
        elif beyond_ob or beyond_fvg:
            sl_quality = 3

    # If no OB/FVG data, check if SL is beyond a swing point
    if sl_quality == 0 and sl > 0 and entry > 0:
        from backend.vsp_helpers import detect_swing_points
        swings = detect_swing_points(candles)
        if direction == "BULLISH":
            swing_lows = swings.get("swing_lows", [])
            for sw in swing_lows:
                sw_price = sw.get("price", 0) if isinstance(sw, dict) else sw
                if sl < sw_price < entry:
                    sl_quality = 2
                    break
        else:
            swing_highs = swings.get("swing_highs", [])
            for sw in swing_highs:
                sw_price = sw.get("price", 0) if isinstance(sw, dict) else sw
                if sl > sw_price > entry:
                    sl_quality = 2
                    break

    return rr_score, sl_quality


def _confluence_bonus(crt: dict, smc: dict, flow_score: int, momentum_score: int,
                       timing_score: int, rr_score: int) -> int:
    """Confluence Bonus — 5 pts max.

    Count satisfied independent factors:
      1. CRT quality >= 14/25
      2. SMC confluence >= 16/25
      3. Flow confirmation >= 8/15
      4. Momentum >= 8/15
      5. Timing >= 6/10
      6. R:R >= 2.5:1 (rr_score >= 5)
    """
    factors = 0
    if crt.get("crt_score", 0) >= 14:
        factors += 1
    if smc.get("smc_score", 0) >= 16:
        factors += 1
    if flow_score >= 8:
        factors += 1
    if momentum_score >= 8:
        factors += 1
    if timing_score >= 6:
        factors += 1
    if rr_score >= 5:
        factors += 1

    return min(factors, CONFLUENCE_MAX)


def _check_fatal_flaws(signal: dict, flow: dict, smc: dict) -> bool:
    """Check fatal flaws — auto-disqualify regardless of other scores.

    Returns True if any fatal flaw is detected.
    """
    if not signal:
        return True

    # 1. R:R < 1.5:1 — insufficient reward for risk
    if signal.get("rr", 0) < MIN_RR:
        return True

    # 2. No structural stop defined
    sl = signal.get("stop_loss", 0)
    entry = signal.get("entry", 0)
    if sl <= 0 or entry <= 0:
        return True

    # 3. Sweep reversal opposing signal direction — if flow detected a sweep
    #    in the OPPOSITE direction of the proposed signal, that's a fatal conflict.
    triggers = flow.get("triggers", [])
    signal_dir = signal.get("direction", "")
    for trigger in triggers:
        t_name = trigger.get("name", "").lower()
        if signal_dir == "BULLISH" and "bearish" in t_name:
            return True
        if signal_dir == "BEARISH" and "bullish" in t_name:
            return True

    # 4. MSB (Market Structure Break) opposing signal direction
    msb = smc.get("msb", {})
    msb_type = msb.get("type", "NONE")
    if msb_type != "NONE":
        if msb_type == "BEARISH" and signal_dir == "BULLISH":
            return True
        if msb_type == "BULLISH" and signal_dir == "BEARISH":
            return True

    return False

