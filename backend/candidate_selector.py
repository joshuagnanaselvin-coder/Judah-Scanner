"""Candidate Selection Engine — universal pre-filter for any dimension/timeframe.

Adaptive ATR threshold: each coin's threshold = 60% of its own rolling ATR baseline.
No fixed percentages per timeframe. Self-tuning per coin.

Pipeline: 529 coins → ~120 candidates
"""
import logging
from typing import Optional
from backend.helpers.candle_math import atr, atr_percent
from backend.market_data import market_data
from backend.config import (
    ADAPTIVE_ATR_LOOKBACK,
    ADAPTIVE_ATR_MIN_MULTIPLIER,
    ADAPTIVE_ATR_BASELINE_MIN_PCT,
    ADAPTIVE_ATR_BASELINE_MAX_PCT,
    MIN_PRICE_CHANGE_PCT,
)

logger = logging.getLogger("judah.candidate")


def adaptive_atr_threshold(candles: list, lookback: int = 50, multiplier: float = 0.60) -> float:
    """Compute adaptive ATR% threshold for a coin based on its own volatility baseline.

    Returns the minimum ATR% this coin must exceed to be considered "active."
    Clamped between ADAPTIVE_ATR_BASELINE_MIN_PCT and ADAPTIVE_ATR_BASELINE_MAX_PCT.
    """
    if len(candles) < lookback + 1:
        return ADAPTIVE_ATR_BASELINE_MIN_PCT

    baseline_atr = atr(candles, period=lookback)
    last_price = candles[-1].close if candles else 0

    if last_price <= 0 or baseline_atr <= 0:
        return ADAPTIVE_ATR_BASELINE_MIN_PCT

    baseline_pct = (baseline_atr / last_price) * 100
    threshold = baseline_pct * multiplier
    threshold = max(threshold, ADAPTIVE_ATR_BASELINE_MIN_PCT)
    threshold = min(threshold, ADAPTIVE_ATR_BASELINE_MAX_PCT)
    return threshold


def should_select(symbol: str, timeframe: str) -> bool:
    """Universal candidate selection — works for any timeframe.

    Args:
        symbol: Trading pair (e.g. "KAVAUSDT")
        timeframe: Any supported TF ("1H", "4H", "15M", "1D", etc.)

    Returns True if the coin is active enough to warrant scanning.
    """
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < 20:
        logger.debug(f"[candidate] {symbol} {timeframe}: insufficient candles ({len(candles) if candles else 0})")
        return False

    last_price = candles[-1].close
    if last_price <= 0:
        return False

    # Adaptive ATR gate
    atr_val = atr(candles)
    atr_pct = atr_percent(candles)
    threshold = adaptive_atr_threshold(candles)

    if atr_pct < threshold:
        logger.debug(f"[candidate] {symbol} {timeframe}: ATR {atr_pct:.3f}% < adaptive threshold {threshold:.3f}%")
        return False

    # Absolute ATR floor (prevents dust coins)
    if atr_val < 0.00001:
        logger.debug(f"[candidate] {symbol} {timeframe}: ATR {atr_val:.8f} below absolute floor")
        return False

    # Price movement gate — skip coins that haven't moved
    if len(candles) >= 3:
        old_close = candles[-3].close
        if old_close > 0:
            move_pct = abs(last_price - old_close) / old_close * 100
            if move_pct < MIN_PRICE_CHANGE_PCT:
                logger.debug(f"[candidate] {symbol} {timeframe}: move {move_pct:.3f}% < {MIN_PRICE_CHANGE_PCT}%")
                return False

    logger.debug(f"[candidate] {symbol} {timeframe}: PASSED (ATR {atr_pct:.3f}% >= {threshold:.3f}%)")
    return True


def get_candidates(symbols: list, timeframe: str) -> list[str]:
    """Run candidate selection on all symbols for a given timeframe.

    Returns list of symbols that passed the filter.
    """
    return [s for s in symbols if should_select(s, timeframe)]
