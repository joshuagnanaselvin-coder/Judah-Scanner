"""Flow Analyzer — institutional flow detection. Max flow score: 25.

CRT + SMC tell you WHERE the structure is.
Flow tells you IF real money is moving there RIGHT NOW.
Momentum tells you IF the price is about to EXPLODE.

CRT max = 40, SMC max = 20, Flow max = 25, Momentum max = 20.
Total possible = 105. No single component can carry a signal alone.

Triggers:
  1. VWAP RECLAIM       — price reclaimed session VWAP from below/above
  2. SWEEP + REVERSAL   — ICT Turtle Soup / Liquidity Grab
  3. RS vs BTC          — relative strength (outperforming/underperforming BTC)
  4. KILLZONE BONUS     — ICT killzones (London/NY open, DST-aware)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
    """Find the index of the most recent session start for VWAP computation.

    Timeframe-aware:
      1H  → 4-hour aligned UTC boundaries (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
      4H  → 4-hour aligned UTC boundaries (matching the candle open time)
      1D  → midnight UTC (00:00)
      Fallback → last 50 candles
    """
    if not candles:
        return 0

    tf = timeframe.lower()

    if tf == "1d":
        # Daily: session starts at 00:00 UTC
        target_hour = 0
        for i in range(len(candles) - 1, -1, -1):
            c = candles[i]
            ts = c.time if c.time < 4_000_000_000 else c.time / 1000
            if datetime.fromtimestamp(ts, tz=timezone.utc).hour < target_hour:
                return i + 1
        return 0

    elif tf == "4h":
        # 4H: align to 4-hour UTC boundaries (0, 4, 8, 12, 16, 20)
        target_hour = (datetime.now(timezone.utc).hour // 4) * 4
        for i in range(len(candles) - 1, -1, -1):
            c = candles[i]
            ts = c.time if c.time < 4_000_000_000 else c.time / 1000
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour < target_hour:
                return i + 1
        return 0

    elif tf == "1h":
        # 1H: 4-hour rolling session for VWAP (existing behavior)
        target_hour = (datetime.now(timezone.utc).hour // 4) * 4
        for i in range(len(candles) - 1, -1, -1):
            c = candles[i]
            ts = c.time if c.time < 4_000_000_000 else c.time / 1000
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
            if hour < target_hour:
                return i + 1
        return 0

    else:
        # 15M, 30M, or unknown: use last 50 candles as session window
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
# 4. KILLZONE MULTIPLIER — DST-aware using IANA timezones
# ─────────────────────────────────────────────────────────────────────────

# ICT killzones are defined in the MARKET'S LOCAL CLOCK TIME, not UTC.
# Local clock times never change with DST — only the UTC offset does.
# zoneinfo handles the UTC offset automatically for any given moment.
#
#  LONDON_OPEN: 08:00-11:00 London local
#     - GMT (winter): UTC+0 → 08:00-11:00 UTC
#     - BST (summer): UTC+1 → 07:00-10:00 UTC
#
#  NY_OPEN: 09:00-11:00 NY local (covers NY cash open + first 2h)
#     - EST (winter): UTC-5 → 14:00-16:00 UTC
#     - EDT (summer): UTC-4 → 13:00-15:00 UTC
#
#  OVERLAP: London + NY simultaneous
#     - GMT + EST (winter): 13:00-16:00 UTC
#     - BST + EDT (summer): 12:00-15:00 UTC
#
#  ASIA_OPEN: 09:00-11:00 Tokyo (UTC+9, no DST) → 00:00-02:00 UTC

_KILLZONE_DEFS = [
    # (name,        tz,                 local_start_hour, local_end_hour, multiplier)
    ("ASIA_OPEN",   "Asia/Tokyo",        9, 11, 1.10),
    ("LONDON_OPEN", "Europe/London",     8, 11, 1.20),
    ("NY_OPEN",     "America/New_York",  9, 11, 1.25),
    ("OVERLAP",     "America/New_York", 13, 16, 1.30),
]


def killzone_multiplier(timestamp_ms: int = None) -> dict:
    """Return the killzone state and score multiplier for a given timestamp.

    DST-aware: converts UTC to market-local time using zoneinfo, so London
    BST vs GMT and NY EDT vs EST are handled automatically.
    """
    if timestamp_ms is None:
        ts = datetime.now(timezone.utc).timestamp()
    else:
        ts = timestamp_ms / 1000 if timestamp_ms > 4_000_000_000 else timestamp_ms

    # Check each killzone — convert UTC hour to local hour for that zone
    for name, tz_name, start_local, end_local, mult in _KILLZONE_DEFS:
        local_dt = datetime.fromtimestamp(ts, tz=ZoneInfo(tz_name))
        if start_local <= local_dt.hour < end_local:
            return {
                "zone": name,
                "multiplier": mult,
                "in_killzone": True,
                "local_time": local_dt.strftime("%H:%M"),
                "tz": str(local_dt.tzinfo),
            }

    return {
        "zone": "OFF_HOURS",
        "multiplier": 0.85,
        "in_killzone": False,
        "local_time": datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York")).strftime("%H:%M ET"),
        "tz": "OFF",
    }


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

    # 1. VWAP reclaim (max 7)
    vwap = detect_vwap_reclaim(candles, timeframe)
    if vwap:
        triggers.append(vwap)

    # 2. Multi-bar sweep + reversal (max 7)
    sweep = detect_sweep_reversal(candles, swings)
    if sweep:
        triggers.append(sweep)

    # 3. Relative strength vs BTC (max 6)
    if btc_candles:
        rs = compute_relative_strength(candles, btc_candles)
        if rs and rs["weight"] > 0:
            triggers.append(rs)

    # 4. Killzone (bonus multiplier applied to total, max 5 pts of value)
    kz = killzone_multiplier()

    # Fixed per-trigger point values matching spec
    _TRIGGER_MAX = {
        "vwap_reclaim_bullish": 7, "vwap_reclaim_bearish": 7,
        "sweep_reversal_bullish": 7, "sweep_reversal_bearish": 7,
        "rs_extreme_bullish": 6, "rs_bullish": 6,
        "rs_extreme_bearish": 6, "rs_bearish": 6,
    }
    raw_boost = sum(_TRIGGER_MAX.get(t.get("name", ""), 0) for t in triggers)
    raw_boost = min(raw_boost, 25)

    # Killzone multiplier applied as percentage adjustment
    kz_mult = kz["multiplier"]
    adjusted_boost = int(raw_boost * kz_mult)
    adjusted_boost = min(adjusted_boost, 25)

    # Direction: tally total pts per side, highest total wins
    _dir_map = {
        "vwap_reclaim_bullish": "BULLISH", "vwap_reclaim_bearish": "BEARISH",
        "sweep_reversal_bullish": "BULLISH", "sweep_reversal_bearish": "BEARISH",
        "rs_extreme_bullish": "BULLISH", "rs_bullish": "BULLISH",
        "rs_extreme_bearish": "BEARISH", "rs_bearish": "BEARISH",
    }
    bull_pts = sum(_TRIGGER_MAX.get(t.get("name", ""), 0)
                   for t in triggers if _dir_map.get(t.get("name", "")) == "BULLISH")
    bear_pts = sum(_TRIGGER_MAX.get(t.get("name", ""), 0)
                   for t in triggers if _dir_map.get(t.get("name", "")) == "BEARISH")
    if bull_pts >= bear_pts and bull_pts > 0:
        direction = "BULLISH"
    elif bear_pts > bull_pts:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    # Buying pressure: ratio of bullish to total flow pts
    total_pts = bull_pts + bear_pts
    buy_pct = round((bull_pts / total_pts) * 100, 1) if total_pts > 0 else 50.0

    return {
        "boost": adjusted_boost,
        "triggers": triggers,
        "killzone": kz,
        "is_flowing": adjusted_boost >= 5,  # at least one significant trigger (lowered from 6)
        "direction": direction,
        "buying_pressure_pct": buy_pct,
        "bullish_pts": bull_pts,
        "bearish_pts": bear_pts,
        "flow_pct": round(adjusted_boost / 25 * 100, 1),  # % of max flow score (25)
    }