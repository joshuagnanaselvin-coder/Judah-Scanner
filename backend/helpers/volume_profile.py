"""Volume Profile: Point of Control, Value Area, HVN/LVN nodes."""
import logging

logger = logging.getLogger("judah.vp")


def _cv(candle, key):
    """Get value from candle — handles both dict and dataclass."""
    if isinstance(candle, dict):
        return candle.get(key, 0)
    return getattr(candle, key, 0)


def compute_volume_profile(candles: list, num_levels: int = 12) -> dict:
    """Standalone function: compute POC, VAH, VAL from candle volume data.

    Returns dict with keys: poc_price, poc_volume, va_high, va_low, levels
    """
    if not candles or len(candles) < 10:
        return {}

    prices = []
    for c in candles:
        prices.extend([_cv(c, 'high'), _cv(c, 'low')])
    pmin, pmax = min(prices), max(prices)
    if pmin == pmax:
        return {}

    step = (pmax - pmin) / num_levels
    levels = []

    for i in range(num_levels):
        ll = pmin + i * step
        lh = ll + step
        mid = (ll + lh) / 2
        vol = 0
        for c in candles:
            cl, ch = _cv(c, 'low'), _cv(c, 'high')
            if cl >= lh or ch <= ll:
                continue
            ol = max(cl, ll)
            oh = min(ch, lh)
            if ch > cl:
                vol += _cv(c, 'volume') * (oh - ol) / (ch - cl)
        levels.append({"price": mid, "volume": vol,
                       "low": ll, "high": lh})

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
            va_levels.append(levels[left])
            va_vol += levels[left]["volume"]
            left -= 1
        elif right < len(levels):
            va_levels.append(levels[right])
            va_vol += levels[right]["volume"]
            right += 1
        else:
            break

    return {
        "poc_price": poc["price"],
        "poc_volume": poc["volume"],
        "va_high": round(max(l["high"] for l in va_levels), 2),
        "va_low": round(min(l["low"] for l in va_levels), 2),
        "levels": sorted(levels, key=lambda x: x["price"]),
    }


class VolumeProfile:
    """
    Volume Profile: divides price range into buckets and counts volume at
    each price level. Produces: POC (highest volume node), VAH (Value Area
    High = 70th percentile), VAL (Value Area Low), and HVN/LVN nodes.
    """

    def __init__(self, candles, bucket_size=None, value_area_pct=0.70):
        self.candles = candles
        self.bucket_size = bucket_size
        self.value_area_pct = value_area_pct
        self.buckets = {}
        self.poc = None
        self.vah = None
        self.val = None
        self.hvn_nodes = []
        self.lvn_nodes = []

    def build(self):
        """Build the volume profile from candle data."""
        if not self.candles:
            return {}

        try:
            highs = [_cv(c, 'high') for c in self.candles]
            lows = [_cv(c, 'low') for c in self.candles]
            price_min = min(lows)
            price_max = max(highs)
        except (TypeError, KeyError, AttributeError):
            return {}

        # Auto bucket size: 0.1% of price range, minimum 0.0001
        if self.bucket_size is None:
            price_range = price_max - price_min
            self.bucket_size = max(0.0001, price_range / 100)

        # Fill buckets
        for candle in self.candles:
            try:
                vol = _cv(candle, 'volume')
                # Primary fill at close (50% weight)
                close_bucket = round(_cv(candle, 'close') / self.bucket_size) * self.bucket_size
                self.buckets[close_bucket] = self.buckets.get(close_bucket, 0) + vol * 0.5

                # Secondary fill at high and low (25% each)
                high_bucket = round(_cv(candle, 'high') / self.bucket_size) * self.bucket_size
                low_bucket = round(_cv(candle, 'low') / self.bucket_size) * self.bucket_size
                self.buckets[high_bucket] = self.buckets.get(high_bucket, 0) + vol * 0.25
                self.buckets[low_bucket] = self.buckets.get(low_bucket, 0) + vol * 0.25
            except (TypeError, KeyError, AttributeError, ZeroDivisionError):
                continue

        if not self.buckets:
            return {}

        # Sort by volume
        sorted_buckets = sorted(self.buckets.items(), key=lambda x: x[1], reverse=True)
        total_volume = sum(self.buckets.values())

        # POC = bucket with highest volume
        self.poc = sorted_buckets[0][0]

        # Value Area: top 70% of volume
        cum_volume = 0
        va_buckets = []
        for price, vol in sorted_buckets:
            cum_volume += vol
            va_buckets.append(price)
            if cum_volume >= total_volume * self.value_area_pct:
                break

        self.vah = max(va_buckets) if va_buckets else price_max
        self.val = min(va_buckets) if va_buckets else price_min

        # HVN/LVN: classify nodes
        avg_vol = total_volume / len(self.buckets)
        for price, vol in sorted_buckets:
            if vol > avg_vol * 1.5:
                self.hvn_nodes.append({'price': price, 'volume': vol})
            elif vol < avg_vol * 0.5:
                self.lvn_nodes.append({'price': price, 'volume': vol})

        return self.to_dict()

    def to_dict(self):
        return {
            'poc': round(self.poc, 5) if self.poc is not None else None,
            'vah': round(self.vah, 5) if self.vah is not None else None,
            'val': round(self.val, 5) if self.val is not None else None,
            'hvn': [{'price': round(n['price'], 5), 'volume': n['volume']} for n in self.hvn_nodes[:5]],
            'lvn': [{'price': round(n['price'], 5), 'volume': n['volume']} for n in self.lvn_nodes[:5]],
        }

    def is_in_value_area(self, price):
        """Check if a price is inside the value area (between VAL and VAH)."""
        if self.val is None or self.vah is None:
            return True
        return self.val <= price <= self.vah

    def distance_from_poc(self, price):
        """Distance from POC as percentage."""
        if self.poc is None:
            return 0
        return abs(price - self.poc) / self.poc * 100 if self.poc > 0 else 0
