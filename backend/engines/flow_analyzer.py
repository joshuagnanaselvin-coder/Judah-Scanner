"""Flow Analyzer — institutional flow detection beyond CRT.

These are the "flow is moving" signals that CRT alone misses:

  1. VWAP RECLAIM       — price reclaimed the session VWAP from below/above
  2. SWEEP + REVERSAL   — liquidity sweep occurred in the last 3-5 bars
                          followed by a reversal candle (not just last candle)
  3. RS vs BTC          — coin's 1H return >> BTC's 1H return (relative strength)
  4. KILLZONE BONUS     — London/NY open windows (8-11 UTC, 13-16 UTC)

All triggers return a weighted contribution that flows into the composite
score through the engine.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("judah.flow")


# ─────────────────────────────────────────────────────────────────────────
# 1. VWAP RECLAIM
# ─────────────────────────────────────────────────────────────────────────

def compute_session_vwap(candles: list, session_start_index: int) -> Optional[float]:
    """Compute VWAP from `session_start_index` to current bar.

    VWAP = Σ(typical_price * volume) / Σ(volume)
    Typical price = (high + low + close) / 3
    """
    if session_start_index < 0 or session_start_index >= len(candles):
        return None

    session = candles[session_start_index:]
    if not session:
        return None

    pv_sum = 0.0
    v_sum = 0.0
    for c in session:
        typical = (c.high + c.low + c.close) / 3.0
        pv_sum += typical * c.volume
        v_sum += c.volume

    return pv_sum / v_sum if v_sum > 0 else None


def _find_session_start(candles: list, timeframe: str = "1h") -> int:
    """Find the index of the most recent session start (00:00 UTC for daily,
    or use 4h/8h boundaries for 1H chart)."""
    if not candles:
        return 0

    if timeframe.lower() in ("1h", "1H"):
        # 4-hour rolling session for VWAP
        target_hour = (datetime.now(timezone.utc).hour // 4) * 4
        for i in range(len(candles) - 1, -1, -1):
            c = candles[i]
            ts = c.time if c.time < 4_000_000_000 else c.time / 1000
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour < target_hour:
                return i + 1
        return 0
    else:
        # 4H / 1D: start of window
        return max(0, len(candles) - 50)


def detect_vwap_reclaim(candles: list, timeframe: str = "1h") -> Optional[dict]:
    """Detect VWAP reclaim: price was below VWAP, now reclaiming.

    A reclaim is a bullish move where price closes above VWAP after being
    below for 3+ bars.
    """
    if len(candles) < 10:
        return None

    sess_start = _find_session_start(candles, timeframe)
    vwap = compute_session_vwap(candles, sess_start)
    if vwap is None or vwap <= 0:
        return None

    last = candles[-1]
    prior_bars = candles[-10:-1]  # 9 bars before last

    # Count bars that closed below VWAP in the prior 9
    below_count = sum(1 for c in prior_bars if c.close < vwap)
    if below_count < 3:
        return None

    # Reclaim = last bar closes above VWAP
    if last.close > vwap:
        # Quality: how far above VWAP? how many bars below before reclaim?
        distance_pct = ((last.close - vwap) / vwap) * 100
        weight = 2 if below_count >= 6 else 1
        return {
            "name": "vwap_reclaim_bullish",
            "vwap": round(vwap, 8),
            "bars_below": below_count,
            "distance_pct": round(distance_pct, 2),
            "weight": weight,
        }

    # Bearish: above VWAP for 3+ bars, now below
    above_count = sum(1 for c in prior_bars if c.close > vwap)
    if above_count < 3:
        return None
    if last.close < vwap:
        distance_pct = ((vwap - last.close) / vwap) * 100
        weight = 2 if above_count >= 6 else 1
        return {
            "name": "vwap_reclaim_bearish",
            "vwap": round(vwap, 8),
            "bars_above": above_count,
            "distance_pct": round(distance_pct, 2),
            "weight": weight,
        }

    return None


# ─────────────────────────────────────────────────────────────────────────
# 2. MULTI-BAR LIQUIDITY SWEEP + REVERSAL
# ─────────────────────────────────────────────────────────────────────────

def detect_sweep_reversal(candles: list, swings: dict, lookback: int = 5) -> Optional[dict]:
    """Detect liquidity sweep + immediate reversal in the last `lookback` bars.

    Pattern:
      - N bars ago: candle wicked beyond a swing low/high (the sweep)
      - Within 1-3 bars after: a strong reversal candle (body > 1.5× avg) closes
        back through the swing level in the opposite direction

    This is the ICT "Turtle Soup" / "Liquidity Grab" pattern.
    """
    if not swings or len(candles) < lookback + 5:
        return None

    recent = candles[-lookback:]
    avg_body = sum(abs(c.close - c.open) for c in candles[-20:]) / 20
    if avg_body <= 0:
        return None

    swing_lows = swings.get("swing_lows", [])[-5:]
    swing_highs = swings.get("swing_highs", [])[-5:]

    # ── BULLISH SWEEP + REVERSAL ─────────────────────────────────────
    for sl in swing_lows:
        sl_price = sl["price"] if isinstance(sl, dict) else sl
        # Find the bar that wicked below
        sweep_bar_idx = None
        for i, c in enumerate(recent):
            if c.low < sl_price:
                sweep_bar_idx = i
                break
        if sweep_bar_idx is None:
            continue
        # Bar after sweep must be bullish reversal with body > 1.5x avg
        for j in range(sweep_bar_idx + 1, len(recent)):
            c = recent[j]
            body = c.close - c.open
            if body > 0 and body >= avg_body * 1.5 and c.close > sl_price:
                # Quality: distance swept + reversal strength
                sweep_depth = (sl_price - recent[sweep_bar_idx].low) / sl_price * 100
                reversal_strength = body / avg_body
                weight = 2 if (sweep_depth > 0.3 and reversal_strength > 2.0) else 1
                return {
                    "name": "sweep_reversal_bullish",
                    "swing_level": sl_price,
                    "sweep_depth_pct": round(sweep_depth, 2),
                    "reversal_strength": round(reversal_strength, 2),
                    "weight": weight,
                }

    # ── BEARISH SWEEP + REVERSAL ─────────────────────────────────────
    for sh in swing_highs:
        sh_price = sh["price"] if isinstance(sh, dict) else sh
        sweep_bar_idx = None
        for i, c in enumerate(recent):
            if c.high > sh_price:
                sweep_bar_idx = i
                break
        if sweep_bar_idx is None:
            continue
        for j in range(sweep_bar_idx + 1, len(recent)):
            c = recent[j]
            body = c.open - c.close  # bearish body
            if body > 0 and body >= avg_body * 1.5 and c.close < sh_price:
                sweep_depth = (recent[sweep_bar_idx].high - sh_price) / sh_price * 100
                reversal_strength = body / avg_body
                weight = 2 if (sweep_depth > 0.3 and reversal_strength > 2.0) else 1
                return {
                    "name": "sweep_reversal_bearish",
                    "swing_level": sh_price,
                    "sweep_depth_pct": round(sweep_depth, 2),
                    "reversal_strength": round(reversal_strength, 2),
                    "weight": weight,
                }

    return None


# ─────────────────────────────────────────────────────────────────────────
# 3. RELATIVE STRENGTH vs BTC
# ─────────────────────────────────────────────────────────────────────────

def compute_relative_strength(symbol_candles: list, btc_candles: list,
                                lookback: int = 4) -> Optional[dict]:
    """Compute the coin's relative strength vs BTC over `lookback` bars.

    RS = coin_return_pct - btc_return_pct

    Returns dict with rs_pct, strong (rs_pct > 1.5%), weak (rs_pct < -1.5%), or neutral.
    """
    if not symbol_candles or not btc_candles:
        return None
    if len(symbol_candles) < lookback + 1 or len(btc_candles) < lookback + 1:
        return None

    coin_now = symbol_candles[-1].close
    coin_then = symbol_candles[-lookback - 1].close
    btc_now = btc_candles[-1].close
    btc_then = btc_candles[-lookback - 1].close

    if coin_then <= 0 or btc_then <= 0:
        return None

    coin_return = ((coin_now - coin_then) / coin_then) * 100
    btc_return = ((btc_now - btc_then) / btc_then) * 100
    rs = coin_return - btc_return

    if rs >= 3.0:
        return {
            "name": "rs_extreme_bullish",
            "rs_pct": round(rs, 2),
            "coin_return": round(coin_return, 2),
            "btc_return": round(btc_return, 2),
            "weight": 2,
        }
    if rs >= 1.5:
        return {
            "name": "rs_bullish",
            "rs_pct": round(rs, 2),
            "coin_return": round(coin_return, 2),
            "btc_return": round(btc_return, 2),
            "weight": 1,
        }
    if rs <= -3.0:
        return {
            "name": "rs_extreme_bearish",
            "rs_pct": round(rs, 2),
            "coin_return": round(coin_return, 2),
            "btc_return": round(btc_return, 2),
            "weight": 2,
        }
    if rs <= -1.5:
        return {
            "name": "rs_bearish",
            "rs_pct": round(rs, 2),
            "coin_return": round(coin_return, 2),
            "btc_return": round(btc_return, 2),
            "weight": 1,
        }
    return {
        "name": "rs_neutral",
        "rs_pct": round(rs, 2),
        "coin_return": round(coin_return, 2),
        "btc_return": round(btc_return, 2),
        "weight": 0,
    }


# ─────────────────────────────────────────────────────────────────────────
# 4. KILLZONE MULTIPLIER
# ─────────────────────────────────────────────────────────────────────────

# ICT killzones (UTC) — institutional high-probability windows
KILLZONES = {
    "ASIA_OPEN":   (0, 2),     # 00:00-02:00 — Asian open
    "LONDON_OPEN": (7, 10),    # 07:00-10:00 — London open
    "NY_OPEN":     (12, 15),   # 12:00-15:00 — NY open (with overlap)
    "NY_LUNCH":    (17, 19),   # 17:00-19:00 — typically choppy, skip
}


def killzone_multiplier(timestamp_ms: int = None) -> dict:
    """Return the killzone state and score multiplier.

    The multiplier boosts flow signals during high-probability windows.
    """
    if timestamp_ms is None:
        ts = datetime.now(timezone.utc).timestamp()
    else:
        ts = timestamp_ms / 1000 if timestamp_ms > 4_000_000_000 else timestamp_ms
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

    for zone, (start, end) in KILLZONES.items():
        if start <= hour < end:
            mult = {"LONDON_OPEN": 1.20, "NY_OPEN": 1.25, "ASIA_OPEN": 1.10}.get(zone, 1.0)
            return {"zone": zone, "multiplier": mult, "in_killzone": True}

    return {"zone": "OFF_HOURS", "multiplier": 1.0, "in_killzone": False}


# ─────────────────────────────────────────────────────────────────────────
# PUBLIC API: FLOW ANALYZER
# ─────────────────────────────────────────────────────────────────────────

def analyze_flow(symbol: str, candles: list, swings: dict,
                 timeframe: str = "1h",
                 btc_candles: list = None) -> dict:
    """Run all flow triggers and return aggregated boost.

    Returns:
      {
        "boost": int,             # total score boost (sum of trigger weights × killzone mult)
        "triggers": list[dict],   # all triggered conditions
        "killzone": dict,         # current killzone state
        "is_flowing": bool,       # True if any significant flow detected
        "direction": str | None,  # inferred dominant direction
      }
    """
    triggers = []

    # 1. VWAP reclaim
    vwap = detect_vwap_reclaim(candles, timeframe)
    if vwap:
        triggers.append(vwap)

    # 2. Multi-bar sweep + reversal
    sweep = detect_sweep_reversal(candles, swings)
    if sweep:
        triggers.append(sweep)

    # 3. Relative strength vs BTC
    if btc_candles:
        rs = compute_relative_strength(candles, btc_candles)
        if rs and rs["weight"] > 0:
            triggers.append(rs)

    # 4. Killzone
    kz = killzone_multiplier()
    if not kz["in_killzone"]:
        kz = {"zone": "OFF_HOURS", "multiplier": 0.85, "in_killzone": False}  # slight penalty

    # Aggregate
    weight_total = sum(t.get("weight", 1) for t in triggers)
    raw_boost = weight_total * 10  # 10 points per weight unit
    adjusted_boost = int(raw_boost * kz["multiplier"])

    # Direction: sweep > vwap > rs
    direction = None
    for t in triggers:
        if "bullish" in t["name"] or t["name"] in ("rs_bullish", "rs_extreme_bullish"):
            direction = "BULLISH"; break
        if "bearish" in t["name"] or t["name"] in ("rs_bearish", "rs_extreme_bearish"):
            direction = "BEARISH"; break

    return {
        "boost": adjusted_boost,
        "triggers": triggers,
        "killzone": kz,
        "is_flowing": len(triggers) >= 1 and weight_total >= 2,
        "direction": direction,
        "raw_weight": weight_total,
    }