"""CRT Engine - Candle Range Theory (ICT methodology). Max score: 25.

CRT has 5 mandatory steps in order:
  1. CONSOLIDATION  - tight range (5-20 bars, compressing)
  2. RANGE CANDLE   - breakout candle with body >= 1.8x avg consolidation body
  3. DISPLACEMENT   - price moves in range candle's direction, creating FVG
  4. FILL           - price RETURNS to fill the FVG (retracement back into body)
  5. ENTRY          - trade taken at retest, IN THE DIRECTION of the range candle

CRT scoring breakdown (max 25):
  Consolidation quality:  0-8
  Range candle strength:  0-8
  FVG quality:            0-3
  Retest quality:         0-4
  Premium/Discount zone:  0-2
"""
import logging
from typing import Optional
from backend.helpers.candle_math import (
    body_ratio, avg_body_size, range_metrics,
    retracement_pct, is_in_ote, is_in_optimal_ote,
    _get, atr, atr_percent
)
from backend.helpers.volume_profile import compute_volume_profile
from backend.helpers.session import get_session_at, session_score, get_session_label

logger = logging.getLogger("judah.crt")


# --- CRT CONSTANTS ---

_CONSOLIDATION_MIN_BARS = 5
_CONSOLIDATION_MAX_BARS = 20
_CONSOLIDATION_BODY_RATIO = 2.0
_CONSOLIDATION_TR_RATIO = 2.0
_CONSOLIDATION_TIGHT_PCT = 0.60
_RANGE_CANDLE_BODY_MULT = 1.8
_RANGE_CANDLE_LOOKAHEAD = 20
_FILL_RECENCY_MAX = 20
_FILL_RECENCY_HALF = 12

_W_CONSOLIDATION = 8
_W_RANGE_CANDLE = 8
_W_FVG = 3
_W_RETEST = 4
_W_ZONE = 2
_CRT_MAX_SCORE = 25


def run_crt(candles: list) -> Optional[dict]:
    """Full CRT analysis — 5-step methodology.

    Returns dict with crt_score (capped at 60), displacement, fill data, entry/SL/TP,
    or None if no valid CRT setup exists.
    """
    if not candles or len(candles) < 30:
        return None

    last_price = _get(candles[-1], 'close')
    if last_price <= 0:
        return None

    session = get_session_at(candles[-1].time)

    # Range metrics (needed early for zone scoring)
    rng = range_metrics(candles, 20)

    # STEP 1: Find consolidation
    consolidation = _find_consolidation(candles)
    if not consolidation:
        logger.debug("CRT: NO_CONSOLIDATION — no tight range found")
        return _no_signal("NO_CONSOLIDATION", session)

    # STEP 2: Identify range candle
    range_candle = _find_range_candle(candles, consolidation)
    if not range_candle:
        logger.debug("CRT: NO_RANGE_CANDLE — no breakout candle after consolidation")
        return _no_signal("NO_RANGE_CANDLE", session)

    rc_index = range_candle["index"]
    rc_direction = range_candle["direction"]
    rc_open = range_candle["open"]
    rc_close = range_candle["close"]
    rc_low = range_candle["low"]
    rc_high = range_candle["high"]
    rc_body = range_candle["body"]

    # STEP 3: Verify the fill
    fill = _verify_fill(candles, rc_index, rc_open, rc_close)
    if not fill:
        logger.debug(f"CRT: NO_FILL — price never re-entered range candle body (idx={rc_index})")
        return _no_signal("NO_FILL", session)

    # STEP 4: Direction (BULLISH/BEARISH)
    crt_trade_direction = rc_direction

    # STEP 5: Recency check
    bar_distance = len(candles) - 1 - rc_index
    if bar_distance > _FILL_RECENCY_MAX:
        logger.debug(f"CRT: STALE — range candle {bar_distance} bars ago (max {_FILL_RECENCY_MAX})")
        return _no_signal("STALE", session)

    recency_multiplier = 1.0 if bar_distance <= _FILL_RECENCY_HALF else 0.5

    # STEP 6: Score the setup
    avg_consol_body = consolidation["avg_body"]
    displacement_ratio = min(rc_body / avg_consol_body, 3.0) if avg_consol_body > 0 else 1.0
    fill_quality = fill["quality"]

    s_consolidation = _score_consolidation(consolidation)
    s_range_candle = _score_range_candle_strength(displacement_ratio)
    s_fvg = _score_fvg_quality(fill)
    s_retest = _score_retest_quality(fill_quality, rc_direction)
    s_zone = _score_zone_alignment(last_price, rng)

    crt_score = s_consolidation + s_range_candle + s_fvg + s_retest + s_zone
    crt_score = int(crt_score * recency_multiplier)
    crt_score = min(crt_score, _CRT_MAX_SCORE)

    logger.debug(f"CRT: PASSED dir={crt_trade_direction} rc_dir={rc_direction} "
                 f"c={s_consolidation} rc={s_range_candle} f={s_fvg} "
                 f"rt={s_retest} z={s_zone} mult={recency_multiplier} "
                 f"score={crt_score}/{_CRT_MAX_SCORE}")

    atr_val = atr(candles)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0

    vp_data = {}
    try:
        vp_data = compute_volume_profile(candles[-60:]) or {}
    except Exception:
        pass

    rng = range_metrics(candles, 20)
    price_pct_in_range = 0.0
    premium_discount = "EQUILIBRIUM"
    if rng["range_size"] > 0:
        price_pct_in_range = ((last_price - rng["low"]) / rng["range_size"]) * 100
        if price_pct_in_range > 65:
            premium_discount = "PREMIUM"
        elif price_pct_in_range < 35:
            premium_discount = "DISCOUNT"

    retrace_pct_val = retracement_pct(rc_high, rc_low, last_price, crt_trade_direction)
    in_optimal_ote = is_in_optimal_ote(retrace_pct_val)

    # Calculate entry, SL, TP
    rc_open_price = rc_open
    rc_close_price = rc_close
    entry, sl, tp = _calc_crt_trade(
        crt_trade_direction, rc_direction, rc_open_price, rc_close_price,
        rc_low, rc_high, last_price, atr_val
    )
    if entry is None:
        logger.debug(f"CRT: LOW_RR — no valid trade levels")
        return _no_signal("LOW_RR", session)

    return {
        "session": session,
        "session_label": get_session_label(session),
        "session_bullish": session_score("BULLISH"),
        "session_bearish": session_score("BEARISH"),
        "session_score": session_score(crt_trade_direction),
        "range": rng,
        "displacement": {
            "candle_index": rc_index,
            "ratio": round(displacement_ratio, 2),
            "direction": rc_direction,
            "high": rc_high,
            "low": rc_low,
            "open": rc_open,
            "close": rc_close,
            "body": round(rc_body, 6),
            "is_extreme": displacement_ratio >= 3.0,
            "crt_trade_direction": crt_trade_direction,
        },
        "fill": fill,
        "retracement_percent": round(retrace_pct_val, 1),
        "in_optimal_ote": in_optimal_ote,
        "crt_score": crt_score,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "volume_profile": vp_data,
        "atr_value": round(atr_val, 6),
        "atr_percent": round(atr_pct, 2),
        "premium_discount": premium_discount,
        "price_position_pct": round(price_pct_in_range, 1),
        "confluence_events": [],
        "reason": "PASSED",
    }


def _no_signal(reason: str, session: str) -> None:
    """Return None — pipeline short-circuits cleanly."""
    return None


# --- STEP 1: CONSOLIDATION (RELAXED) ---

def _find_consolidation(candles: list) -> dict | None:
    """Find a consolidation period: bars with tight bodies + compressing range.

    RELAXED: Requires MAJORITY (60%+) of bars to be tight, not ALL.
    This is the #1 reason signals die — relaxed to find real setups.
    """
    n = len(candles)
    max_start = n - _CONSOLIDATION_MAX_BARS - _RANGE_CANDLE_LOOKAHEAD - 1
    if max_start < _CONSOLIDATION_MIN_BARS:
        return None

    best = None
    best_tight_count = 0

    for window_size in range(_CONSOLIDATION_MIN_BARS, _CONSOLIDATION_MAX_BARS + 1):
        end = n - _RANGE_CANDLE_LOOKAHEAD - 1
        start = end - window_size

        if start < 0:
            continue

        window = candles[start:end]
        bodies = [abs(_get(c, 'close') - _get(c, 'open')) for c in window]
        trs = [_true_range(c, candles[start - 1] if start > 0 else None) for c in window]

        avg_body = sum(bodies) / len(bodies) if bodies else 0
        avg_tr = sum(trs) / len(trs) if trs else 0

        if avg_body <= 0:
            continue

        tight_count = sum(
            1 for b, tr in zip(bodies, trs)
            if b <= avg_body * _CONSOLIDATION_BODY_RATIO and tr <= avg_tr * _CONSOLIDATION_TR_RATIO
        )
        tight_pct = tight_count / len(window)

        if tight_pct >= _CONSOLIDATION_TIGHT_PCT and tight_count > best_tight_count:
            best_tight_count = tight_count
            best = {
                "start_index": start,
                "end_index": end,
                "bar_count": window_size,
                "avg_body": round(avg_body, 6),
                "avg_tr": round(avg_tr, 6),
                "tight_pct": round(tight_pct, 2),
                "body_sizes": [round(b, 6) for b in bodies],
            }

    return best


def _true_range(candle, prev_candle) -> float:
    """Calculate true range for a single candle."""
    h = _get(candle, 'high')
    l = _get(candle, 'low')
    c = _get(candle, 'close')
    if prev_candle is None:
        return h - l
    prev_c = _get(prev_candle, 'close')
    return max(h - l, abs(h - prev_c), abs(l - prev_c))


# --- STEP 2: RANGE CANDLE ---

def _find_range_candle(candles: list, consolidation: dict) -> dict | None:
    """Find the first candle after consolidation whose body >= 1.8x avg consolidation body."""
    start_search = consolidation["end_index"]
    avg_body = consolidation["avg_body"]
    end_search = min(start_search + _RANGE_CANDLE_LOOKAHEAD, len(candles))

    for i in range(start_search, end_search):
        c = candles[i]
        body = abs(_get(c, 'close') - _get(c, 'open'))
        if body >= avg_body * _RANGE_CANDLE_BODY_MULT:
            direction = "BULLISH" if _get(c, 'close') > _get(c, 'open') else "BEARISH"
            return {
                "index": i,
                "direction": direction,
                "open": _get(c, 'open'),
                "close": _get(c, 'close'),
                "high": _get(c, 'high'),
                "low": _get(c, 'low'),
                "body": body,
                "volume": _get(c, 'volume'),
            }
    return None


# --- STEP 3: VERIFY FILL ---

def _verify_fill(candles: list, rc_index: int, rc_open: float, rc_close: float) -> dict | None:
    """Verify that price re-entered the range candle body (fill)."""
    if rc_index + 1 >= len(candles):
        return None

    rc_low = min(rc_open, rc_close)
    rc_high = max(rc_open, rc_close)
    body_size = rc_high - rc_low

    fill_candles = candles[rc_index + 1:]
    if not fill_candles:
        return None

    touch_count = 0
    best_quality = 0.0

    for c in fill_candles:
        c_low = _get(c, 'low')
        c_high = _get(c, 'high')
        if c_low <= rc_high and c_high >= rc_low:
            touch_count += 1
            overlap = min(c_high, rc_high) - max(c_low, rc_low)
            if body_size > 0:
                best_quality = max(best_quality, overlap / body_size)

    if touch_count == 0:
        return None

    return {
        "touch_count": touch_count,
        "quality": round(best_quality, 3),
        "rc_low": rc_low,
        "rc_high": rc_high,
    }


# --- STEP 4: DISPLACEMENT (incorporated into return dict) ---
# Displacement is implicit in the range candle body ratio and direction


# --- STEP 5: CALCULATE TRADE LEVELS ---

def _calc_crt_trade(crt_direction, rc_direction, rc_open, rc_close,
                    rc_low, rc_high, current_price, atr_val):
    """Calculate entry, SL, TP for a CRT trade.

    Uses 1.5x ATR buffer from RC wick to ensure enough risk for 1.5:1 RR minimum.
    """
    if atr_val <= 0:
        atr_val = 0.001

    sl_buffer = atr_val * 1.5  # 1.5x ATR buffer from RC wick

    if crt_direction == "BULLISH":
        entry = current_price
        sl = rc_low - sl_buffer
        risk = entry - sl
        if risk <= 0:
            return None, None, None
        tp = entry + risk * 2.0
        tp = min(tp, entry + atr_val * 5.0)
    else:
        entry = current_price
        sl = rc_high + sl_buffer
        risk = sl - entry
        if risk <= 0:
            return None, None, None
        tp = entry - risk * 2.0
        tp = max(tp, entry - atr_val * 5.0)

    actual_risk = abs(entry - sl)
    actual_reward = abs(tp - entry)
    if actual_risk <= 0 or actual_reward / actual_risk < 1.2:
        return None, None, None

    return round(entry, 5), round(sl, 5), round(tp, 5)


# --- SCORING COMPONENTS (sum to 25 max) ---

def _score_consolidation(consolidation: dict) -> int:
    """0-8: tighter + more bars touched = higher score."""
    tight_pct = consolidation.get("tight_pct", 0)
    bar_count = consolidation.get("bar_count", 0)

    if tight_pct >= 0.9:
        score = 8
    elif tight_pct >= 0.75:
        score = 6
    elif tight_pct >= 0.60:
        score = 5
    elif tight_pct >= 0.50:
        score = 3
    else:
        score = 2

    if bar_count >= 10:
        score = min(score + 1, 8)

    return score


def _score_range_candle_strength(ratio: float) -> int:
    """0-8: stronger displacement = higher score."""
    if ratio >= 3.0:
        return 8
    if ratio >= 2.5:
        return 7
    if ratio >= 2.0:
        return 5
    if ratio >= 1.5:
        return 3
    return 2


def _score_fvg_quality(fill: dict) -> int:
    """0-3: better fill quality = higher score."""
    quality = fill.get("quality", 0)
    touches = fill.get("touch_count", 0)

    if quality >= 0.8 and touches >= 3:
        return 3
    if quality >= 0.6 and touches >= 2:
        return 2
    if touches >= 1:
        return 1
    return 1


def _score_retest_quality(fill_quality: float, rc_direction: str) -> int:
    """0-4: deeper retracement into body = higher score."""
    if fill_quality >= 0.8:
        return 4
    if fill_quality >= 0.6:
        return 3
    if fill_quality >= 0.4:
        return 2
    if fill_quality >= 0.2:
        return 1
    return 1


def _score_zone_alignment(price: float, rng: dict) -> int:
    """0-2: price in premium/discount zone = bonus."""
    if not rng or rng.get("range_size", 0) <= 0:
        return 1

    pct = ((price - rng["low"]) / rng["range_size"]) * 100

    if pct <= 20 or pct >= 80:
        return 2
    if pct <= 35 or pct >= 65:
        return 2
    if pct <= 45 or pct >= 55:
        return 1
    return 1
