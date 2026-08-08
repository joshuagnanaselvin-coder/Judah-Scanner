"""Regime Engine — per-pair market regime detection.

Classifies each coin into one of four regimes:
  TRENDING  — directional move with momentum
  RANGING   — sideways movement within bounds
  VOLATILE  — high ATR, choppy, no clear direction
  COMPRESSING — low ATR, tight range, building energy

Regime modifies scoring weights:
  TRENDING  → Momentum +20%, CRT -10%
  RANGING   → CRT +20%, Momentum -10%
  VOLATILE  → Flow +15%, min score 65
  COMPRESSING → all scores -5% (too early to call direction)
"""
import logging
from typing import Optional

from backend.market_data import market_data
from backend.helpers.candle_math import atr, calc_envelope
from backend.helpers.volume_profile import compute_volume_profile
from backend.config import (
    REGIME_ATR_PERIOD, REGIME_TREND_SLOPE_PERIOD,
    REGIME_VP_WIDTH_PERCENT, REGIME_MIN_BARS,
)

logger = logging.getLogger("judah.regime")

# Regime-to-scoring modifier map
REGIME_MODIFIERS = {
    "TRENDING":    {"momentum_mult": 1.2, "crt_mult": 0.9, "flow_mult": 1.0, "min_score": 50},
    "RANGING":     {"momentum_mult": 0.9, "crt_mult": 1.2,  "flow_mult": 1.0, "min_score": 50},
    "VOLATILE":    {"momentum_mult": 1.0, "crt_mult": 0.9,  "flow_mult": 1.15, "min_score": 65},
    "COMPRESSING": {"momentum_mult": 0.95, "crt_mult": 0.95, "flow_mult": 1.0, "min_score": 60},
    "UNKNOWN":     {"momentum_mult": 1.0, "crt_mult": 1.0,  "flow_mult": 1.0, "min_score": 50},
}


def detect_regime(symbol: str, timeframe: str = "1H") -> dict:
    """Detect market regime for a coin on a given timeframe.

    Uses ATR ratio, trend slope, and volume profile width to classify.

    Returns:
        dict with "regime", "confidence", "atr_pct", "trend_slope", "modifiers"
    """
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < REGIME_MIN_BARS:
        return _default_regime()

    last_price = candles[-1].close
    atr_val = atr(candles, period=REGIME_ATR_PERIOD)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0

    # Trend slope: linear regression on closes over TREND_SLOPE_PERIOD
    slope = _calc_trend_slope(candles, REGIME_TREND_SLOPE_PERIOD)

    # Volume profile width: narrow = compression, wide = expansion
    vp_width = _calc_vp_width(candles)

    # Classification logic
    regime, confidence = _classify_regime(atr_pct, slope, vp_width, last_price)

    modifiers = REGIME_MODIFIERS.get(regime, REGIME_MODIFIERS["UNKNOWN"])

    return {
        "regime": regime,
        "confidence": confidence,
        "atr_pct": round(atr_pct, 3),
        "trend_slope": round(slope, 6),
        "vp_width_pct": round(vp_width, 2),
        "modifiers": modifiers,
    }


def get_regime_modifier(symbol: str, component: str) -> float:
    """Get a scoring modifier for a specific component (momentum, crt, flow)."""
    regime_data = detect_regime(symbol)
    modifiers = regime_data.get("modifiers", REGIME_MODIFIERS["UNKNOWN"])
    key = f"{component}_mult"
    return modifiers.get(key, 1.0)


def get_min_score_for_regime(symbol: str) -> int:
    """Get minimum score threshold for a pair's current regime."""
    regime_data = detect_regime(symbol)
    modifiers = regime_data.get("modifiers", REGIME_MODIFIERS["UNKNOWN"])
    return modifiers.get("min_score", 50)


# ── Private Helpers ─────────────────────────────────────────────────────

def _default_regime() -> dict:
    return {
        "regime": "UNKNOWN",
        "confidence": 0,
        "atr_pct": 0.0,
        "trend_slope": 0.0,
        "vp_width_pct": 0.0,
        "modifiers": REGIME_MODIFIERS["UNKNOWN"],
    }


def _calc_trend_slope(candles: list, period: int) -> float:
    """Linear regression slope of closes over the last `period` candles."""
    if len(candles) < period:
        period = len(candles)
    if period < 2:
        return 0.0

    closes = [c.close for c in candles[-period:]]
    n = len(closes)
    x_mean = (n - 1) / 2.0
    y_mean = sum(closes) / n

    numerator = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return 0.0
    return numerator / denominator


def _calc_vp_width(candles: list) -> float:
    """Volume profile width as percentage of price (VAH - VAL) / price."""
    try:
        vp = compute_volume_profile(candles, bins=20)
        va_high = vp.get("va_high", 0)
        va_low = vp.get("va_low", 0)
        price = candles[-1].close
        if price > 0 and va_high > va_low:
            return ((va_high - va_low) / price) * 100
    except Exception:
        pass
    return 0.0


def _classify_regime(atr_pct: float, slope: float, vp_width: float, price: float) -> tuple:
    """Classify regime based on ATR%, trend slope, and VP width.

    Returns (regime_str, confidence_int).
    """
    # Volatility thresholds (ATR% based on typical crypto behavior)
    HIGH_ATR = 3.0      # High volatility
    LOW_ATR = 0.5       # Low volatility (compression)

    # Trend slope threshold (normalized)
    slope_pct = (slope / price * 100) if price > 0 else 0.0
    STRONG_TREND = 0.3  # 0.3% per bar = strong trend

    # VP width threshold
    NARROW_VP = 5.0     # VP width < 5% = compression
    WIDE_VP = 15.0      # VP width > 15% = expansion

    confidence = 50

    # Volatile: high ATR, no clear slope
    if atr_pct >= HIGH_ATR and abs(slope_pct) < STRONG_TREND:
        confidence = 70
        return "VOLATILE", confidence

    # Compressing: low ATR + narrow VP
    if atr_pct <= LOW_ATR and vp_width < NARROW_VP:
        confidence = 75
        return "COMPRESSING", confidence

    # Trending: clear slope direction
    if abs(slope_pct) >= STRONG_TREND:
        confidence = 80 if abs(slope_pct) >= STRONG_TREND * 2 else 60
        return "TRENDING", confidence

    # Ranging: moderate ATR, no clear slope, moderate VP width
    if NARROW_VP <= vp_width <= WIDE_VP:
        confidence = 55
        return "RANGING", confidence

    # Default: unknown / transitioning
    confidence = 40
    return "UNKNOWN", confidence
