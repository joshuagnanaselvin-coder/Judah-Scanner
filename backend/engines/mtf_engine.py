"""Multi-Timeframe Alignment Engine."""
import logging

logger = logging.getLogger("judah.mtf")


def _cv(candle, key):
    """Get value from candle — handles both dict and dataclass."""
    if isinstance(candle, dict):
        return candle.get(key, 0)
    return getattr(candle, key, 0)


class MTFAnalyzer:
    """
    Multi-Timeframe Analysis: checks if lower-TF setup aligns with
    higher-TF structure. A 1h BULLISH OB is much stronger if 4h is also
    bullish. A 4h BEARISH MSB is more reliable if 1d trend is bearish.
    """

    def __init__(self, candle_store):
        self.candle_store = candle_store  # {symbol_tf: [candles]}

    def get_trend(self, symbol, timeframe):
        """Get trend direction for a symbol on a timeframe using EMA cross."""
        candles = self.candle_store.get(f"{symbol}_{timeframe}", [])
        if len(candles) < 21:
            return 'NEUTRAL'

        try:
            closes = [_cv(c, 'close') for c in candles]
            ema_fast = sum(closes[-8:]) / 8
            ema_slow = sum(closes[-21:]) / 21

            if ema_slow == 0:
                return 'NEUTRAL'

            ratio = ema_fast / ema_slow
            if ratio > 1.005:
                return 'BULLISH'
            elif ratio < 0.995:
                return 'BEARISH'
            return 'NEUTRAL'
        except (TypeError, IndexError):
            return 'NEUTRAL'

    def alignment_score(self, symbol, signal_tf, signal_direction):
        """
        Score how well the signal aligns with higher timeframes.

        Check order: 1h -> 4h -> 1d
        - If signal is 1h: check 4h and 1d alignment
        - If signal is 4h: check 1d alignment only
        - If signal is 1d: no higher TF to check (score = 0)
        """
        tf_hierarchy = {'1h': ['4h', '1d'], '4h': ['1d'], '1d': []}
        check_tfs = tf_hierarchy.get(signal_tf, [])

        if not check_tfs:
            return 0, []

        alignment_details = []
        score = 0

        for htf in check_tfs:
            trend = self.get_trend(symbol, htf)
            aligned = (trend == signal_direction)

            if aligned:
                score += 2  # +2 per aligned TF (max 4)
                alignment_details.append(f"{htf}={trend} OK")
            else:
                alignment_details.append(f"{htf}={trend} MISMATCH")

        return min(4, score), alignment_details
