"""Pre-scan filter — skip coins that aren't worth scanning."""
import logging
from backend.helpers.candle_math import atr_percent
from backend.config import (
    MIN_ATR_PERCENT, ADAPTIVE_ATR_MIN_ABSOLUTE,
    MIN_PRICE_CHANGE_4H_PCT, MIN_24H_VOLUME_USDT,
)

logger = logging.getLogger("judah.pre_filter")

def should_scan(symbol: str, candles_by_tf: dict) -> bool:
    candles = candles_by_tf.get("4H")
    if not candles or len(candles) < 20:
        logger.debug(f"[prefilter] {symbol}: no candles or < 20")
        return False

    atr_p = atr_percent(candles)
    last_price = candles[-1].close if candles else 0
    atr_val = (atr_p / 100.0) * last_price if last_price > 0 else 0

    if atr_p < MIN_ATR_PERCENT or atr_val < ADAPTIVE_ATR_MIN_ABSOLUTE:
        logger.debug(f"[prefilter] {symbol}: ATR {atr_p:.3f}% / {atr_val:.6f} below threshold "
                      f"{MIN_ATR_PERCENT}% / {ADAPTIVE_ATR_MIN_ABSOLUTE}")
        return False

    if len(candles) >= 3:
        old = candles[-3].close
        new = candles[-1].close
        if old > 0 and abs(new - old) / old * 100 < MIN_PRICE_CHANGE_4H_PCT:
            logger.debug(f"[prefilter] {symbol}: price change {abs(new-old)/old*100:.3f}% < {MIN_PRICE_CHANGE_4H_PCT}%")
            return False

    logger.debug(f"[prefilter] {symbol}: PASSED")
    return True
