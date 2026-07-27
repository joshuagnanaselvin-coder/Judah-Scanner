"""Swing points and FVG detection — ICT methodology."""
from backend.helpers.candle_math import body_pct_of_range

def detect_swing_points(candles: list, lookback: int = 3) -> dict:
    if not candles or len(candles) < lookback * 2 + 1:
        return {"swing_highs": [], "swing_lows": []}

    highs, lows = [], []
    for i in range(lookback, len(candles) - lookback):
        c = candles[i]
        left = candles[i - lookback:i]
        right = candles[i + 1:i + lookback + 1]

        if all(c.high >= x.high for x in left) and all(c.high >= x.high for x in right):
            highs.append({
                "index": i, "price": round(c.high, 2), "time": c.time,
                "body_quality": round(body_pct_of_range(c), 3),
            })
        if all(c.low <= x.low for x in left) and all(c.low <= x.low for x in right):
            lows.append({
                "index": i, "price": round(c.low, 2), "time": c.time,
                "body_quality": round(body_pct_of_range(c), 3),
            })
    return {"swing_highs": highs, "swing_lows": lows}

def detect_fvg(candles: list, lookback: int = 20) -> list:
    """ICT Fair Value Gap: 3-candle imbalance (gap between candle i-1 and i+1)."""
    if not candles or len(candles) < 3: return []

    fvgs = []
    start = max(1, len(candles) - lookback)

    for i in range(start, len(candles) - 1):
        prev, nxt = candles[i-1], candles[i+1]

        # Bullish FVG: gap between prev high and next low
        if prev.high < nxt.low:
            fvgs.append({
                "type": "BULLISH", "candle_index": i,
                "top": round(nxt.low, 2), "bottom": round(prev.high, 2),
                "size": round(nxt.low - prev.high, 2), "filled": False,
            })
        # Bearish FVG: gap between prev low and next high
        if prev.low > nxt.high:
            fvgs.append({
                "type": "BEARISH", "candle_index": i,
                "top": round(prev.low, 2), "bottom": round(nxt.high, 2),
                "size": round(prev.low - nxt.high, 2), "filled": False,
            })

    # Mark filled FVGs
    last_close = candles[-1].close
    for fvg in fvgs:
        if fvg["type"] == "BULLISH" and last_close > fvg["top"]:
            fvg["filled"] = True
        if fvg["type"] == "BEARISH" and last_close < fvg["bottom"]:
            fvg["filled"] = True

    return fvgs
