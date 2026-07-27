"""Signal Builder — combines CRT + SMC analysis into a structured signal dict."""
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.helpers.candle_math import _get, atr
from backend.helpers.volume_profile import compute_volume_profile
from backend.vsp_helpers import detect_swing_points, detect_fvg
from backend.liquidity_map import detect_liquidity_pools

logger = logging.getLogger("judah.builder")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _tier(score: float) -> str:
    if score >= 70:
        return "SNIPER"
    if score >= 60:
        return "OPPORTUNITY"
    if score >= 50:
        return "WATCH"
    return "REJECTED"


# ---------------------------------------------------------------------------
# Structural SL/TP — hedge fund methodology
# ---------------------------------------------------------------------------

def _find_nearest_swing(direction: str, candles: list, lookback: int = 20) -> Optional[float]:
    """Find the nearest swing low (bullish) or swing high (bearish) that was swept.

    This is the last line of defense — if price breaks it, the smart money thesis is wrong.
    """
    if not candles or len(candles) < lookback:
        return None

    recent = candles[-lookback:]

    if direction == "BULLISH":
        # Find the lowest low — where stops were hunted before the move up
        lowest = min(recent, key=lambda c: c.low)
        return lowest.low
    else:
        # Find the highest high — where shorts were stopped out before the move down
        highest = max(recent, key=lambda c: c.high)
        return highest.high


def _find_fvg_target(direction: str, fvg_zones: list, candles: list, num_levels: int = 2) -> list:
    """Find opposing FVG zones for take-profit targets.

    Returns sorted list of price levels (nearest first).
    """
    if not fvg_zones or not candles:
        return []

    current_price = candles[-1].close
    targets = []

    if direction == "BULLISH":
        # FVGs ABOVE current price (resistance levels)
        for fvg in fvg_zones:
            top = fvg.get("top") if isinstance(fvg, dict) else getattr(fvg, "top", None)
            if top is not None and top > current_price:
                targets.append(top)
    else:
        # FVGs BELOW current price (support levels)
        for fvg in fvg_zones:
            bottom = fvg.get("bottom") if isinstance(fvg, dict) else getattr(fvg, "bottom", None)
            if bottom is not None and bottom < current_price:
                targets.append(bottom)

    targets.sort(key=lambda x: abs(x - current_price))
    return targets[:num_levels]


def calculate_structural_sl_tp(
    entry_price: float,
    direction: str,
    candles: list,
    fvg_zones: list = None,
    atr_val: float = None,
) -> tuple:
    """Calculate SL and TP from structural levels.

    Returns (stop_loss, take_profit_1, take_profit_2, risk_reward_ratio).

    Hedge fund methodology:
    - SL goes beyond the structural point that invalidates the thesis
    - TP goes to opposing structural level (FVG or swing)
    - RR emerges from market geometry, not arbitrary math
    """
    if not candles:
        # Fallback to ATR if no candle data
        atr_val = atr_val or (entry_price * 0.01)
        if direction == "BULLISH":
            sl = entry_price - atr_val * 0.5
            return round(sl, 4), round(entry_price + atr_val * 1.5, 4), round(entry_price + atr_val * 2.5, 4), 3.0
        else:
            sl = entry_price + atr_val * 0.5
            return round(sl, 4), round(entry_price - atr_val * 1.5, 4), round(entry_price - atr_val * 2.5, 4), 3.0

    # Buffer: max(ATR * 0.5, 0.1% of price) — never tighter
    atr_safe = atr_val or (entry_price * 0.01)
    buffer = max(atr_safe * 0.5, entry_price * 0.001)

    # SL: Beyond the nearest swept swing point
    swing_level = _find_nearest_swing(direction, candles)
    if swing_level is None:
        # Fallback: recent 10-candle swing
        recent_low = min(c.low for c in candles[-10:])
        recent_high = max(c.high for c in candles[-10:])
        swing_level = recent_low if direction == "BULLISH" else recent_high

    if direction == "BULLISH":
        stop_loss = swing_level - buffer
        take_profit_1 = entry_price + (entry_price - stop_loss) * 1.0   # 1:1 minimum
        take_profit_2 = entry_price + (entry_price - stop_loss) * 2.5   # 2.5:1 extension
    else:
        stop_loss = swing_level + buffer
        take_profit_1 = entry_price - (stop_loss - entry_price) * 1.0
        take_profit_2 = entry_price - (stop_loss - entry_price) * 2.5

    # Refine TP with FVG levels (nearest opposing FVG)
    if fvg_zones:
        fvg_targets = _find_fvg_target(direction, fvg_zones, candles)
        if fvg_targets:
            take_profit_1 = fvg_targets[0]
            if len(fvg_targets) > 1:
                take_profit_2 = fvg_targets[1]

    # Ensure minimum 1:1 RR
    risk = abs(entry_price - stop_loss)
    reward_tp1 = abs(take_profit_1 - entry_price)
    if risk > 0 and reward_tp1 / risk < 1.0:
        if direction == "BULLISH":
            take_profit_1 = entry_price + risk * 1.0
            take_profit_2 = entry_price + risk * 2.5
        else:
            take_profit_1 = entry_price - risk * 1.0
            take_profit_2 = entry_price - risk * 2.5

    risk_reward = round(abs(take_profit_1 - entry_price) / risk, 2) if risk > 0 else 1.0

    return (
        round(stop_loss, 4),
        round(take_profit_1, 4),
        round(take_profit_2, 4),
        risk_reward,
    )


# ---------------------------------------------------------------------------
# Main build function
# ---------------------------------------------------------------------------

def build_signal(
    symbol: str,
    timeframe: str,
    crt: dict,
    smc: dict,
    candles: list,
) -> Optional[dict]:
    """Build final trade signal with all institutional features.

    Parameters
    ----------
    symbol : str — Trading pair, e.g. "BTCUSDT".
    timeframe : str — Candle timeframe, e.g. "1h", "4h", "1d".
    crt : dict — Full CRT engine result (from engines/crt_engine.run_crt).
    smc : dict — Full SMC engine result (from engines/smc_engine.run_smc).
    candles : list — Recent candle list (Candle dataclass objects).
    """
    crt_score = crt.get("crt_score", 0)
    smc_score = smc.get("smc_score", 0)
    total = crt_score + smc_score

    if total < 10:
        logger.debug(f"[builder] REJECT {symbol} {timeframe}: score too low {total}")
        return None

    direction = crt["displacement"]["crt_trade_direction"]

    atr_val = atr(candles)
    last = candles[-1].close

    # Structural SL/TP — replaces fixed ATR multiplier
    fvgs = detect_fvg(candles) or []
    smc_fvgs = smc.get("fvg_zones", []) or fvgs
    stop_loss, tp1, tp2, risk_reward = calculate_structural_sl_tp(
        entry_price=last,
        direction=direction,
        candles=candles,
        fvg_zones=smc_fvgs,
        atr_val=atr_val,
    )

    if direction == "BULLISH":
        entry = +(last + 0.0001)
    else:
        entry = +(last - 0.0001)

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

    # === COMPOSITE SCORE ===
    composite_score = crt_score + smc_score

    logger.info(f"COMPOSITE: {crt_score}+{smc_score}={composite_score}/100")

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
        "stop_loss": round(stop_loss, 5),
        "take_profit_1": round(tp1, 5),
        "take_profit_2": round(tp2, 5),
        "take_profit": round(tp1, 5),        # backwards-compat alias → TP1
        "bsl": stop_loss if direction == "BEARISH" else None,
        "ssl": stop_loss if direction == "BULLISH" else None,
        "structure_sl": True,
        "sl_source": stop_loss,
        "sl_type": "structural",
        "risk": round(risk, 5),
        "reward": round(reward, 5),
        "rr": round(risk_reward, 2),
        "scenario": _scenario(crt, smc),

        # MARKET DATA
        "current_price": last,
        "atr": round(atr_val, 5),
        "atr_value": round(atr_val, 5),
        "atr_percent": round((atr_val / last * 100) if last > 0 else 0, 2),
        "atr_sl_distance": round(risk, 5),
        "distance_to_entry_pct": round(abs(candles[-1].close - entry) / candles[-1].close * 100, 2),

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


def _detect_session(ts):
    """Detect trading session from candle timestamp.

    Handles datetime, int, float, ISO string, millisecond, or second timestamps.
    Never raises — returns 0 (neutral/Asia) on any unexpected format.
    """
    try:
        from datetime import datetime, timezone

        # Case 1: already a datetime object
        if isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            return dt.hour

        # Case 2: string (ISO format like "2024-07-27T18:59:43Z")
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                return dt.hour
            except Exception:
                return 0

        # Case 3: numeric (int or float) — may be ms or seconds
        if isinstance(ts, (int, float)):
            # Binance uses ms: > year 2100 in seconds = 4102444800
            if ts > 4102444800:
                ts = ts / 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.hour

        return 0
    except Exception:
        return 0


def _tier_label(tier: str) -> str:
    return {
        "SNIPER": "Sniper", "ACTIVE": "Active", "WATCH": "Watch", "REJECTED": "Rejected",
    }.get(tier, tier)
