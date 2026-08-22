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
    CONFLUENCE_MAX, D2_MIN_ENTRY_PRECISION, D2_MIN_FLOW, D2_MIN_MOMENTUM,
    IGNORE_MIN_SCORE, TYPE_B_MIN_D2_SCORE, TYPE_B_ENTRY_PRECISION_GATE,
)
import logging

logger = logging.getLogger("judah.ltf_pipeline")

_ATR_PENALTY = 5          # Points deducted when ATR too low (was 10)
_RANGE_PENALTY = 4        # Points deducted when range too small (was 8)
_FLOW_PENALTY = 5         # Points deducted when no flow (was 10)
_NO_SMC_PENALTY = 5       # Points deducted when SMC analysis fails (was 10)
_FALLBACK_PENALTY = 8     # Points deducted when fallback confidence too low (was 15)
_FATAL_FLAW_PENALTY = 8   # Points deducted per fatal flaw (was 20)
_NO_CANDLES_PENALTY = 10  # Points deducted when insufficient candles (was 20)

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

    # Flaw 3: Volume < 0.5x avg on key candle = low conviction
    # Use only closed candles — forming 15M candle naturally has lower volume
    # and would false-trigger this check.
    if candles and len(candles) >= 6:
        vol_avg = sum(_get(c, 'volume') for c in candles[-20:]) / min(len(candles[-20:]), 20)
        key_candles = [c for c in candles[-5:-1] if getattr(c, 'is_closed', True)]
        if len(key_candles) >= 2:
            key_vol_avg = sum(_get(c, 'volume') for c in key_candles) / len(key_candles)
            if vol_avg > 0 and key_vol_avg < vol_avg * 0.5:
                flaws.append("low_volume_key_candle")

    # Flaw 4: Entry > 8% past OB/FVG zone (was 5% — too strict, blocked valid breakouts)
    last_price = _get(candles[-1], 'close') if candles else 0
    ob = smc.get("ob")
    if ob and last_price > 0:
        ob_high = ob.get("high", 0)
        ob_low = ob.get("low", 0)
        if ob_high > 0 and ob_low > 0:
            ob_mid = (ob_high + ob_low) / 2
            deviation = abs(last_price - ob_mid) / ob_mid * 100
            if deviation > 12.0:
                flaws.append(f"entry_far_from_ob_{deviation:.1f}%")

    return flaws



async def scan_ltf_pipeline(symbol: str, timeframe: str = "15M") -> dict:
    """D2's own 4-layer pipeline — independent from D1.

    Flow → CRT → SMC → Momentum → Signal Builder
    Falls back to SMC-only for impulse coins.
    Every coin produces a result (score penalties instead of hard drops).
    """
    candles = market_data.get_candles(symbol, timeframe)
    penalties = 0
    penalty_reasons = []
    skip_reason = None

    if not candles or len(candles) < 25:
        penalties += _NO_CANDLES_PENALTY
        penalty_reasons.append("insufficient_candles")
        candles = []
        skip_reason = "insufficient_candles"

    last_price = _get(candles[-1], 'close', 0) if candles else 0
    atr_val = atr(candles) if candles else 0
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0
    if candles and (atr_pct < MIN_ATR_PERCENT or atr_val < ADAPTIVE_ATR_MIN_ABSOLUTE):
        penalties += _ATR_PENALTY
        penalty_reasons.append(f"atr_low({atr_pct:.3f}%)")

    env = calc_envelope(candles, 50)
    range_size = env.get('range_size', 0)
    if candles and range_size < atr_val * MIN_RANGE_MULTIPLIER:
        penalties += _RANGE_PENALTY
        penalty_reasons.append(f"range_small({range_size:.6f})")
        logger.debug(f"[ltf_pipeline] RANGE_SMALL {symbol}: range={range_size:.6f} — penalty -{_RANGE_PENALTY}")

    _count_stage("candidate_pass")

    # FLOW GATE
    swings = detect_swing_points(candles[-30:] if candles else [])
    btc_candles = market_data.get_candles("BTCUSDT", timeframe) or []
    flow = analyze_flow(symbol, candles, swings, timeframe, btc_candles)
    fast = detect_fast_mover(candles, swings) if candles else {"is_fast_mover": False, "score": 0}

    if not flow.get("is_flowing") and not fast.get("is_fast_mover"):
        penalties += _FLOW_PENALTY
        penalty_reasons.append("no_flow")
        logger.debug(f"[ltf_pipeline] FLOW_OFF {symbol}: no flow, not fast_mover — penalty -{_FLOW_PENALTY}")

    _count_stage("flow_gate_pass")

    # PRIMARY PATH: CRT + SMC
    crt = run_crt(candles) if candles else None
    smc = None
    path = "NONE"

    if crt:
        smc = run_smc(candles, crt)
        if smc:
            path = "CRT+SMC"
        else:
            penalties += _NO_SMC_PENALTY
            penalty_reasons.append("smc_fail")
            path = "CRT(no_SMC)"
    else:
        # FALLBACK: SMC-only for impulse coins
        fallback_crt = build_smc_only_context(candles) if candles else None
        if fallback_crt:
            smc = run_smc(candles, fallback_crt)
            if smc:
                crt = fallback_crt
                path = "SMC-ONLY"
            else:
                penalties += _NO_SMC_PENALTY
                penalty_reasons.append("smc_fail_fallback")
                crt = fallback_crt
                path = "SMC(no_result)"
        else:
            penalties += _FALLBACK_PENALTY
            penalty_reasons.append("no_structure")
            path = "NO_STRUCTURE"

    _count_stage("crt_smc_pass")

    # ── FATAL FLAW (penalty, not hard drop) ─────────────────────────────
    if crt and smc and candles:
        ob = smc.get("ob")
        fvg_zone = smc.get("fvg")
        last_price_2 = _get(candles[-1], 'close', 0) if candles else 0
        if ob and last_price_2 > 0:
            ob_low = ob.get("low", 0)
            ob_high = ob.get("high", 0)
            if ob_low and ob_high:
                ob_mid = (ob_low + ob_high) / 2
                flow["ob_proximity"] = (ob_low <= last_price_2 <= ob_high) or \
                    (abs(last_price_2 - ob_mid) / ob_mid * 100 <= 1.5)
        if fvg_zone and last_price_2 > 0:
            fvg_bot = fvg_zone.get("bottom", 0)
            fvg_top = fvg_zone.get("top", 0)
            if fvg_bot and fvg_top:
                fvg_mid = (fvg_bot + fvg_top) / 2
                flow["fvg_proximity"] = (fvg_bot <= last_price_2 <= fvg_top) or \
                    (abs(last_price_2 - fvg_mid) / fvg_mid * 100 <= 1.5)

        fatal_flaws = _check_d2_fatal_flaws(candles, smc, flow)
        if fatal_flaws:
            for f in fatal_flaws:
                _count_stage(f"fatal_{f}")
            penalties += len(fatal_flaws) * _FATAL_FLAW_PENALTY
            penalty_reasons.extend(f"fatal:{f}" for f in fatal_flaws)
            logger.info(f"[ltf_pipeline] FATAL FLAW {symbol}: {fatal_flaws} — penalty -{len(fatal_flaws) * _FATAL_FLAW_PENALTY}")

    _count_stage("fatal_flaw_pass")

    # ── Structural tags for D3 frontend display ────────────────────────
    # Extract OB/MSB/FVG/Liquidity from SMC result so D3 can render
    # structure tags on the frontend cards (MSB, OB, FVG, LIQ SWEPT).
    # D3 reads these at the top level of raw_signal (raw_signal.ob, etc.)
    # AND under raw_signal.structure for organized access.
    _d2_structure = {}
    _d2_ob = None
    _d2_msb = None
    _d2_fvg = None
    _d2_liq = None
    if smc:
        msb = smc.get("msb", {})
        if msb and msb.get("confirmed"):
            _d2_msb = {
                "type": msb.get("type", ""),
                "confirmed": True,
                "level": msb.get("level", 0),
                "direction": msb.get("direction", ""),
            }
            _d2_structure["msb"] = _d2_msb
        ob = smc.get("ob")
        if ob:
            _d2_ob = {
                "type": ob.get("type", ""),
                "zone": ob.get("zone", "UNKNOWN"),
                "high": ob.get("high", 0),
                "low": ob.get("low", 0),
                "strength": ob.get("strength", 0),
                "proximity": ob.get("proximity", 999),
            }
            _d2_structure["ob"] = _d2_ob
        fvg = smc.get("fvg")
        if fvg:
            _d2_fvg = {
                "type": fvg.get("type", ""),
                "top": fvg.get("top", 0),
                "bottom": fvg.get("bottom", 0),
                "size_atr": fvg.get("size_atr", 0),
                "filled_pct": fvg.get("filled_pct", 100),
            }
            _d2_structure["fvg"] = _d2_fvg
        liq = smc.get("liquidity")
        if liq:
            _d2_liq = {
                "swept": liq.get("swept", False),
                "level": liq.get("level", 0),
                "direction": liq.get("direction", ""),
            }
            _d2_structure["liquidity"] = _d2_liq

    # ── D2 100-POINT SCORING ────────────────────────────────────────────
    fm = detect_fast_mover(candles, swings) if candles else {"is_fast_mover": False, "score": 0}
    crt_score_input = min(crt.get("crt_score", 0) if crt else 0, 25)
    smc_score_input = min(smc.get("smc_score", 0) if smc else 0, SMC_SCORE_MAX)
    flow_score_input = min(flow.get("boost", 0), D2_FLOW_SCORE_MAX)
    momentum_score_input = min(fm.get("score", 0), 15)

    _count_stage("scoring_pass")

    htf_context_score = 0

    nascent = detect_nascent_move(
        candles,
        (crt or {}).get("displacement", {}).get("crt_trade_direction", "BULLISH"),
        ""
    ) if candles else {"nascent_move": False, "conditions_met": 0, "partial": False}
    nascent_score = _score_nascent_move(nascent)
    timing_score = _score_timing_d2(candles) if candles else 0
    confluence_score = _confluence_bonus_d2(
        crt_score_input, smc_score_input, flow_score_input,
        momentum_score_input, nascent_score, htf_context_score, timing_score
    )

    raw_composite = crt_score_input + smc_score_input + flow_score_input + momentum_score_input + \
                    nascent_score + htf_context_score + timing_score + confluence_score
    composite_score = max(0, raw_composite - penalties)

    logger.debug(f"[ltf_pipeline] SCORE {symbol} ({path}): raw={raw_composite} "
                 f"penalties=-{penalties} ({', '.join(penalty_reasons)}) → final={composite_score}")

    # ── DIRECTION ───────────────────────────────────────────────────────
    if crt and "displacement" in crt:
        direction = crt["displacement"]["crt_trade_direction"]
    elif smc:
        msb = smc.get("msb", {})
        direction = (msb.get("type", "BULLISH").upper() if msb.get("type") else "BULLISH")
    else:
        direction = "NEUTRAL"

    # ── ENTRY / SL / TP (always build, even for REJECTED) ──────────────
    entry = _get(candles[-1], 'close', 0) if candles else 0
    if entry > 0 and direction != "NEUTRAL":
        atr_sl = atr_val * 1.5 if atr_val > 0 else entry * 0.02
        if direction == "BULLISH":
            sl = entry - atr_sl
            tp1 = entry + atr_sl * 1.0
            tp2 = entry + atr_sl * 2.0
        else:
            sl = entry + atr_sl
            tp1 = entry - atr_sl * 1.0
            tp2 = entry - atr_sl * 2.0
        risk = abs(entry - sl)
        rr = abs(tp1 - entry) / risk if risk > 0 else 0
    else:
        sl, tp1, tp2, rr = entry, entry, entry, 0

    # ── TIER ────────────────────────────────────────────────────────────
    if composite_score >= TIER_SNIPER_SCORE:
        tier = "SNIPER"
    elif composite_score >= TIER_OPPORTUNITY_SCORE:
        tier = "OPPORTUNITY"
    elif composite_score >= TIER_WATCH_SCORE:
        tier = "WATCH"
    elif composite_score >= TIER_WEAK_SCORE:
        tier = "WEAK"
    else:
        tier = "REJECTED"

    ep_pass = crt_score_input >= D2_MIN_ENTRY_PRECISION
    flow_pass = flow_score_input >= D2_MIN_FLOW
    mom_pass = momentum_score_input >= D2_MIN_MOMENTUM

    signal = {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "tier": tier,
        "composite_score": composite_score,
        "raw_composite_score": raw_composite,
        "penalties": penalties,
        "penalty_reasons": penalty_reasons,
        "engine_path": path,
        "structure": _d2_structure,
        "ob": _d2_ob,
        "msb": _d2_msb,
        "fvg": _d2_fvg,
        "liquidity": _d2_liq,
        "flow_direction": flow.get("direction", "NEUTRAL"),
        "killzone": flow.get("killzone", "NONE"),
        "flow_score": flow_score_input,
        "momentum_score": momentum_score_input,
        "fast_mover_boost": momentum_score_input,
        "htf_context": htf_context_score,
        "nascent_move": nascent.get("nascent_move", False),
        "nascent_conditions": nascent.get("conditions_met", 0),
        "nascent_partial": nascent.get("partial", False),
        "nascent_score": nascent_score,
        "crt_score": crt_score_input,
        "smc_score": smc_score_input,
        "entry_precision": 0.0,
        "entry_precision_raw": 0.0,
        "timing_score": timing_score,
        "confluence_score": confluence_score,
        "scoring_breakdown": {
            "entry_precision": 0.0,
            "ltf_structure": smc_score_input,
            "flow": flow_score_input,
            "nascent_move": nascent_score,
            "htf_context": htf_context_score,
            "momentum": momentum_score_input,
            "timing": timing_score,
            "confluence": confluence_score,
            "max_entry_precision": 20,
        },
        "threshold_ep_pass": ep_pass,
        "threshold_flow_pass": flow_pass,
        "threshold_momentum_pass": mom_pass,
        "thresholds_passed": all([ep_pass, flow_pass, mom_pass]),
        "entry": entry,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "rr1": rr,
        "rr2": rr * 1.5,
        "expected_value_pct": round(composite_score * 0.5 * rr, 2),
        "estimated_win_rate": round(min(composite_score / 100 * 80 + 20, 85), 1),
        "born_at": _get(candles[-1], 'time', 0) if candles else 0,
        "confidence": min(composite_score / 100, 1.0),
        "atr": atr_val,
        "atr_pct": atr_pct,
    }

    if tier == "REJECTED":
        logger.debug(f"[ltf_pipeline] REJECTED {symbol} {timeframe}: score={composite_score} "
                      f"penalties={penalty_reasons}")
    else:
        _count_stage("final_signal")
        logger.info(f"[ltf_pipeline] SIGNAL {symbol} {timeframe}: {tier} score={composite_score} "
                     f"dir={direction} rr={rr:.1f} path={path} "
                     f"crt={crt_score_input} smc={smc_score_input} flow={flow_score_input} mom={momentum_score_input} "
                     f"EP={crt_score_input}/{D2_MIN_ENTRY_PRECISION} nascent={nascent.get('conditions_met',0)}/5 "
                     f"penalties={penalty_reasons}")

    _count_stage("crt_smc_pass")

    # ── EvidenceRecord: log D2 structural findings ──────────────────────
    _log_ltf_evidence(symbol, timeframe, signal, crt or {}, smc or {}, flow, nascent, path)
    return signal

async def _log_ltf_evidence_async(symbol: str, timeframe: str, signal: dict,
                                   crt: dict, smc: dict, flow: dict, nascent: dict, path: str):
    """Append EvidenceRecords for D2 structural findings to evidence_store."""
    from backend.evidence_store import evidence_store, next_evidence_id
    from backend.evidence_record import EvidenceCategory, EvidenceStrength, EvidenceRecord
    from backend.state_store import state_store
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).timestamp()
    snap_id = state_store.last_snapshot_id
    direction = signal.get("direction", "NEUTRAL")
    last_price = signal.get("entry", 0)
    records: list = []

    # MSB break evidence
    msb = crt.get("msb", smc.get("msb", {}))
    msb_type = msb.get("type") or "NONE"
    if msb_type != "NONE":
        strength = EvidenceStrength.STRONG if msb.get("confirmed", False) else EvidenceStrength.MODERATE
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.MSB_BREAK,
            symbol=symbol, timeframe=timeframe,
            price=last_price, strength=strength,
            direction=msb_type.upper(),
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
    # htf_context always 0 (D2 independent) — removed from confluence check

    return min(factors, CONFLUENCE_MAX)
