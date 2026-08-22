"""Pure math functions — no side effects, no API calls, fully testable."""

def _get(candle, key, default=0):
    """Get value from candle whether it's a dict or object."""
    if isinstance(candle, dict):
        return candle.get(key, default)
    return getattr(candle, key, default)

def body_ratio(candle, avg_body: float) -> float:
    if avg_body <= 0: return 0.0
    return abs(_get(candle, 'close') - _get(candle, 'open')) / avg_body

def avg_body_size(candles: list) -> float:
    if not candles: return 0.0
    return sum(abs(_get(c, 'close') - _get(c, 'open')) for c in candles) / len(candles)

def body_pct_of_range(candle) -> float:
    rng = _get(candle, 'high') - _get(candle, 'low')
    return (abs(_get(candle, 'close') - _get(candle, 'open')) / rng) if rng > 0 else 0.0

def atr(candles: list, period: int = 14) -> float:
    if not candles or len(candles) < period + 1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        c, prev = candles[i], _get(candles[i-1], 'close')
        trs.append(max(_get(c, 'high') - _get(c, 'low'),
                       abs(_get(c, 'high') - prev),
                       abs(_get(c, 'low') - prev)))
    recent = trs[-period:]
    return sum(recent) / len(recent)

def atr_percent(candles: list, period: int = 14) -> float:
    _atr = atr(candles, period)
    if not candles or _get(candles[-1], 'close') == 0: return 0.0
    return (_atr / _get(candles[-1], 'close')) * 100

def vw_atr(candles: list, period: int = 14) -> float:
    """Volume-weighted ATR — weights each bar's TR by its volume share.

    Standard ATR treats every bar equally. VWATR gives more weight to
    high-volume bars (institutional activity) and less to low-volume bars
    (noise). This produces tighter, more meaningful structural levels.

    Returns the VWATR value (same units as ATR).
    """
    if not candles or len(candles) < period + 1:
        return 0.0

    # Calculate TR for each bar
    trs = []
    volumes = []
    for i in range(1, len(candles)):
        c, prev = candles[i], _get(candles[i-1], 'close')
        tr = max(_get(c, 'high') - _get(c, 'low'),
                 abs(_get(c, 'high') - prev),
                 abs(_get(c, 'low') - prev))
        trs.append(tr)
        volumes.append(_get(c, 'volume'))

    # Use last `period` bars
    recent_tr = trs[-period:]
    recent_vol = volumes[-period:]
    total_vol = sum(recent_vol)

    if total_vol <= 0:
        # Fallback to standard ATR if no volume data
        return sum(recent_tr) / len(recent_tr)

    # Volume-weighted average TR
    vwatr = sum(tr * (vol / total_vol) for tr, vol in zip(recent_tr, recent_vol))
    return vwatr

def calc_envelope(candles: list, period: int = 20) -> dict:
    """Calculate the high/low envelope from the LAST `period` candles.

    Uses candles[-period:] (last N candles), NOT candles[:period].
    Rounds to 5 decimal places for precision.
    """
    subset = candles[-period:] if len(candles) >= period else candles

    highs = [_get(c, 'high') for c in subset]
    lows = [_get(c, 'low') for c in subset]

    range_high = max(highs)
    range_low = min(lows)
    range_mid = (range_high + range_low) / 2
    range_size = range_high - range_low

    return {
        'high': round(range_high, 5),
        'low': round(range_low, 5),
        'midpoint': round(range_mid, 5),
        'range_size': round(range_size, 5),
    }

def calc_adaptive_envelope(candles: list, atr_multiplier: float = 2.0,
                           lookback: int = 20) -> dict:
    """ATR-adaptive range: POC +/- (ATR * multiplier) as dynamic envelope."""
    from backend.helpers.volume_profile import VolumeProfile

    _atr = atr(candles, 14)
    if _atr <= 0:
        _atr = 0.001

    poc = _get(candles[-1], 'close') if candles else 0

    # Try to get POC from volume profile
    try:
        vp = VolumeProfile(candles[-lookback:])
        profile = vp.build()
        if profile.get('poc'):
            poc = profile['poc']
    except Exception:
        pass

    envelope_high = poc + (_atr * atr_multiplier)
    envelope_low = poc - (_atr * atr_multiplier)

    return {
        'high': round(envelope_high, 5),
        'low': round(envelope_low, 5),
        'midpoint': round((envelope_high + envelope_low) / 2, 5),
        'range_size': round(envelope_high - envelope_low, 5),
        'atr': round(_atr, 5),
        'atr_multiplier': atr_multiplier,
    }

def range_metrics(candles: list, lookback: int) -> dict:
    """Backward-compatible range metrics — delegates to calc_envelope."""
    env = calc_envelope(candles, lookback)
    last = _get(candles[-1], 'close') if candles else 0
    rng = env['high'] - env['low']
    return {
        "high": env['high'],
        "low": env['low'],
        "midpoint": env['midpoint'],
        "range_size": env['range_size'],
        "price_position": "ABOVE_MID" if last >= env['midpoint'] else "BELOW_MID",
        "price_percent": round(((last - env['low']) / rng) * 100, 1) if rng > 0 else 50.0,
    }

def retracement_pct(disp_high, disp_low, price, direction) -> float:
    size = abs(disp_high - disp_low)
    if size == 0: return 0.0
    if direction == "BULLISH":
        return ((disp_high - price) / size) * 100
    return ((price - disp_low) / size) * 100

def is_in_ote(pct: float) -> bool:
    """Optimal Trade Entry zone: 50-62% retracement (ICT definition)."""
    return 50 <= pct <= 62

def dist_from_entry_pct(current: float, entry: float) -> float:
    return abs(current - entry) / entry * 100 if entry > 0 else 999.0
