"""Signal Builder — combines CRT + SMC analysis into a structured signal dict.
Institutional hedge fund methodology for SL/TP/Entry.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.helpers.candle_math import _get, atr
from backend.helpers.volume_profile import compute_volume_profile
from backend.vsp_helpers import detect_swing_points, detect_fvg
from backend.liquidity_map import detect_liquidity_pools
from backend.config import (
    SL_RELEVANCE_PCT,
    SL_MAX_STRUCTURAL_DISTANCE_PCT,
    SL_ATR_FALLBACK_MULT,
    SL_SKIP_SWEPT,
    SWING_SL_LOOKBACK,
    MIN_RR,
    TP_MAX_RR,
    SL_BUFFER_PERCENT,
    TP_RR_MULTIPLIER,
    TIER_SNIPER_SCORE,
    TIER_OPPORTUNITY_SCORE,
    TIER_WATCH_SCORE,
)

logger = logging.getLogger("judah.builder")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _tier(score: float) -> str:
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


def _tier_label(tier: str) -> str:
    return {
        "SNIPER": "Sniper",
        "OPPORTUNITY": "Opportunity",
        "WATCH": "Watch",
        "REJECTED": "Rejected",
    }.get(tier, tier)


def _detect_session(timestamp: int) -> str:
    from backend.helpers.session import get_current_session
    return get_current_session()


def _detect_tick_size(price: float) -> float:
    """Auto-detect tick size from price magnitude. Returns float."""
    if price < 0.0001:
        return 1e-9      # Sub-satoshi pairs (PEPE etc.)
    elif price < 0.01:
        return 1e-7
    elif price < 1:
        return 1e-5
    elif price < 100:
        return 0.0001
    else:
        return 0.001


# ──────────────────────────────────────────────────────────────────────────
# INSTITUTIONAL ENTRY
# ──────────────────────────────────────────────────────────────────────────

def _calculate_entry(scenario: str, direction: str, candles: list, smc: dict, crt: dict) -> tuple:
    """Institutional limit-entry at verified structural anchor.

    Rules (hedge fund methodology):
    1. If scenario has a structural anchor (OB, FVG, sweep, MSB, OTE, CRT), use it
       ONLY if the anchor is within 2% of current market price.
       If anchor is >2% away → the setup is stale / hasn't reached entry yet → use
       ATR-bounded limit near market (within 0.5%).
    2. Never use a pure "last + buffer" market entry without any structural basis.
    3. If no structural anchor → reject signal (not a valid institutional setup).

    Returns (entry_price, entry_type, distance_to_entry_pct)
    """
    last = candles[-1].close
    atr_val = crt.get("atr_value", last * 0.01) or last * 0.01
    tick_size = _detect_tick_size(last)
    atr_buffer = max(tick_size, atr_val * 0.05, last * 0.0005)

    # We build a prioritized list of (price, type, score) candidates.
    # The highest-scoring valid candidate wins.
    candidates: list[tuple[float, str, float]] = []

    def _add_candidate(price: float, etype: str, base_score: float = 5.0):
        if price <= 0 or not (0.00000001 <= price <= 999_999_999):
            return
        dist_pct = abs(price - last) / last * 100
        # Proximity gate: only accept if within 2% of market
        if dist_pct > 2.0:
            return
        # Prefer candidates closer to market (lower distance = higher effective score)
        adj_score = base_score - dist_pct
        candidates.append((round(price, 8), etype, adj_score))

    # ── Scenario-specific structural anchors ──────────────────────────────
    try:
        if scenario == "OB_BOUNCE" and smc.get("ob"):
            ob = smc["ob"]
            ob_high = ob.get("high", 0)
            ob_low = ob.get("low", 0)
            if ob_high and ob_low:
                _add_candidate((ob_high + ob_low) / 2, "structural_ob", 5.0)

        elif scenario and scenario.startswith("FVG_FILL") and smc.get("fvg"):
            fvg = smc["fvg"]
            fvg_top = fvg.get("top", 0)
            fvg_bot = fvg.get("bottom", 0)
            if fvg_top and fvg_bot:
                _add_candidate((fvg_top + fvg_bot) / 2, "structural_fvg", 5.0)

        elif scenario == "LIQUIDITY_SWEEP" and smc.get("liquidity"):
            liq = smc["liquidity"]
            level = liq.get("level", 0)
            if level and level > 0:
                _add_candidate(level, "structural_sweep", 5.0)

        elif scenario == "MSB_RETEST" and smc.get("msb"):
            msb = smc["msb"]
            level = msb.get("level")
            if level:
                _add_candidate(level, "structural_msb", 5.0)

        elif scenario == "DISPLACEMENT_RETRACEMENT" and crt.get("displacement"):
            d = crt["displacement"]
            disp_low = d.get("low", 0)
            disp_high = d.get("high", 0)
            if disp_low and disp_high and disp_high > disp_low:
                if direction == "BULLISH":
                    entry_candidate = disp_low + (disp_high - disp_low) * 0.59
                else:
                    entry_candidate = disp_high - (disp_high - disp_low) * 0.59
                _add_candidate(entry_candidate, "structural_ote", 5.0)

        elif scenario == "CRT_SETUP" and crt.get("range"):
            rng = crt["range"]
            rng_low = rng.get("low", 0)
            rng_high = rng.get("high", 0)
            if direction == "BULLISH" and rng_low:
                _add_candidate(rng_low, "structural_crt_low", 4.0)
            elif direction == "BEARISH" and rng_high:
                _add_candidate(rng_high, "structural_crt_high", 4.0)

    except Exception:
        pass

    # ── ATR-bounded limit near market (always valid as fallback) ───────────
    if direction == "BULLISH":
        market_limit = last - atr_buffer * 0.3
    else:
        market_limit = last + atr_buffer * 0.3
    _add_candidate(market_limit, "limit_near_market", 3.0)

    if not candidates:
        # Absolute safety net: use current price (rare edge case)
        return round(last, 8), "market_fallback", 0.0

    # Pick highest-adjusted candidate
    candidates.sort(key=lambda x: x[2], reverse=True)
    entry, entry_type, _ = candidates[0]

    distance_pct = (entry - last) / last * 100 if last else 0
    return round(entry, 8), entry_type, round(distance_pct, 3)


# ──────────────────────────────────────────────────────────────────────────
# INSTITUTIONAL STOP LOSS
# ──────────────────────────────────────────────────────────────────────────

def _detect_swept_level(direction: str, level: float, candles: list) -> bool:
    """Return True if the given swing level has been 'swallowed' — price traded
    through it and closed back on the other side (stale structure).

    Bullish: level was a swing low. Swept if price has gone BELOW it and
             closed back ABOVE it.
    Bearish: level was a swing high. Swept if price has gone ABOVE it and
             closed back BELOW it.
    """
    if not candles or len(candles) < 3:
        return False

    for c in candles[-SWING_SL_LOOKBACK:]:
        if direction == "BULLISH":
            if c.close < level and c.close > c.open:
                # Bullish candle closed below the level — it was broken through
                return True
        else:
            if c.close > level and c.close < c.open:
                # Bearish candle closed above the level — it was broken through
                return True
    return False


def _find_institutional_sl(direction: str, entry_price: float, candles: list) -> Optional[float]:
    """Find the IMMEDIATE swing point that invalidates the thesis.

    Institutional rules (what top quant funds do):
    1. Only consider swings within `SL_RELEVANCE_PCT`% of entry — no distant wicks
    2. Skip already-swept levels (stale structure)
    3. Recency-weighted: look at last SWING_SL_LOOKBACK candles (not 20)
    4. Pick the CLOSEST valid swing to entry (tighter stop = better R:R)

    Returns:
        SL price, or None if no valid structural swing found.
    """
    if not candles or len(candles) < SWING_SL_LOOKBACK:
        return None

    recent = candles[-SWING_SL_LOOKBACK:]
    relevance_threshold = entry_price * SL_RELEVANCE_PCT / 100
    max_dist = entry_price * SL_MAX_STRUCTURAL_DISTANCE_PCT / 100

    # Detect all swing points in lookback window
    swings = detect_swing_points(recent)
    if not swings:
        return None

    candidates = []

    if direction == "BULLISH":
        # We want swing LOWS below entry — structural support under the entry
        for swing_low in swings.get("swing_lows", []):
            level = swing_low if isinstance(swing_low, (int, float)) else swing_low.get("price", 0)
            if level <= 0:
                continue
            # Must be below entry (SL is below entry for bullish)
            if level >= entry_price:
                continue
            dist = entry_price - level
            # Relevance gate
            if dist > max_dist:
                continue
            # Skip already-swept structure
            if SL_SKIP_SWEPT and _detect_swept_level("BULLISH", level, recent):
                continue
            # Score: prefer closer swings (tighter SL = better)
            candidates.append((level, dist))

    else:
        # BEARISH: swing HIGHS above entry — structural resistance above the entry
        for swing_high in swings.get("swing_highs", []):
            level = swing_high if isinstance(swing_high, (int, float)) else swing_high.get("price", 0)
            if level <= 0:
                continue
            # Must be above entry (SL is above entry for bearish)
            if level <= entry_price:
                continue
            dist = level - entry_price
            # Relevance gate
            if dist > max_dist:
                continue
            # Skip already-swept structure
            if SL_SKIP_SWEPT and _detect_swept_level("BEARISH", level, recent):
                continue
            candidates.append((level, dist))

    if not candidates:
        return None

    # Pick the CLOSEST swing to entry → tightest SL
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def calculate_structural_sl_tp(
    entry_price: float,
    direction: str,
    candles: list,
    fvg_zones: list = None,
    atr_val: float = None,
) -> tuple:
    """Calculate SL and TP — institutional hedge fund methodology.

    Returns (stop_loss, take_profit_1, take_profit_2, risk_reward, sl_method).

    SL priority:
      1. Structural swing (nearest UNSWEPT, within 3% of entry)
      2. ATR fallback (1.5x ATR) if no valid swing
      3. Always cap max distance at 4% of entry

    TP priority:
      1. Nearest opposing FVG zone
      2. 1:1 minimum, 2.5:1 extension from SL distance
      3. Hard cap at 4:1 RR (institutional standard — no lottery tickets)
    """
    if not candles:
        # No candle data — pure ATR fallback
        atr_fallback = atr_val or (entry_price * 0.01)
        if direction == "BULLISH":
            sl = entry_price - atr_fallback * SL_ATR_FALLBACK_MULT
            tp1 = entry_price + (entry_price - sl) * 1.0
            tp2 = entry_price + (entry_price - sl) * 2.0
        else:
            sl = entry_price + atr_fallback * SL_ATR_FALLBACK_MULT
            tp1 = entry_price - (sl - entry_price) * 1.0
            tp2 = entry_price - (sl - entry_price) * 2.0
        risk = abs(entry_price - sl)
        rr = round(abs(tp1 - entry_price) / risk, 2) if risk > 0 else 1.0
        return round(sl, 5), round(tp1, 5), round(tp2, 5), rr, "atr"

    atr_safe = atr_val or atr(candles) or (entry_price * 0.01)
    buffer = max(atr_safe * 0.3, entry_price * 0.0003)

    # ── STEP 1: Try structural swing ──────────────────────────────────────
    sl_method = "structural"
    swing_level = _find_institutional_sl(direction, entry_price, candles)

    if swing_level is not None:
        # Structural SL: buffer beyond the swing
        if direction == "BULLISH":
            stop_loss = swing_level - buffer
        else:
            stop_loss = swing_level + buffer
        # Verify the swing didn't place SL too far
        max_sl_dist = entry_price * SL_MAX_STRUCTURAL_DISTANCE_PCT / 100
        sl_dist = abs(entry_price - stop_loss)
        if sl_dist > max_sl_dist:
            # Structural swing too distant — fall back to ATR
            sl_method = "atr"
            if direction == "BULLISH":
                stop_loss = entry_price - atr_safe * SL_ATR_FALLBACK_MULT
            else:
                stop_loss = entry_price + atr_safe * SL_ATR_FALLBACK_MULT
    else:
        # ── STEP 2: ATR fallback ──────────────────────────────────────────
        sl_method = "atr"
        if direction == "BULLISH":
            stop_loss = entry_price - atr_safe * SL_ATR_FALLBACK_MULT
        else:
            stop_loss = entry_price + atr_safe * SL_ATR_FALLBACK_MULT

    # ── STEP 3: Cap max SL distance (hard limit) ──────────────────────────
    max_sl = entry_price * SL_MAX_STRUCTURAL_DISTANCE_PCT / 100
    sl_dist = abs(entry_price - stop_loss)
    if sl_dist > max_sl:
        if direction == "BULLISH":
            stop_loss = entry_price - max_sl
        else:
            stop_loss = entry_price + max_sl
        if sl_method == "structural":
            sl_method = "capped"

    # ── STEP 4: TP calculation ────────────────────────────────────────────
    risk = abs(entry_price - stop_loss)
    # 1:1 minimum, then extend toward TP_RR_MULTIPLIER (capped at TP_MAX_RR)
    tp_rr = min(TP_RR_MULTIPLIER or 2.5, TP_MAX_RR)

    if direction == "BULLISH":
        take_profit_1 = entry_price + risk * 1.0
        take_profit_2 = entry_price + risk * tp_rr
    else:
        take_profit_1 = entry_price - risk * 1.0
        take_profit_2 = entry_price - risk * tp_rr

    # Refine TP with FVG levels (nearest opposing FVG — structural target)
    if fvg_zones:
        fvg_targets = _find_fvg_target(direction, fvg_zones, candles)
        if fvg_targets:
            take_profit_1 = fvg_targets[0]
            if len(fvg_targets) > 1:
                take_profit_2 = fvg_targets[1]

    # Ensure minimum RR
    risk = abs(entry_price - stop_loss)
    reward_tp1 = abs(take_profit_1 - entry_price)
    if risk > 0 and reward_tp1 / risk < MIN_RR:
        # Force TP1 to MIN_RR distance to pass the gate
        if direction == "BULLISH":
            take_profit_1 = entry_price + risk * MIN_RR
        else:
            take_profit_1 = entry_price - risk * MIN_RR

    # Hard cap TP at TP_MAX_RR
    max_tp_dist = risk * TP_MAX_RR
    if direction == "BULLISH":
        if abs(take_profit_1 - entry_price) > max_tp_dist:
            take_profit_1 = entry_price + max_tp_dist
    else:
        if abs(take_profit_1 - entry_price) > max_tp_dist:
            take_profit_1 = entry_price - max_tp_dist

    risk_reward = round(abs(take_profit_1 - entry_price) / risk, 2) if risk > 0 else 1.0

    return (
        round(stop_loss, 5),
        round(take_profit_1, 5),
        round(take_profit_2, 5),
        risk_reward,
        sl_method,
    )


def _find_fvg_target(direction: str, fvg_zones: list, candles: list) -> list:
    """Find the nearest opposing FVG zone to use as TP target(s).

    For BULLISH: target is nearest BULLISH FVG above entry (price runs up into it).
    For BEARISH: target is nearest BEARISH FVG below entry (price runs down into it).

    Returns list of price levels [tp1, tp2] sorted by proximity, or empty list.
    """
    if not fvg_zones or not candles:
        return []

    entry_price = candles[-1].close
    targets = []

    for fvg in fvg_zones:
        fvg_type = fvg.get("type", "")
        # For BULLISH: FVG above entry → price can rise into it
        # For BEARISH: FVG below entry → price can fall into it
        if direction == "BULLISH" and fvg_type == "BULLISH":
            fvg_top = fvg.get("top", 0)
            if fvg_top > entry_price:
                targets.append(fvg_top)
        elif direction == "BEARISH" and fvg_type == "BEARISH":
            fvg_bottom = fvg.get("bottom", 0)
            if fvg_bottom < entry_price and fvg_bottom > 0:
                targets.append(fvg_bottom)

    if not targets:
        return []

    # Sort by proximity to entry
    targets.sort(key=lambda t: abs(t - entry_price))

    # Return up to 2 levels
    return targets[:2]


def _find_nearest_swing(direction: str, candles: list, lookback: int = 20) -> Optional[float]:
    """Deprecated: kept for backwards compatibility. Use _find_institutional_sl."""
    import warnings
    warnings.warn("_find_nearest_swing is deprecated; use _find_institutional_sl", DeprecationWarning, stacklevel=2)
    if not candles or len(candles) < lookback:
        return None
    recent = candles[-lookback:]
    if direction == "BULLISH":
        lowest = min(recent, key=lambda c: c.low)
        return lowest.low
    else:
        highest = max(recent, key=lambda c: c.high)
        return highest.high


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_signal(
    symbol: str,
    timeframe: str,
    crt: dict,
    smc: dict,
    candles: list,
    flow_score: float = 0.0,
    momentum_score: float = 0.0,
) -> Optional[dict]:
    """Build final trade signal with all institutional features.

    4-component scoring:
      CRT (timing)       max 40
      SMC (structure)    max 20
      Flow (conviction)  max 25   — passed in from engine
      Momentum (ignite)  max 20   — passed in from engine
    Total max = 105.

    Parameters
    ----------
    symbol : str — Trading pair, e.g. "BTCUSDT".
    timeframe : str — Candle timeframe, e.g. "1h", "4h", "1d".
    crt : dict — Full CRT engine result.
    smc : dict — Full SMC engine result.
    candles : list — Recent candle list.
    flow_score : float — Flow boost from flow_analyzer (capped 25).
    momentum_score : float — Fast-mover momentum (capped 20).
    """
    crt_score = crt.get("crt_score", 0)
    smc_score = smc.get("smc_score", 0)

    # 4-component composite
    composite_score = crt_score + smc_score + flow_score + momentum_score

    total = composite_score

    # Minimum total score to produce a signal
    if total < 15:
        logger.debug(f"[builder] REJECT {symbol} {timeframe}: score too low {total}")
        return None

    direction = crt["displacement"]["crt_trade_direction"]

    atr_val = atr(candles)
    last = candles[-1].close

    # ── Scenario-aware hybrid entry (replaces fixed last ± 0.0001) ──────
    scenario = _scenario(crt, smc)
    entry, entry_type, distance_to_entry_pct = _calculate_entry(
        scenario, direction, candles, smc, crt
    )

    # Structural SL/TP — passes hybrid entry so RR reflects structural-to-structural
    fvgs = detect_fvg(candles) or []
    smc_fvgs = smc.get("fvg_zones", []) or fvgs
    stop_loss, tp1, tp2, risk_reward, sl_method = calculate_structural_sl_tp(
        entry_price=entry,
        direction=direction,
        candles=candles,
        fvg_zones=smc_fvgs,
        atr_val=atr_val,
    )

    risk = abs(entry - stop_loss)
    reward = abs(tp1 - entry)

    logger.debug(f"[builder] {symbol} {timeframe}: total={total} crt={crt_score} smc={smc_score} "
                 f"dir={direction} entry={entry:.5f} sl={stop_loss:.5f} tp1={tp1:.5f} tp2={tp2:.5f} rr={risk_reward:.1f}")

    # === INSTITUTIONAL DATA ===
    order_flow = _compute_order_flow(candles)
    liquidity_zones = _extract_liquidity_zones(smc, crt)

    # === VOLUME PROFILE ===
    vp_data = crt.get("volume_profile") or {}
    if not vp_data:
        try:
            vp_data = compute_volume_profile(candles[-60:]) or {}
        except Exception:
            vp_data = {}

    volume_profile = {
        "poc_price": vp_data.get("poc_price", 0),
        "poc_volume": vp_data.get("poc_volume", 0),
        "va_high": vp_data.get("va_high", 0),
        "va_low": vp_data.get("va_low", 0),
        "hvn_zones": [],
        "lvn_zones": [],
    }

    # === DISPLACEMENT sub-fields ===
    disp = crt.get("displacement", {})

    # === RETRACEMENT ===
    retracement_pct = crt.get("retracement_percent", 0)
    in_ote = crt.get("in_optimal_ote", False)

    # === SWING POINTS ===
    swings = detect_swing_points(candles)
    swing_count = {
        "highs": len(swings.get("swing_highs", [])),
        "lows": len(swings.get("swing_lows", [])),
    }

    # === FVG ===
    fvg = smc.get("fvg")
    fvg_count = len(fvgs)

    # === LIQUIDITY ===
    liq_pools = detect_liquidity_pools(swings) if swings else {"pools": []}

    # === COMPOSITE SCORE (4 components) ===
    # CRT(40) + SMC(20) + Flow(25) + Momentum(20) = 105 max
    composite_score = crt_score + smc_score + flow_score + momentum_score

    logger.info(f"COMPOSITE: CRT={crt_score} SMC={smc_score} Flow={flow_score} Mom={momentum_score} = {composite_score}/105")

    # === SIGNAL DICT ===
    signal = {
        # CORE
        "id": f"{symbol}_{timeframe}_{candles[-1].time}",
        "symbol": symbol,
        "engine": timeframe,
        "direction": direction,
        "timestamp": candles[-1].time,

        # SCORES
        "base_score": total,
        "composite_score": composite_score,
        "crt_score": crt_score,
        "smc_score": smc_score,
        "tier": _tier(composite_score),
        "tier_label": _tier_label(_tier(composite_score)),

        # SESSION
        "session": crt.get("session", "UNKNOWN"),
        "session_label": crt.get("session_label", crt.get("session", "UNKNOWN")),
        "session_bullish": crt.get("session_bullish", 0),
        "session_bearish": crt.get("session_bearish", 0),
        "sessions_active": [_detect_session(candles[-1].time)],

        # CRT ANALYSIS
        "displacement": disp,
        "displacement_ratio": disp.get("ratio", 0),
        "displacement_direction": disp.get("direction", ""),
        "retracement_percent": retracement_pct,
        "in_ote": in_ote,
        "in_optimal_ote": in_ote,
        "premium_discount": crt.get("premium_discount", "EQUILIBRIUM"),
        "price_position_pct": crt.get("price_position_pct", 50.0),
        "range": crt.get("range", {}),

        # VOLUME PROFILE
        "volume_profile": volume_profile,

        # SMC STRUCTURE
        "vsp": smc.get("vsp"),
        "ob": smc.get("ob"),
        "fvg": fvg,
        "fvg_count": fvg_count,
        "msb": smc.get("msb", {"confirmed": False, "type": None, "level": None}),
        "market_structure": smc.get("msb", {"confirmed": False, "type": None, "level": None}),
        "liquidity": smc.get("liquidity"),
        "liquidity_pools": liq_pools,
        "swing_count": swing_count,
        "swing_highs": len(swings.get("swing_highs", [])),
        "swing_lows": len(swings.get("swing_lows", [])),

        # INSTITUTIONAL
        "institutional_order_flow": order_flow,
        "order_flow": order_flow.get("net_pressure", "neutral") if isinstance(order_flow, dict) else "neutral",
        "liquidity_zones": liquidity_zones,

        # CONFLUENCE
        "confluence": crt.get("confluence_events", []),
        "confluence_boost": 0,
        "confluence_events": crt.get("confluence_events", []),

        # TRADE — Structural SL/TP
        "entry": round(entry, 5),
        "entry_type": entry_type,
        "stop_loss": round(stop_loss, 5),
        "take_profit_1": round(tp1, 5),
        "take_profit_2": round(tp2, 5),
        "rr": round(risk_reward, 2),
        "rr1": round(risk_reward, 2),     # backward compat
        "rr2": round(risk_reward * 2.0, 2),
        "scenario": scenario,

        # MARKET DATA
        "current_price": last,
        "atr": round(atr_val, 5),
        "atr_value": round(atr_val, 5),
        "atr_percent": round((atr_val / last * 100) if last > 0 else 0, 2),
        "atr_sl_distance": round(risk, 5),
        "distance_to_entry_pct": distance_to_entry_pct,

        # FRESHNESS
        "freshness_state": "hot",
        "freshness_factor": 1.0,
        "age_ticks": 0,

        # MTF
        "mtf_alignment": 0,
        "mtf_details": [],

        # PRIORITY
        "priority_boosts": [],
    }

    return signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scenario(crt, smc):
    direction = crt["displacement"]["crt_trade_direction"]
    fvg = smc.get("fvg")
    if crt["in_optimal_ote"] and smc.get("ob"):
        return "DISPLACEMENT_RETRACEMENT"
    if fvg and fvg.get("proximity", 999) <= 1.0:
        return "FVG_FILL_ENTRY_" + direction
    if smc.get("msb", {}).get("confirmed"):
        return "MSB_RETEST"
    if smc.get("liquidity", {}).get("swept"):
        return "LIQUIDITY_SWEEP"
    if smc.get("ob"):
        return "OB_BOUNCE"
    return "CRT_SETUP"


def _compute_order_flow(candles: list) -> dict:
    if not candles or len(candles) < 20:
        return {"net_pressure": "neutral", "buying_pct": 50, "selling_pct": 50}

    recent = candles[-20:]
    buy_vol = 0.0
    sell_vol = 0.0

    for c in recent:
        if c.close > c.open:
            buy_vol += c.volume * 0.7 + abs(c.close - c.open)
            sell_vol += c.volume * 0.3
        elif c.close < c.open:
            sell_vol += c.volume * 0.7 + abs(c.close - c.open)
            buy_vol += c.volume * 0.3
        else:
            buy_vol += c.volume * 0.5
            sell_vol += c.volume * 0.5

    total = buy_vol + sell_vol
    if total == 0:
        return {"net_pressure": "neutral", "buying_pct": 50, "selling_pct": 50}

    buying_pct = round((buy_vol / total) * 100, 1)
    selling_pct = round((sell_vol / total) * 100, 1)

    if buying_pct >= 65:
        net = "strong_buying"
    elif buying_pct >= 55:
        net = "buying"
    elif selling_pct >= 65:
        net = "strong_selling"
    elif selling_pct >= 55:
        net = "selling"
    else:
        net = "neutral"

    return {
        "net_pressure": net,
        "buying_pct": buying_pct,
        "selling_pct": selling_pct,
        "buy_volume": round(buy_vol, 2),
        "sell_volume": round(sell_vol, 2),
    }


def _extract_liquidity_zones(smc: dict, crt: dict) -> dict:
    zones = {}
    liq = smc.get("liquidity", {})
    if liq:
        zones["nearest_sellside"] = liq.get("level")
        zones["nearest_buyside"] = liq.get("level")
        zones["swept"] = liq.get("swept", False)
        zones["direction"] = liq.get("direction", "")
        zones["level"] = liq.get("level")
    return zones
