"""Single engine file — scan any coin on any timeframe."""
from backend.engines.crt_engine import run_crt
from backend.engines.smc_engine import run_smc
from backend.engines.signal_builder import build_signal
from backend.market_data import market_data
from backend.helpers.candle_math import atr, atr_percent, calc_envelope, _get
from backend.config import (
    MIN_ATR_PERCENT, MIN_ATR_ABSOLUTE, MIN_RANGE_MULTIPLIER,
)
import logging

logger = logging.getLogger("judah.engine")

def scan(symbol: str, timeframe: str) -> dict | None:
    """Run full CRT -> SMC -> Signal pipeline for one coin on one timeframe."""
    candles = market_data.get_candles(symbol, timeframe)
    if not candles or len(candles) < 50:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: no candles ({len(candles) if candles else 0})")
        return None

    last_price = _get(candles[-1], 'close')

    # Volatility gate
    atr_val = atr(candles)
    atr_pct = (atr_val / last_price * 100) if last_price > 0 else 0.0
    if atr_pct < MIN_ATR_PERCENT or atr_val < MIN_ATR_ABSOLUTE:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: ATR {atr_val:.6f} ({atr_pct:.3f}%) below threshold")
        return None

    # Range size gate
    env = calc_envelope(candles, 50)
    range_size = env.get('range_size', 0)
    if range_size < atr_val * MIN_RANGE_MULTIPLIER:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: range {range_size:.6f} < {MIN_RANGE_MULTIPLIER}x ATR")
        return None

    # CRT
    logger.debug(f"[engine] Running CRT for {symbol} {timeframe} ({len(candles)} candles, last={last_price:.5f})")
    crt = run_crt(candles)
    if not crt:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: CRT returned None")
        return None
    logger.debug(f"[engine] CRT passed {symbol} {timeframe}: score={crt.get('crt_score',0)} dir={crt.get('displacement',{}).get('crt_trade_direction','?')}")

    # SMC
    smc = run_smc(candles, crt)
    if not smc:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: SMC returned None")
        return None
    logger.debug(f"[engine] SMC passed {symbol} {timeframe}: score={smc.get('smc_score',0)}")

    # Signal builder
    logger.debug(f"[engine] Building signal for {symbol} {timeframe}")
    signal = build_signal(symbol, timeframe, crt, smc, candles)
    if signal:
        logger.info(f"[engine] SIGNAL {symbol} {timeframe}: {signal['tier']} score={signal['composite_score']} "
                     f"dir={signal['direction']} rr={signal['rr']:.1f} entry={signal['entry']:.5f} "
                     f"sl={signal['stop_loss']:.5f} tp={signal['take_profit']:.5f}")
    else:
        logger.debug(f"[engine] SKIP {symbol} {timeframe}: build_signal returned None")
    return signal
