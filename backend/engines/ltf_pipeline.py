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
from backend.helpers.impulse_context import synth_crt_score, build_smc_only_context
from backend.helpers.candle_math import atr, calc_envelope, _get
from backend.engines.ltf_scanner import detect_nascent_move, calculate_entry_precision
from backend.vsp_helpers import detect_swing_points
from backend.config import (
    MIN_ATR_PERCENT, ADAPTIVE_ATR_MIN_ABSOLUTE, MIN_RANGE_MULTIPLIER,
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE, TIER_WEAK_SCORE,
    D2_FLOW_SCORE_MAX, SMC_SCORE_MAX,
    HTF_CONTEXT_SAME, HTF_CONTEXT_NEUTRAL, HTF_CONTEXT_OPPOSING,
    HTF_CONTEXT_NO_DATA, HTF_CONTEXT_MAX, HTF_CONTEXT_MIN,
    CONFLUENCE_MAX, D2_MIN_ENTRY_PRECISION, D2_MIN_FLOW, D2_MIN_MOMENTUM,
    IGNORE_MIN_SCORE, TYPE_B_MIN_D2_SCORE, TYPE_B_ENTRY_PRECISION_GATE,
)
import logging

logger = logging.getLogger("judah.ltf_pipeline")

_FALLBACK_MIN_CONFIDENCE = 30  # weighted fallback threshold (was binary MSB + SMC≥15)

# Stage counters for pipeline bottleneck analysis
_stage_stats = {}


def _count_stage(stage_name: str):
    """Increment a stage counter (for pipeline bottleneck debugging)."""
    _stage_stats[stage_name] = _stage_stats.get(stage_name, 0) + 1


def _log_stage_summary():
    """Log a summary of how many coins passed each stage (called from engine)."""
    total = _stage_stats.get("candidate_pass", 0)
    stages = [
        ("candidate_pass", "candidates"),
        ("flow_gate_pass", "flow_gate"),
        ("crt_smc_pass", "crt_smc"),
        ("fatal_flaw_pass", "fatal_flaws"),
        ("scoring_pass", "scoring"),
        ("final_signal", "final"),
    ]
    parts = [f"{_stage_stats.get(k, 0)}/{total} {label}" for k, label in stages]
    logger.info(f"[ltf_pipeline] STAGE COUNTS: {' → '.join(parts)}")

    # Log specific fatal flaw kills
    fatal_types = ["fatal_no_structure_no_precision", "fatal_delta_opposing",
                   "fatal_low_volume_key_candle", "fatal_entry_far_from_ob"]
    for ft in fatal_types:
        count = _stage_stats.get(ft, 0)
        if count > 0:
            logger.info(f"[ltf_pipeline]   {ft.replace('fatal_', '')}: {count}")
    _stage_stats.clear()


def _reset_stage_stats():
    _stage_stats.clear()


# _synth_crt_score and _build_smc_only_context are in helpers/impulse_context.py


def _check_d2_fatal_flaws(candles: list, smc: dict, flow: dict) -> list:
    """D2 fatal flaws — auto-disqualify before scoring.

    Returns list of flaw strings (empty = no flaws).
    1. No structure + no entry precision
    2. Delta opposing 2+ consecutive candles
    3. Volume < 1.0x avg on key candle
    4. Entry > 2% past OB/FVG zone
    """
    flaws = []

    # Flaw 1: No structure + no entry precision
    has_structure = (
        (smc.get("msb") or {}).get("confirmed", False)
        or (smc.get("choch") or {}).get("detected", False)
        or bool(smc.get("ob"))
        or bool(smc.get("fvg"))
    )
    has_precision = flow.get("ob_proximity", False) or flow.get("fvg_proximity", False)
    if not has_structure and not has_precision:
        flaws.append("no_structure_no_precision")

    # Flaw 2: Delta opposing 2+ consecutive candles
    delta_history = flow.get("delta_history", [])
    opp_count = 0
    signal_dir = flow.get("direction", "NEUTRAL")
    for d in delta_history[-3:]:  # check last 3 candles
        if signal_dir == "BULLISH" and d < 0:
            opp_count += 1
        elif signal_dir == "BEARISH" and d > 0:
            opp_count += 1
    if opp_count >= 2:
        flaws.append(f"delta_opposing_{opp_count}_candles")

    # Flaw 3: Volume < 0.3x avg on key candle = low conviction
    if candles and len(candles) >= 3:
        closed = candles  # Use all candles (dict-based in tests, object-based in production)
        if len(closed) >= 2:
            vol_avg = sum(_get(c, 'volume') for c in closed[-20:]) / min(len(closed[-20:]), 20)
            last_vol = _get(closed[-1], 'volume')
            # Volume must be at least 0.3x avg on key candle.
            # Below 30% avg = low conviction entry (was <1.0x = way too strict, blocking all signals)
            if vol_avg > 0 and last_vol < vol_avg * 0.3:
                flaws.append("low_volume_key_candle")

    # Flaw 4: Entry > 2% past OB/FVG zone
    last_price = _get(candles[-1], 'close') if candles else 0
    ob = smc.get("ob")
    if ob and last_price > 0:
        ob_high = ob.get("high", 0)
        ob_low = ob.get("low", 0)
        if ob_high > 0 and ob_low > 0:
            ob_mid = (ob_high + ob_low) / 2
            deviation = abs(last_price - ob_mid) / ob_mid * 100
            if deviation > 5.0:
                flaws.append(f"entry_far_from_ob_{deviation:.1f}%")

    return flaws



async def scan_ltf_pipeline(symbol: str, timeframe: str = "15M") -> dict | None:
    """D2's own 4-layer pipeline — independent from D1.

    Flow → CRT → SMC → Momentum → Signal Builder
    Falls back to SMC-only for impulse coins.
    """
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < 25:
        return None

    # ── Data Quality Gate (D2 pipeline) ──────────────────────────────
    from backend.data_quality_gate import validate_candles
    quality = validate_candles(candles, timeframe)
    if quality.state in ("INVALID", "GAPPED", "MISSING", "STALE"):
        return None
    # DEGRADED and INCOMPLETE still proceed — partial 15M data is acceptable

    last_price = _get(candles[-1], 'close')
    atr_val = atr(candles)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0
    if atr_pct < MIN_ATR_PERCENT or atr_val < ADAPTIVE_ATR_MIN_ABSOLUTE:
        return None

    env = calc_envelope(candles, 50)
    range_size = env.get('range_size', 0)
    if range_size < atr_val * MIN_RANGE_MULTIPLIER:
        return None

    _count_stage("candidate_pass")

    # FLOW GATE
    swings = detect_swing_points(candles[-30:])
    btc_candles = market_data.get_candles("BTCUSDT", timeframe)
    flow = analyze_flow(symbol, candles, swings, timeframe, btc_candles)
    fast = detect_fast_mover(candles, swings)

    if not flow["is_flowing"] and not fast["is_fast_mover"]:
        return None

    _count_stage("flow_gate_pass")

    # PRIMARY PATH: CRT + SMC
    crt = run_crt(candles)

    if crt:
        smc = run_smc(candles, crt)
        if not smc:
            return None
        path = "CRT+SMC"
    else:
        # FALLBACK: Weighted confidence for impulse coins
        fallback_crt = build_smc_only_context(candles)
        if not fallback_crt:
            return None
        smc = run_smc(candles, fallback_crt)
        if not smc:
            return None

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

        if fallback_score < _FALLBACK_MIN_CONFIDENCE:
            return None

        crt = fallback_crt
        path = "SMC-ONLY"

    _count_stage("crt_smc_pass")

    # D2 FATAL FLAW CHECK (auto-disqualify before scoring)
    # Populate flow proximity flags from SMC data so the precision check is accurate
    ob = smc.get("ob")
    fvg_zone = smc.get("fvg")
    last_price = _get(candles[-1], 'close') if candles else 0
    if ob and last_price > 0:
        ob_low = ob.get("low", 0)
        ob_high = ob.get("high", 0)
        if ob_low and ob_high:
            ob_mid = (ob_low + ob_high) / 2
            flow["ob_proximity"] = (ob_low <= last_price <= ob_high) or \
                (abs(last_price - ob_mid) / ob_mid * 100 <= 1.5)
    if fvg_zone and last_price > 0:
        fvg_bot = fvg_zone.get("bottom", 0)
        fvg_top = fvg_zone.get("top", 0)
        if fvg_bot and fvg_top:
            fvg_mid = (fvg_bot + fvg_top) / 2
            flow["fvg_proximity"] = (fvg_bot <= last_price <= fvg_top) or \
                (abs(last_price - fvg_mid) / fvg_mid * 100 <= 1.5)

    fatal_flaws = _check_d2_fatal_flaws(candles, smc, flow)
    if fatal_flaws:
        for f in fatal_flaws:
            _count_stage(f"fatal_{f}")
        logger.info(f"[ltf_pipeline] FATAL FLAW {symbol}: {fatal_flaws}")
        return None

    _count_stage("fatal_flaw_pass")

    # === D2 100-POINT SCORING ===
    # D2: Entry Precision(20) + LTF Structure(20) + Flow(15) + Momentum(15)
    #     + Nascent Move(10) + HTF Context(10) + Timing(5) + Confluence(5) = 100
    fm = detect_fast_mover(candles, swings)
    crt["crt_score"] = min(crt.get("crt_score", 0), 20)  # Entry Precision: 20 max
    smc["smc_score"] = min(smc.get("smc_score", 0), SMC_SCORE_MAX)
    flow_score = min(flow["boost"], D2_FLOW_SCORE_MAX)
    momentum_score = min(fm["score"] if fm["is_fast_mover"] else 0, 15)

    _count_stage("scoring_pass")

    # HTF Context (10 pts)
    htf_context_score = _score_htf_context(symbol, crt, candles)

    # --- Nascent Move (15 pts) ---
    d1_dir = _get_d1_direction(symbol)
    nascent = detect_nascent_move(candles, crt.get("displacement", {}).get("crt_trade_direction", "BULLISH"), d1_dir)
    nascent_score = _score_nascent_move(nascent)

    # --- Timing (5 pts) ---
    timing_score = _score_timing_d2(candles)

    # --- Confluence Bonus (5 pts) ---
    confluence_score = _confluence_bonus_d2(
        crt.get("crt_score", 0), smc.get("smc_score", 0),
        flow_score, momentum_score, nascent_score, htf_context_score, timing_score
    )

    logger.debug(f"[ltf_pipeline] D2 scores: CRT={crt['crt_score']} SMC={smc['smc_score']} "
                 f"Flow={flow_score} Mom={momentum_score} HTF={htf_context_score} "
                 f"Nascent={nascent_score} Timing={timing_score} Confluence={confluence_score}")

    logger.debug(f"[ltf_pipeline] Building {symbol} ({path}) flow={flow_score} momentum={momentum_score}")
    signal = build_signal(symbol, timeframe, crt, smc, candles, flow_score, momentum_score)

    if signal:
        signal["flow_score"] = flow_score
        signal["fast_mover_boost"] = momentum_score
        signal["momentum_score"] = momentum_score
        signal["htf_context"] = htf_context_score
        signal["nascent_move"] = nascent.get("nascent_move", False)
        signal["nascent_conditions"] = nascent.get("conditions_met", 0)
        signal["nascent_partial"] = nascent.get("partial", False)
        signal["nascent_score"] = nascent_score

        # Add D2 scoring breakdown to composite
        # build_signal composite includes CRT+SMC+Flow+Momentum (up to ~80 pts)
        # We add HTF Context + Nascent Move + Timing + Confluence to reach 100 max
        signal["composite_score"] = min(
            signal.get("composite_score", 0) + htf_context_score + nascent_score + timing_score + confluence_score,
            100
        )

        # Entry precision (CRT score serves as the entry timing component)
        entry_precision = crt.get("crt_score", 0)
        signal["entry_precision"] = entry_precision
        signal["entry_precision_raw"] = entry_precision

        # Enforce minimum sub-score gates
        ep_pass = entry_precision >= D2_MIN_ENTRY_PRECISION
        flow_pass = flow_score >= D2_MIN_FLOW
        mom_pass = momentum_score >= D2_MIN_MOMENTUM

        signal["threshold_ep_pass"] = ep_pass
        signal["threshold_flow_pass"] = flow_pass
        signal["threshold_momentum_pass"] = mom_pass
        signal["thresholds_passed"] = all([ep_pass, flow_pass, mom_pass])

        # Tier assignment
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

        # Downgrade to REJECTED if sub-thresholds fail
        if not signal["thresholds_passed"] and composite < IGNORE_MIN_SCORE:
            signal["tier"] = "REJECTED"

        # Scoring breakdown for frontend
        signal["scoring_breakdown"] = {
            "entry_precision": crt.get("crt_score", 0),
            "ltf_structure": smc.get("smc_score", 0),
            "flow": flow_score,
            "nascent_move": nascent_score,
            "htf_context": htf_context_score,
            "momentum": momentum_score,
            "timing": timing_score,
            "confluence": confluence_score,
            "max_entry_precision": 20,
        }

        signal["engine_path"] = path
        signal["flow_direction"] = flow["direction"]
        signal["killzone"] = flow["killzone"]
        _count_stage("final_signal")
        logger.info(f"[ltf_pipeline] SIGNAL {symbol} {timeframe}: {signal['tier']} score={signal['composite_score']} "
                     f"dir={signal['direction']} rr={signal['rr']:.1f} path={path} "
                     f"crt={signal['crt_score']} smc={signal['smc_score']} flow={flow_score} mom={momentum_score} "
                     f"EP={entry_precision:.0f}/{D2_MIN_ENTRY_PRECISION} nascent={nascent.get('conditions_met',0)}/5")

        # === EvidenceRecord: log D2 structural findings ===
        _log_ltf_evidence(symbol, timeframe, signal, crt, smc, flow, nascent, path)
    else:
        logger.debug(f"[ltf_pipeline] SKIP {symbol}: build_signal returned None")
    return signal


# ── EvidenceRecord Logger ──────────────────────────────────────────────

async def _log_ltf_evidence_async(symbol: str, timeframe: str, signal: dict,
                                   crt: dict, smc: dict, flow: dict, nascent: dict, path: str):
    """Append EvidenceRecords for D2 structural findings to evidence_store."""
    from backend.evidence_store import evidence_store, next_evidence_id
    from backend.evidence_record import EvidenceCategory, EvidenceStrength
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).timestamp()
    snap_id = state_store.last_snapshot_id
    direction = signal.get("direction", "NEUTRAL")
    last_price = signal.get("entry", 0)
    records: list = []

    # MSB break evidence
    msb = crt.get("msb", smc.get("msb", {}))
    if msb and msb.get("type", "NONE") != "NONE":
        strength = EvidenceStrength.STRONG if msb.get("confirmed", False) else EvidenceStrength.MODERATE
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.MSB_BREAK,
            symbol=symbol, timeframe=timeframe,
            price=last_price, strength=strength,
            direction=msb.get("type", direction).upper(),
            confidence=0.8 if msb.get("confirmed") else 0.5,
            candle_time=now, detected_at=now,
            source="ltf_pipeline.crt", snapshot_id=snap_id,
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
            source="ltf_pipeline.smc", snapshot_id=snap_id,
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
            source="ltf_pipeline.smc", snapshot_id=snap_id,
            details={"top": fvg.get("top", 0), "bottom": fvg.get("bottom", 0),
                     "proximity": fvg.get("proximity", 999)},
        ))

    # Nascent move evidence
    if nascent.get("nascent_move"):
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.CANDLE_PATTERN,
            symbol=symbol, timeframe=timeframe,
            price=last_price,
            strength=EvidenceStrength.STRONG if nascent.get("conditions_met", 0) >= 4 else EvidenceStrength.MODERATE,
            direction=direction,
            confidence=min(nascent.get("conditions_met", 0) / 5, 1.0),
            candle_time=now, detected_at=now,
            source="ltf_pipeline.nascent", snapshot_id=snap_id,
            details={"conditions_met": nascent.get("conditions_met", 0),
                     "partial": nascent.get("partial", False)},
        ))

    # Flow trigger evidence
    for trigger in flow.get("trgers", flow.get("triggers", []))[:3]:
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.VOLUME_PROFILE,
            symbol=symbol, timeframe=timeframe,
            price=last_price, strength=EvidenceStrength.MODERATE,
            direction=direction, confidence=0.6,
            candle_time=now, detected_at=now,
            source="ltf_pipeline.flow", snapshot_id=snap_id,
            details={"trigger": trigger.get("name", "unknown"),
                     "boost": trigger.get("boost", 0)},
        ))

    if records:
        evidence_ids = []
        for rec in records:
            eid = await evidence_store.append(rec)
            evidence_ids.append(eid)
        signal["evidence_ids"] = evidence_ids
        logger.debug(f"[ltf_pipeline] Logged {len(records)} evidence records for {symbol}")


def _log_ltf_evidence(symbol: str, timeframe: str, signal: dict,
                       crt: dict, smc: dict, flow: dict, nascent: dict, path: str):
    """Fire-and-forget evidence logging (async, non-blocking for scan())."""
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(_log_ltf_evidence_async(symbol, timeframe, signal,
                                                  crt, smc, flow, nascent, path))
    except RuntimeError:
        pass


# ── D2 Scoring Helpers ──────────────────────────────────────────────────

def _score_htf_context(symbol: str, crt: dict, candles: list) -> int:
    """HTF Context Bonus — 10 pts max (structured replacement for htf_bonus accumulation).

    Same direction as D1: +5
    D1 neutral (range-bound): +2
    Opposing direction to D1: -5
    No D1 data: +3
    """
    from backend.config import TIMEFRAMES_HTF
    from backend.signal_store import signal_store as sig_store

    d1_dir = ""
    for htf in TIMEFRAMES_HTF:
        d1_sig = sig_store.get(symbol, htf)
        if d1_sig and d1_sig.get("composite_score", 0) > 0:
            d1_dir = d1_sig.get("direction", "")
            break

    d2_dir = crt.get("displacement", {}).get("crt_trade_direction", "")

    if not d1_dir:
        return HTF_CONTEXT_NO_DATA
    if d1_dir == d2_dir and d2_dir:
        return HTF_CONTEXT_SAME
    if d1_dir != d2_dir and d1_dir and d2_dir:
        return HTF_CONTEXT_OPPOSING
    return HTF_CONTEXT_NEUTRAL


def _get_d1_direction(symbol: str) -> str:
    """Get D1 direction for nascent move detection."""
    from backend.config import TIMEFRAMES_HTF
    from backend.signal_store import signal_store as sig_store
    for htf in TIMEFRAMES_HTF:
        d1_sig = sig_store.get(symbol, htf)
        if d1_sig and d1_sig.get("composite_score", 0) > 0:
            return d1_sig.get("direction", "")
    return ""




def _score_nascent_move(nascent: dict) -> int:
    """Score Nascent Move confidence for D2 scoring rubric.

    5/5 conditions = 10 pts
    3-4 conditions = 5 pts
    <3 conditions = 0 pts
    """
    conditions = nascent.get("conditions_met", 0)
    if conditions >= 5:
        return 10
    elif conditions >= 3:
        return 5
    return 0


def _score_timing_d2(candles: list) -> int:
    """D2 Timing — 5 pts max (simplified from D1's 10 pts)."""
    from backend.helpers.session import get_session_at, session_score
    from backend.config import (
        KILLZONE_LONDON_START, KILLZONE_LONDON_END,
        KILLZONE_NY_START, KILLZONE_NY_END,
        KILLZONE_LONDON_CLOSE_START, KILLZONE_LONDON_CLOSE_END,
        TIMING_KILLZONE_MAX, TIMING_SESSION_MAX,
    )
    from datetime import datetime, timezone

    ts = int(datetime.now(timezone.utc).timestamp())
    session = get_session_at(ts)
    utc_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour + datetime.fromtimestamp(ts, tz=timezone.utc).minute / 60.0

    # Killzone bonus (3 pts)
    if (KILLZONE_LONDON_START <= utc_hour < KILLZONE_LONDON_END or
        KILLZONE_NY_START <= utc_hour < KILLZONE_NY_END):
        killzone_score = 3
    elif KILLZONE_LONDON_CLOSE_START <= utc_hour < KILLZONE_LONDON_CLOSE_END:
        killzone_score = 2
    else:
        killzone_score = 0

    # Session quality (2 pts)
    session_score_pts = min(session_score(session), 2)

    return min(killzone_score + session_score_pts, 5)


def _confluence_bonus_d2(crt_score: int, smc_score: int, flow_score: int,
                          momentum_score: int, nascent_score: int,
                          htf_context: int, timing_score: int) -> int:
    """D2 Confluence Bonus — 5 pts max."""
    factors = 0
    if crt_score >= 14:         # CRT quality >= 14/20
        factors += 1
    if smc_score >= 16:         # SMC >= 16/25
        factors += 1
    if flow_score >= 8:         # Flow >= 8/15
        factors += 1
    if momentum_score >= 8:     # Momentum >= 8/15
        factors += 1
    if nascent_score >= 5:      # Nascent >= 5/10
        factors += 1
    if htf_context >= 3:        # HTF context >= 3/10
        factors += 1

    return min(factors, CONFLUENCE_MAX)
