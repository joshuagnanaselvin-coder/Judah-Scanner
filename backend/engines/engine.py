"""Single engine file — flow-first, structure-confirm, CRT-time.

CRITICAL: NO FILTERING. Every coin that enters must produce a result.
All 500 coins scan, all results flow to D3 and frontend.
"REJECTED" / score=0 is a valid result — it means "no actionable setup."

Pipeline order (institutional):
  1. Data quality:     Validate candles (warn, don't skip)
  2. Volatility gate:  ATR check (penalize score, don't skip)
  3. Range size gate:  Range check (penalize score, don't skip)
  4. Flow analysis:    Volume/sweep/VWAP/RS analysis (contributes to scoring)
  5. Structure:        CRT + SMC (fallback to SMC-only if CRT missing)
  6. Signal build:     Entry / SL / TP / RR
  7. Scoring:          100-pt: CRT(20) + SMC(25) + Flow(15) + Momentum(15) + Timing(10) + R/R(10) + Confluence(5)
  8. Tier assignment:  SNIPER/OPPORTUNITY/WATCH/WEAK/REJECTED
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
_FALLBACK_MIN_CONFIDENCE = 15  # weighted fallback threshold (replaces binary MSB + SMC≥15)
# Lowered from 30 → 15: HTF (1H/4H/1D) rarely lights all 6 components at once.
# 15 lets solid setups through (e.g. MSB(8) + OB(5) + flow(8) = 21, or OB+liq+sweep).

# Penalty constants for gate failures (score reductions, NOT skips)
_ATR_PENALTY = 10          # Points deducted when ATR too low
_RANGE_PENALTY = 10        # Points deducted when range too small
_FLOW_PENALTY = 15         # Points deducted when no flow triggers and not fast mover
_NO_CANDLES_SCORE = 0      # Score when no candles (REJECTED)


async def scan(symbol: str, timeframe: str) -> dict:
    """Run full CRT -> SMC -> Signal pipeline for one coin on one timeframe.

    NO FILTERING — every coin produces a result (REJECTED / 0 score is still a result).
    Falls back to SMC-only path if CRT returns None (impulse / fast-moving coins
    where the strict 5-step CRT consolidation pattern doesn't exist yet).
    """
    penalties = 0  # Accumulate score penalties from gate failures (initialized FIRST)

    # === Candle retrieval + early exit for empty data ===
    candles = market_data.get_candles(symbol, timeframe)
    candle_count = len(candles) if candles else 0
    last_price = _get(candles[-1], 'close') if candles else 0

    # Guard: empty candles — build minimal REJECTED, no analysis attempted
    if candle_count == 0:
        return _build_minimal_rejected(symbol, timeframe, None, None, [], {
            "boost": 0, "is_flowing": False, "direction": "NEUTRAL",
            "triggers": [], "killzone": {"zone": "UNKNOWN"},
        }, penalties, "MISSING")

    # === Data Quality Check — warn + penalize, never skip ===
    quality_state = "VALID"
    if candles and candle_count >= 25:
        from backend.data_quality_gate import validate_candles
        quality = validate_candles(candles, timeframe)
        if quality.state in ("INVALID", "GAPPED", "MISSING"):
            logger.warning(f"[engine] QUALITY_WARN {symbol} {timeframe}: {quality.state} issues={quality.issues}")
            penalties += 25
            quality_state = quality.state
        elif quality.state == "STALE":
            logger.debug(f"[engine] STALE {symbol} {timeframe}: age={quality.last_candle_age_sec:.0f}s — penalty -15")
            penalties += 15
            quality_state = "STALE"
        # DEGRADED and INCOMPLETE proceed without penalty
    elif candle_count < 25:
        logger.debug(f"[engine] NO_CANDLES {symbol} {timeframe}: {candle_count} candles")
        penalties += 100  # Guarantees score=0 → REJECTED (still written!)

    # Volatility check — penalize score instead of skipping
    atr_val = atr(candles)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0
    if atr_pct < MIN_ATR_PERCENT or atr_val < ADAPTIVE_ATR_MIN_ABSOLUTE:
        logger.debug(f"[engine] ATR_LOW {symbol} {timeframe}: {atr_val:.6f} ({atr_pct:.3f}%) — penalty -{_ATR_PENALTY}")
        penalties += _ATR_PENALTY

    # Range size check — penalize score instead of skipping
    env = calc_envelope(candles, 50)
    range_size = env.get('range_size', 0)
    if range_size < atr_val * MIN_RANGE_MULTIPLIER:
        logger.debug(f"[engine] RANGE_SMALL {symbol} {timeframe}: {range_size:.6f} — penalty -{_RANGE_PENALTY}")
        penalties += _RANGE_PENALTY

    # === FLOW ANALYSIS — no skipping, contributes to scoring ===
    # No flow = low score penalty, but coin still gets a REJECTED result.
    swings = detect_swing_points(candles[-30:])
    btc_candles = market_data.get_candles("BTCUSDT", timeframe)
    if btc_candles:
        btc_quality = validate_candles(btc_candles, timeframe)
        if btc_quality.state == "INVALID":
            btc_candles = None  # use None — flow analysis handles missing BTC gracefully
    flow = analyze_flow(symbol, candles, swings, timeframe, btc_candles)
    fast = detect_fast_mover(candles, swings)

    # === FLOW: penalize score for neutral coins, never skip ===
    if not flow["is_flowing"] and not fast["is_fast_mover"]:
        logger.debug(f"[engine] FLAT {symbol} {timeframe}: no flow triggers — penalty -{_FLOW_PENALTY}")
        penalties += _FLOW_PENALTY

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
            logger.debug(f"[engine] SMC_NONE {symbol} {timeframe}: SMC returned None — using empty SMC")
            smc = {"smc_score": 0}
        logger.debug(f"[engine] SMC passed {symbol} {timeframe}: score={smc.get('smc_score',0)}")
        path = "CRT+SMC"
    else:
        # === FALLBACK PATH: SMC-only (impulse / fast-moving coins) ===
        logger.debug(f"[engine] CRT missing for {symbol} {timeframe} — trying SMC-only fallback")
        fallback_crt = build_smc_only_context(candles)
        if not fallback_crt:
            logger.debug(f"[engine] FALLBACK_EMPTY {symbol} {timeframe}: no CRT and no MSB direction — "
                         f"using SMC-only with empty context")
            fallback_crt = {"crt_score": 0, "trade_direction": None}
        smc = run_smc(candles, fallback_crt)
        if not smc:
            logger.debug(f"[engine] FALLBACK_EMPTY {symbol} {timeframe}: SMC-only fallback returned None — "
                         f"using empty SMC")
            smc = {"smc_score": 0}

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

        # FALLBACK CONFIDENCE → penalty (never skip — all coins flow)
        if fallback_score < _FALLBACK_MIN_CONFIDENCE:
            logger.debug(f"[engine] FALLBACK_LOW {symbol} {timeframe}: confidence {fallback_score} < {_FALLBACK_MIN_CONFIDENCE} — penalty -20")
            penalties += 20

        crt = fallback_crt
        path = "SMC-ONLY"

    # === D1 100-POINT SCORING ===
    # D1: CRT(20) + SMC(25) + Flow(15) + Momentum(15) + Timing(10) + R/R(10) + Confluence(5) = 100
    # Plus Conviction multiplier (up to 1.15x) when all 4 core dimensions are strong.
    fm = detect_fast_mover(candles, swings)
    crt["crt_score"] = min(crt.get("crt_score", 0), 20)  # D1 CRT capped at 20
    smc["smc_score"] = min(smc.get("smc_score", 0), SMC_SCORE_MAX)
    flow_score = min(flow["boost"], 15)
    momentum_score = min(fm["score"] if fm["is_fast_mover"] else 0, 15)

    # === IMPROVEMENT #2: Volume Profile scoring ===
    # Hedge fund logic: entries at the POC or value area edge have statistically
    # better outcomes than entries at the extremes. Score entries that land on
    # POC/+1SD or POC/-1SD.

    # Build base signal first (includes CRT+SMC+Flow+Momentum in composite)
    logger.debug(f"[engine] Building signal for {symbol} {timeframe} ({path}) "
                 f"flow={flow_score} momentum={momentum_score}")
    signal = build_signal(symbol, timeframe, crt, smc, candles, flow_score, momentum_score)

    if signal:
        # --- Timing (10 pts) ---
        timing_score = _score_timing(candles)
        # --- Risk/Reward (10 pts) ---
        rr_score, sl_quality = _score_rr(signal, smc, crt, candles)
        # --- Volume Profile alignment (3 pts) ---
        vp_score = _score_volume_profile(signal, crt)
        # --- Confluence Bonus (5 pts) ---
        confluence_score = _confluence_bonus(crt, smc, flow_score, momentum_score, timing_score, rr_score, vp_score)

        # Fatal flaw check — add penalty (never skip or hard-zero)
        fatal_flaw = _check_fatal_flaws(signal, flow, smc)
        fatal_flaw_penalty = 30 if fatal_flaw else 0  # Large penalty = natural REJECTED tier
        if fatal_flaw:
            logger.info(f"[engine] FATAL FLAW {symbol} {timeframe}: signal penalized -{fatal_flaw_penalty}")
            penalties += fatal_flaw_penalty

        # Add Timing + R/R + Confluence to composite (always runs)
        base_composite = signal.get("composite_score", 0) + timing_score + rr_score + confluence_score

        # === IMPROVEMENT #4: Conviction Multiplier ===
        # Hedge fund methodology: when all 4 core dimensions score ≥70% of their max,
        # it's a high-conviction signal. Apply multiplicative boost (not additive).
        # This prevents a 100-pt signal from being 4 mediocre scores; it rewards
        # STRONG scores across all dimensions (CRT ≥14, SMC ≥18, Flow ≥11, Momentum ≥11).
        crt_raw = crt.get("crt_score", 0)
        smc_raw = smc.get("smc_score", 0)
        conviction_mult = 1.0
        conviction_factors = 0
        if crt_raw >= 14:       # CRT ≥70% of 20
            conviction_factors += 1
        if smc_raw >= 18:       # SMC ≥72% of 25
            conviction_factors += 1
        if flow_score >= 11:    # Flow ≥73% of 15
            conviction_factors += 1
        if momentum_score >= 11: # Momentum ≥73% of 15
            conviction_factors += 1

        if conviction_factors >= 4:
            conviction_mult = 1.15       # All 4 strong: +15% multiplier
        elif conviction_factors >= 3:
            conviction_mult = 1.08       # 3 of 4: +8% multiplier
        elif conviction_factors >= 2:
            conviction_mult = 1.03       # 2 of 4: +3% minor boost

        final_composite = base_composite * conviction_mult
        final_composite = min(final_composite, 100)
        # Apply accumulated penalties from gate failures (ATR, range, flow, quality, fallback)
        final_composite = max(0, final_composite - penalties)
        signal["composite_score"] = round(final_composite, 1)
        signal["conviction_mult"] = round(conviction_mult, 3)
        signal["conviction_factors"] = conviction_factors
        signal["penalties"] = penalties  # Track what was deducted

        signal["scoring_breakdown"] = {
            "crt": crt.get("crt_score", 0),
            "smc": smc.get("smc_score", 0),
            "flow": flow_score,
            "momentum": momentum_score,
            "timing": timing_score,
            "rr": rr_score,
            "volume_profile": vp_score,
            "confluence": confluence_score,
            "conviction_mult": round(conviction_mult, 3),
            "conviction_factors": conviction_factors,
            "fatal_flaw": fatal_flaw,
            "penalties": penalties,
            "quality_state": quality_state,
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

        # === EvidenceRecord: log structural findings (awaited — no fire-and-forget) ===
        await _log_evidence_async(symbol, timeframe, signal, crt, smc, flow, path)
    elif signal is None:
        # build_signal returned None — create minimal REJECTED signal so coin still flows to D3
        logger.debug(f"[engine] BUILD_NONE {symbol} {timeframe}: building minimal REJECTED signal")
        signal = _build_minimal_rejected(symbol, timeframe, crt, smc, candles, flow, penalties, quality_state)

    return signal


def _build_minimal_rejected(symbol: str, timeframe: str, crt: dict, smc: dict,
                             candles: list, flow: dict, penalties: int,
                             quality_state: str) -> dict:
    """Create a minimal REJECTED signal when build_signal returns None.

    CRITICAL: Never skip a coin — all 500 must produce a result that flows to D3.
    This ensures every coin has a D1 tier entry, even if it's REJECTED.
    """
    last_price = _get(candles[-1], 'close') if candles else 0
    direction = "BULLISH"  # Default — D3 may override based on data

    # Build minimal SL/TP for completeness (even if 0)
    sl = last_price * 0.95 if last_price > 0 else 0  # Placeholder 5% below
    tp = last_price * 1.05 if last_price > 0 else 0  # Placeholder 5% above
    rr = abs((tp - last_price) / (last_price - sl)) if (last_price - sl) != 0 else 0

    return {
        "symbol": symbol,
        "engine": timeframe,
        "direction": direction,
        "tier": "REJECTED",
        "composite_score": 0,
        "entry": last_price,
        "stop_loss": sl,
        "take_profit_1": tp,
        "rr": round(rr, 2),
        "crt_score": crt.get("crt_score", 0) if crt else 0,
        "smc_score": smc.get("smc_score", 0) if smc else 0,
        "flow_score": flow.get("boost", 0),
        "scoring_breakdown": {
            "crt": crt.get("crt_score", 0) if crt else 0,
            "smc": smc.get("smc_score", 0) if smc else 0,
            "flow": flow.get("boost", 0),
            "momentum": 0,
            "timing": 0,
            "rr": 0,
            "confluence": 0,
            "fatal_flaw": False,
            "penalties": penalties,
            "quality_state": quality_state,
            "minimal_signal": True,
        },
        "engine_path": "NONE",
        "flow_direction": flow.get("direction", "NEUTRAL"),
        "killzone": flow.get("killzone", {"zone": "UNKNOWN"}),
    }


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
    from backend.session_regime import session_regime
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

    base_timing = min(killzone_score + session_quality_score + recency_score, 10)

    return base_timing


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
                       timing_score: int, rr_score: int, vp_score: int = 0) -> int:
    """Confluence Bonus — 5 pts max.

    Count satisfied independent factors:
      1. CRT quality >= 14/25
      2. SMC confluence >= 16/25
      3. Flow confirmation >= 8/15
      4. Momentum >= 8/15
      5. Timing >= 6/10
      6. R:R >= 2.5:1 (rr_score >= 5)
      7. Volume Profile edge entry (vp_score >= 2)
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
    if vp_score >= 2:
        factors += 1

    return min(factors, CONFLUENCE_MAX)


def _score_volume_profile(signal: dict, crt: dict) -> int:
    """Hedge fund VP scoring — value area edge entries score higher.

    Institutional logic:
      - Entry AT POC = neutral (0 pts) — fair value, no edge
      - Entry at POC ±0.5 SD (value area boundary) = +3 pts — institutional defense
      - Entry inside VA but not at POC = +1 pt — okay
      - Entry outside VA (>1.5 SD) = 0 pts — risky territory, outside value
      - VAH/VAL proximity = +2 pts — at edge of acceptance/rejection
    """
    vp = crt.get("volume_profile") or signal.get("volume_profile") or {}
    if not vp or vp.get("poc_price", 0) <= 0:
        return 0

    poc = vp.get("poc_price", 0)
    va_high = vp.get("va_high", 0)
    va_low = vp.get("va_low", 0)
    entry = signal.get("entry", 0)
    direction = signal.get("direction", "BULLISH")

    if entry <= 0 or poc <= 0:
        return 0

    # Calculate distance from POC and VA boundaries
    va_range = va_high - va_low if va_high > va_low else 0

    # Entry at POC (within 0.1% of POC price)
    if abs(entry - poc) / poc < 0.001:
        return 1  # At fair value — minimal edge

    if va_range > 0:
        # Position within value area (0-100%)
        if va_low <= entry <= va_high:
            pct_in_va = ((entry - va_low) / va_range) * 100

            # BULLISH: entries at lower VA edge (VAL) are best (deep discount)
            if direction == "BULLISH" and pct_in_va <= 25:
                return 3  # Value area low - deep discount entry
            elif direction == "BULLISH" and pct_in_va <= 40:
                return 2  # Lower half of value area - good risk/reward
            elif direction == "BEARISH" and pct_in_va >= 75:
                return 3  # Value area high - premium short entry
            elif direction == "BEARISH" and pct_in_va >= 60:
                return 2  # Upper half of value area - good short entry
            else:
                return 1  # Inside VA but not at edge

    # Entry outside VA — risky, no edge
    return 0


def _check_fatal_flaws(signal: dict, flow: dict, smc: dict) -> bool:
    """Check fatal flaws — auto-disqualify regardless of other scores.

    Returns True if any fatal flaw is detected.
    """
    if not signal:
        return True

    # 1. R:R < 1.5:1 — insufficient reward for risk
    rr = signal.get("rr", 0)
    if rr < MIN_RR:
        logger.info(f"[engine] FATAL FLAW {signal.get('symbol')} {signal.get('engine')}: RR={rr} < {MIN_RR}")
        return True

    # 2. No structural stop defined
    sl = signal.get("stop_loss", 0)
    entry = signal.get("entry", 0)
    if sl <= 0 or entry <= 0:
        logger.info(f"[engine] FATAL FLAW {signal.get('symbol')} {signal.get('engine')}: SL={sl} entry={entry} invalid")
        return True

    # 3. Flow trigger opposing signal direction.
    #    Only "sweep_reversal_*" triggers represent genuine directional conflict
    #    — e.g. a bullish sweep opposing a BEARISH signal. Relative-extreme
    #    triggers (rs_extreme_bearish in a BULLISH signal) are actually
    #    CONFLUENT because extreme bearish RSI = bullish reversal setup.
    triggers = flow.get("triggers", [])
    signal_dir = signal.get("direction", "")
    for trigger in triggers:
        t_name = trigger.get("name", "").lower()
        if "sweep_reversal" not in t_name:
            continue
        if signal_dir == "BULLISH" and "bearish" in t_name:
            logger.info(f"[engine] FATAL FLAW {signal.get('symbol')}: sweep_reversal_bearish opposes BULLISH signal")
            return True
        if signal_dir == "BEARISH" and "bullish" in t_name:
            logger.info(f"[engine] FATAL FLAW {signal.get('symbol')}: sweep_reversal_bullish opposes BEARISH signal")
            return True

    # 4. MSB (Market Structure Break) opposing signal direction.
    # NOTE: msb["type"] is "CHOCH" / "BOS" — direction lives in msb["direction"].
    msb = smc.get("msb", {})
    if msb.get("confirmed"):
        msb_dir = msb.get("direction", "")
        if msb_dir == "BEARISH" and signal_dir == "BULLISH":
            logger.info(f"[engine] FATAL FLAW {signal.get('symbol')}: BEARISH MSB opposes BULLISH signal")
            return True
        if msb_dir == "BULLISH" and signal_dir == "BEARISH":
            logger.info(f"[engine] FATAL FLAW {signal.get('symbol')}: BULLISH MSB opposes BEARISH signal")
            return True

    return False


# ── EvidenceRecord Logger ──────────────────────────────────────────────

async def _log_evidence_async(symbol: str, timeframe: str, signal: dict,
                               crt: dict, smc: dict, flow: dict, path: str):
    """Append EvidenceRecords for structural findings to evidence_store."""
    from backend.evidence_store import evidence_store, next_evidence_id
    from backend.evidence_record import EvidenceCategory, EvidenceRecord, EvidenceStrength
    from backend.state_store import state_store
    from datetime import datetime, timezone
    import asyncio

    now = datetime.now(timezone.utc).timestamp()
    snap_id = state_store.last_snapshot_id
    direction = signal.get("direction", "NEUTRAL")
    last_price = signal.get("entry", 0)

    records: list = []

    # MSB break evidence (None-safe: msb.type may be None for unconfirmed breaks)
    msb = smc.get("msb", {})
    if msb and msb.get("type") and msb.get("type", "NONE") != "NONE":
        strength = EvidenceStrength.STRONG if msb.get("confirmed", False) else EvidenceStrength.MODERATE
        msb_dir = (msb.get("direction") or direction or "NEUTRAL").upper()
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.MSB_BREAK,
            symbol=symbol, timeframe=timeframe,
            price=last_price, strength=strength,
            direction=msb_dir,
            confidence=0.8 if msb.get("confirmed") else 0.5,
            candle_time=now, detected_at=now,
            source="engine.crt", snapshot_id=snap_id,
            details={"confirmed": msb.get("confirmed", False), "path": path},
        ))

    # OB evidence
    ob = smc.get("ob", {})
    if ob and ob.get("high", 0) > 0:
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.ORDER_BLOCK,
            symbol=symbol, timeframe=timeframe,
            price=(ob.get("high", 0) + ob.get("low", 0)) / 2,
            strength=EvidenceStrength.STRONG if ob.get("strength", 0) >= 3 else EvidenceStrength.MODERATE,
            direction=direction, confidence=min(ob.get("strength", 0) / 5, 1.0),
            candle_time=now, detected_at=now,
            source="engine.smc", snapshot_id=snap_id,
            details={"ob_high": ob.get("high", 0), "ob_low": ob.get("low", 0),
                     "strength": ob.get("strength", 0)},
        ))

    # FVG evidence
    fvg = smc.get("fvg", {})
    if fvg and fvg.get("top", 0) > 0:
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.FAIR_VALUE_GAP,
            symbol=symbol, timeframe=timeframe,
            price=(fvg.get("top", 0) + fvg.get("bottom", 0)) / 2,
            strength=EvidenceStrength.STRONG if fvg.get("proximity", 999) <= 1.0 else EvidenceStrength.MODERATE,
            direction=direction, confidence=0.7,
            candle_time=now, detected_at=now,
            source="engine.smc", snapshot_id=snap_id,
            details={"top": fvg.get("top", 0), "bottom": fvg.get("bottom", 0),
                     "proximity": fvg.get("proximity", 999)},
        ))

    # Liquidity sweep evidence
    liq = smc.get("liquidity", {})
    if liq and liq.get("swept", False):
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.LIQUIDITY_POOL,
            symbol=symbol, timeframe=timeframe,
            price=liq.get("level", last_price),
            strength=EvidenceStrength.STRONG, direction=direction,
            confidence=0.7, candle_time=now, detected_at=now,
            source="engine.smc", snapshot_id=snap_id,
            details={"swept": True, "level": liq.get("level", 0)},
        ))

    # Flow trigger evidence
    for trigger in flow.get("trgers", flow.get("triggers", []))[:3]:
        t_name = trigger.get("name", "unknown")
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.VOLUME_PROFILE,
            symbol=symbol, timeframe=timeframe,
            price=last_price, strength=EvidenceStrength.MODERATE,
            direction=direction, confidence=0.6,
            candle_time=now, detected_at=now,
            source="engine.flow", snapshot_id=snap_id,
            details={"trigger": t_name, "boost": trigger.get("boost", 0)},
        ))

    if records:
        evidence_ids = []
        for rec in records:
            eid = await evidence_store.append(rec)
            evidence_ids.append(eid)
        signal["evidence_ids"] = evidence_ids
        logger.debug(f"[engine] Logged {len(records)} evidence records for {symbol}")
