"""Volume Profile computation — POC and Value Area."""
def compute_volume_profile(candles: list, num_levels: int = 12) -> dict:
    if not candles or len(candles) < 10: return {}

    prices = []
    for c in candles:
        prices.extend([c.high, c.low])
    pmin, pmax = min(prices), max(prices)
    if pmin == pmax: return {}

    step = (pmax - pmin) / num_levels
    levels = []

    for i in range(num_levels):
        ll = pmin + i * step
        lh = ll + step
        mid = (ll + lh) / 2
        vol = 0
        for c in candles:
            if c.low >= lh or c.high <= ll: continue
            ol = max(c.low, ll)
            oh = min(c.high, lh)
            if c.high > c.low:
                vol += c.volume * (oh - ol) / (c.high - c.low)
        levels.append({"price": round(mid, 2), "volume": round(vol, 2),
                       "low": round(ll, 2), "high": round(lh, 2)})

    sorted_vol = sorted(levels, key=lambda x: x["volume"], reverse=True)
    poc = sorted_vol[0]
    total_vol = sum(l["volume"] for l in levels)
    target = total_vol * 0.70

    va_levels = [poc]
    va_vol = poc["volume"]
    poc_idx = levels.index(poc)
    left, right = poc_idx - 1, poc_idx + 1

    while va_vol < target:
        lv = levels[left]["volume"] if left >= 0 else 0
        rv = levels[right]["volume"] if right < len(levels) else 0
        if lv >= rv and left >= 0:
            va_levels.append(levels[left]); va_vol += levels[left]["volume"]; left -= 1
        elif right < len(levels):
            va_levels.append(levels[right]); va_vol += levels[right]["volume"]; right += 1
        else:
            break

    return {
        "poc_price": poc["price"],
        "poc_volume": poc["volume"],
        "va_high": round(max(l["high"] for l in va_levels), 2),
        "va_low": round(min(l["low"] for l in va_levels), 2),
        "levels": sorted(levels, key=lambda x: x["price"]),
    }
