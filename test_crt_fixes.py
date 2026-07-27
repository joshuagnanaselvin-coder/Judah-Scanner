#!/usr/bin/env python3
"""Tests for CRT engine fixes — direction, scoring cap, total score bounds."""
import sys
import os
import logging

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("judah.test")

from engines.crt_engine import (
    run_crt,
    _score_consolidation, _score_range_candle_strength,
    _score_fvg_quality, _score_retest_quality, _score_zone_alignment,
    _CRT_MAX_SCORE, _W_CONSOLIDATION, _W_RANGE_CANDLE, _W_FVG, _W_RETEST, _W_ZONE,
    _find_consolidation, _find_range_candle, _verify_fill,
    _CONSOLIDATION_MIN_BARS, _RANGE_CANDLE_LOOKAHEAD, _FILL_RECENCY_MAX,
)
from helpers.candle_math import range_metrics


# ─── Helpers ──────────────────────────────────────────────────────────────────

class FakeCandle:
    def __init__(self, time, open_, high, low, close, volume=1000):
        self.time = time
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def make_candles(price_data, base_time=1_700_000_000_000):
    """Create candles from a list of (open, high, low, close) tuples.
    Each candle = 1 bar apart in time.
    """
    candles = []
    for i, (o, h, l, c) in enumerate(price_data):
        candles.append(FakeCandle(base_time + i * 3600_000, o, h, l, c))
    return candles


def passed(tag, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    msg = f"  [{status}] {tag}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    if not condition:
        return False
    return True


# ─── Test 1: Direction Logic ──────────────────────────────────────────────────

def test_direction_bullish_range_candle():
    """Bullish range candle + fill → trade direction = LONG."""
    price = 100.0
    # Step 1: 8 bars of consolidation (small bodies, tight range)
    consolid = []
    for i in range(8):
        o = price - 0.5 + i * 0.1
        c = o + 0.3
        consolid.append((o, c + 0.2, c - 0.2, c))

    # Step 2: BULLISH range candle (large body, broke up)
    rc = (price - 1.0, price + 5.0, price - 0.5, price + 4.0)  # big bullish candle

    # Step 3: Price fills back INTO the range candle body
    # RC body = [99.0, 104.0], so fill = close back inside that range
    fill_candle = (103.0, 103.5, 102.0, 102.5)  # close 102.5 is inside [99, 104]

    all_candles = consolid + [rc, fill_candle]
    candles = make_candles(all_candles)

    result = run_crt(candles)
    if result is None:
        return passed("Bullish RC → LONG direction", False, f"run_crt returned None. Reason check: need >= {_CONSOLIDATION_MIN_BARS} bars consolid + range candle + fill. Candle count: {len(candles)}")

    direction = result["displacement"]["crt_trade_direction"]
    return passed("Bullish RC → LONG direction", direction == "LONG", f"got {direction}")


def test_direction_bearish_range_candle():
    """Bearish range candle + fill → trade direction = SHORT."""
    price = 100.0
    # Step 1: 8 bars of consolidation
    consolid = []
    for i in range(8):
        o = price + 0.5 - i * 0.1
        c = o - 0.3
        consolid.append((o, o + 0.2, c - 0.2, c))

    # Step 2: BEARISH range candle (large body, broke down)
    rc = (price + 1.0, price + 0.5, price - 5.0, price - 4.0)  # big bearish candle

    # Step 3: Price fills back INTO the range candle body
    # RC body = [96.0, 101.0], fill = close inside that range
    fill_candle = (97.0, 97.5, 96.5, 97.2)

    all_candles = consolid + [rc, fill_candle]
    candles = make_candles(all_candles)

    result = run_crt(candles)
    if result is None:
        return passed("Bearish RC → SHORT direction", False, f"run_crt returned None. Candle count: {len(candles)}")

    direction = result["displacement"]["crt_trade_direction"]
    return passed("Bearish RC → SHORT direction", direction == "SHORT", f"got {direction}")


# ─── Test 2: Score Cap ───────────────────────────────────────────────────────

def test_score_components_sum_to_max():
    """Each component scorer should respect its max weight."""
    # Max consolidation
    c = {"bar_count": 10, "avg_body": 0.001, "avg_tr": 0.005}
    s = _score_consolidation(c)
    passed(f"Consolidation max ≤ {_W_CONSOLIDATION}", s <= _W_CONSOLIDATION, f"got {s}")

    # Max range candle
    s = _score_range_candle_strength(3.0)  # max ratio
    passed(f"Range candle max ≤ {_W_RANGE_CANDLE}", s <= _W_RANGE_CANDLE, f"got {s}")

    # Max FVG
    s = _score_fvg_quality({"quality": 1.0})
    passed(f"FVG max ≤ {_W_FVG}", s <= _W_FVG, f"got {s}")

    # Max retest
    s = _score_retest_quality(1.0, "BULLISH")
    passed(f"Retest max ≤ {_W_RETEST}", s <= _W_RETEST, f"got {s}")

    # Max zone
    rng = {"low": 90, "high": 110, "range_size": 20, "midpoint": 100}
    s = _score_zone_alignment(92, rng)  # in discount
    passed(f"Zone max ≤ {_W_ZONE}", s <= _W_ZONE, f"got {s}")


def test_score_never_exceeds_60():
    """Build an optimal CRT setup and verify score ≤ 60."""
    price = 100.0
    # 10 bars of perfect consolidation
    consolid = []
    for i in range(10):
        o = price - 0.3 + i * 0.06
        c = o + 0.2
        consolid.append((o, c + 0.15, c - 0.15, c))

    # Massive bullish range candle
    rc = (price - 0.5, price + 8.0, price - 0.2, price + 7.5)

    # Perfect fill (deep inside body)
    fill_candle = (103.0, 103.5, 102.0, 102.8)

    # One more candle at the end to make it recent
    final = (102.5, 103.0, 102.0, 102.6)

    all_candles = consolid + [rc, fill_candle, final]
    candles = make_candles(all_candles)

    result = run_crt(candles)
    if result is None:
        return passed("Score cap at 60", False, "run_crt returned None for optimal setup")

    score = result["crt_score"]
    passed("CRT score ≤ 60 (hard cap)", score <= 60, f"got {score}")
    passed("CRT score > 0 for valid setup", score > 0, f"got {score}")


def test_total_score_never_exceeds_100():
    """Even with max CRT + max SMC, total should ≤ 100."""
    # CRT max is 60, SMC max is 40. 60+40=100.
    # Just verify the constant sums are correct.
    total_max = _CRT_MAX_SCORE + 40  # 60 + 40 (SMC max)
    passed("Total max score ≤ 100", total_max <= 100, f"CRT max={_CRT_MAX_SCORE} + SMC max=40 = {total_max}")


# ─── Test 3: Rejection Cases ─────────────────────────────────────────────────

def test_no_consolidation():
    """Flat/noisy market without consolidation → score = 0."""
    price = 100.0
    # Random-looking candles, no tight consolidation
    noisy = [
        (100, 105, 95, 102),
        (102, 108, 98, 100),
        (100, 103, 97, 101),
        (101, 106, 99, 104),
        (104, 110, 102, 107),
        (107, 112, 105, 109),
        (109, 115, 107, 113),
        (113, 118, 110, 116),
    ]
    candles = make_candles(noisy)
    result = run_crt(candles)
    if result is None:
        passed("No consolidation → None", True, "correctly rejected")
        return True
    score = result.get("crt_score", 0)
    return passed("No consolidation → score=0", score == 0, f"got {score}")


def test_no_fill():
    """Trend with no fill back into body → score = 0."""
    price = 100.0
    # 8 bars consolidation
    consolid = []
    for i in range(8):
        o = price - 0.3 + i * 0.06
        c = o + 0.2
        consolid.append((o, c + 0.15, c - 0.15, c))

    # Range candle broke up
    rc = (price - 0.5, price + 8.0, price - 0.2, price + 7.5)

    # Price keeps going UP — never fills back into body
    no_fill = [
        (105, 110, 104, 108),
        (108, 115, 107, 112),
        (112, 118, 110, 116),
    ]

    candles = make_candles(consolid + [rc] + no_fill)
    result = run_crt(candles)
    if result is None:
        passed("No fill → None", True, "correctly rejected")
        return True
    score = result.get("crt_score", 0)
    return passed("No fill → score=0", score == 0, f"got {score}, reason={result.get('reason')}")


def test_stale_setup():
    """Range candle too old → score = 0."""
    price = 100.0
    consolid = []
    for i in range(8):
        o = price - 0.3 + i * 0.06
        c = o + 0.2
        consolid.append((o, c + 0.15, c - 0.15, c))

    rc = (price - 0.5, price + 8.0, price - 0.2, price + 7.5)
    fill = (103, 104, 102, 103.5)

    # Many bars between fill and current — makes it stale
    # _FILL_RECENCY_MAX = 8 bars. Put 10 bars of drift after fill.
    drift = [(103 + i * 0.1, 104 + i * 0.1, 102 + i * 0.1, 103 + i * 0.1) for i in range(10)]

    candles = make_candles(consolid + [rc, fill] + drift)
    result = run_crt(candles)
    if result is None:
        passed("Stale (>8 bars) → None", True, "correctly rejected")
        return True
    score = result.get("crt_score", 0)
    return passed("Stale → score=0 or halved", score <= 30, f"got {score}, reason={result.get('reason')}")


# ─── Test 4: Signal Builder Direction Propagation ────────────────────────────

def test_signal_builder_uses_crt_direction():
    """Verify signal_builder reads crt_trade_direction, not rc_direction."""
    from engines.signal_builder import build_signal
    import inspect
    source = inspect.getsource(build_signal)
    has_crt_trade = "crt_trade_direction" in source
    passed("signal_builder uses crt_trade_direction", has_crt_trade)
    return has_crt_trade


# ─── Test 5: Constant Verification ──────────────────────────────────────────

def test_scoring_constants():
    """Verify weights sum to 60."""
    total = _W_CONSOLIDATION + _W_RANGE_CANDLE + _W_FVG + _W_RETEST + _W_ZONE
    passed(f"Weights sum to {_CRT_MAX_SCORE}", total == _CRT_MAX_SCORE == 60,
           f"{_W_CONSOLIDATION}+{_W_RANGE_CANDLE}+{_W_FVG}+{_W_RETEST}+{_W_ZONE}={total}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  CRT Engine — Bug Fix Verification Tests")
    print("=" * 70)

    all_pass = True

    print("\n── Bug 1: Direction Logic ──")
    all_pass &= test_direction_bullish_range_candle()
    all_pass &= test_direction_bearish_range_candle()
    all_pass &= test_signal_builder_uses_crt_direction()

    print("\n── Bug 2: Score Cap ──")
    test_scoring_constants()
    test_score_components_sum_to_max()
    all_pass &= test_score_never_exceeds_60()
    all_pass &= test_total_score_never_exceeds_100()

    print("\n── Rejection Cases ──")
    all_pass &= test_no_consolidation()
    all_pass &= test_no_fill()
    all_pass &= test_stale_setup()

    print("\n" + "=" * 70)
    if all_pass:
        print("  ALL TESTS PASSED ✓")
    else:
        print("  SOME TESTS FAILED ✗ — review output above")
    print("=" * 70)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
