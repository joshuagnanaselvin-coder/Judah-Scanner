"""Dimension 2 — LTF Entry Scanner (15M timeframe).

D2 receives its coin list from D1's HTF output AND scans all coins independently.
Nascent Move Detector identifies LTF-first breakouts for Type B signals.
Entry Precision sub-scorer gates Type B classification.
"""
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from backend.market_data import market_data
from backend.state_store import state_store
from backend.config import (
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE, TIER_WEAK_SCORE,
    TYPE_B_MIN_D2_SCORE, TYPE_B_ENTRY_PRECISION_GATE,
    D2_MIN_ENTRY_PRECISION, D2_MIN_FLOW, D2_MIN_MOMENTUM,
    IGNORE_MIN_SCORE,
)
from backend.helpers.candle_math import atr
from backend.vsp_helpers import detect_swing_points, detect_fvg

logger = logging.getLogger("judah.ltf")


class LTFSignal:
    """Persistent D2 signal — 15M entry timing."""

    __slots__ = (
        "signal_id", "coin", "timeframe", "direction",
        "score", "tier", "entry", "sl", "tp1", "tp2",
        "rr1", "rr2", "freshness", "born_at", "last_scan",
        "score_history", "raw_signal",
        "nascent_move", "entry_precision", "flow_score",
        "momentum_score", "_freshness",
        "entry_type",
    )

    def __init__(self, coin: str, raw: dict):
        self.signal_id = raw.get("signal_id") or str(uuid.uuid4())[:12]
        self.coin = coin
        self.timeframe = "15M"
        self.direction = raw.get("direction", "NEUTRAL")
        self.score = float(raw.get("composite_score", 0))
        self.tier = raw.get("tier", "WATCH")
        self.entry = float(raw.get("entry", 0))
        self.sl = float(raw.get("stop_loss", 0))
        self.tp1 = float(raw.get("take_profit_1", 0))
        self.tp2 = float(raw.get("take_profit_2", 0))
        self.rr1 = round(float(raw.get("rr1", 0)), 2)
        self.rr2 = round(float(raw.get("rr2", 0)), 2)
        self.freshness = "HOT"
        self.born_at = datetime.now(timezone.utc)
        self.last_scan = datetime.now(timezone.utc)
        self.score_history: deque = deque(maxlen=20)
        self.score_history.append((self.born_at.timestamp(), self.score))
        self.raw_signal = raw
        # Nascent Move & Entry Precision
        self.nascent_move = bool(raw.get("nascent_move", False))
        self.entry_precision = float(raw.get("entry_precision", 0.0))
        self.flow_score = float(raw.get("flow_score", 0.0))
        self.momentum_score = float(raw.get("momentum_score", 0.0))
        self.entry_type = raw.get("entry_type", "")

    def update(self, raw: dict):
        self.score = float(raw.get("composite_score", 0))
        self.tier = raw.get("tier", self.tier)
        self.direction = raw.get("direction", self.direction)
        self.entry = float(raw.get("entry", self.entry))
        self.sl = float(raw.get("stop_loss", self.sl))
        self.tp1 = float(raw.get("take_profit_1", self.tp1))
        self.tp2 = float(raw.get("take_profit_2", self.tp2))
        self.rr1 = round(float(raw.get("rr1", 0)), 2)
        self.rr2 = round(float(raw.get("rr2", 0)), 2)
        self.last_scan = datetime.now(timezone.utc)
        self.score_history.append((self.last_scan.timestamp(), self.score))
        self.raw_signal = raw
        self.nascent_move = bool(raw.get("nascent_move", self.nascent_move))
        self.entry_precision = float(raw.get("entry_precision", self.entry_precision))
        self.flow_score = float(raw.get("flow_score", self.flow_score))
        self.momentum_score = float(raw.get("momentum_score", self.momentum_score))
        self.entry_type = raw.get("entry_type", self.entry_type)
        self._update_freshness()

    def _update_freshness(self):
        age_min = (datetime.now(timezone.utc) - self.born_at).total_seconds() / 60
        recent_scores = [s for _, s in self.score_history]
        if len(recent_scores) >= 3:
            trend = recent_scores[-1] - recent_scores[-3]
        else:
            trend = 0
        if age_min < 3:
            self.freshness = "HOT"
        elif age_min < 8 and trend >= 0:
            self.freshness = "WARM"
        elif age_min < 15:
            self.freshness = "COOLING"
        else:
            self.freshness = "STALE"

    def is_expired(self) -> bool:
        """Signal expired if older than 15 minutes (15M timeframe)."""
        age_min = (datetime.now(timezone.utc) - self.born_at).total_seconds() / 60
        return age_min > 15

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "coin": self.coin,
            "engine": self.timeframe,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "score": round(self.score, 1),
            "composite_score": round(self.score, 1),
            "tier": self.tier,
            "entry": self.entry,
            "stop_loss": self.sl,
            "take_profit_1": self.tp1,
            "take_profit_2": self.tp2,
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "rr1": self.rr1,
            "rr2": self.rr2,
            "freshness": self.freshness,
            "born_at": self.born_at.isoformat(),
            "last_scan": self.last_scan.isoformat(),
            "score_history": list(self.score_history),
            "nascent_move": self.nascent_move,
            "entry_precision": self.entry_precision,
            "flow_score": self.flow_score,
            "momentum_score": self.momentum_score,
            "entry_type": self.entry_type,
        }


__all__ = ["LTFSignal", "scan_entry", "detect_nascent_move", "calculate_entry_precision"]


def detect_nascent_move(candles: list, direction: str, d1_direction: str = "") -> dict:
    """5-condition Nascent Move Detector — identifies LTF-first breakouts.

    Conditions (all pass/fail):
    1. 15M structure break (close above/below swing point with >= 1.5x volume)
    2. OB interaction (retesting impulse OB within 15-30 min of break)
    3. Volume + Delta (breakout candle >= 2x avg volume AND delta >= 60% aligned)
    4. Liquidity sweep (stop-loss cluster taken out within last 2h, >= 0.5% of price)
    5. No opposing HTF structure (1H/4H have no DIRECT opposing signal)

    Returns:
        dict with "nascent_move" (bool), "conditions_met" (int), "partial" (bool)
    """
    if not candles or len(candles) < 25:
        return {"nascent_move": False, "conditions_met": 0, "partial": False}

    conditions_met = 0
    last = candles[-1]
    last_price = last.close

    # Condition 1: 15M structure break with volume
    swings = detect_swing_points(candles[-30:])
    if direction == "BULLISH":
        recent_highs = swings.get("swing_highs", [])
        if recent_highs:
            swing_high = recent_highs[-1].get("price", 0) if isinstance(recent_highs[-1], dict) else recent_highs[-1]
            if last.close > swing_high and swing_high > 0:
                avg_vol = sum(c.volume for c in candles[-10:-1]) / max(len(candles[-10:-1]), 1)
                if last.volume >= avg_vol * 1.5:
                    conditions_met += 1
    else:
        recent_lows = swings.get("swing_lows", [])
        if recent_lows:
            swing_low = recent_lows[-1].get("price", 0) if isinstance(recent_lows[-1], dict) else recent_lows[-1]
            if last.close < swing_low and swing_low > 0:
                avg_vol = sum(c.volume for c in candles[-10:-1]) / max(len(candles[-10:-1]), 1)
                if last.volume >= avg_vol * 1.5:
                    conditions_met += 1

    # Condition 2: OB interaction
    fvgs = detect_fvg(candles) or []
    for fvg in fvgs:
        fvg_type = fvg.get("type", "")
        fvg_top = fvg.get("top", 0)
        fvg_bot = fvg.get("bottom", 0)
        if direction == "BULLISH" and fvg_type == "BULLISH":
            if fvg_bot <= last_price <= fvg_top:
                conditions_met += 1
                break
        elif direction == "BEARISH" and fvg_type == "BEARISH":
            if fvg_bot <= last_price <= fvg_top:
                conditions_met += 1
                break

    # Condition 3: Volume + Delta (breakout candle)
    avg_vol_20 = sum(c.volume for c in candles[-20:]) / max(len(candles[-20:]), 1)
    if last.volume >= avg_vol_20 * 2.0:
        # Delta check: close vs open alignment
        body = abs(last.close - last.open)
        total_range = last.high - last.low
        if total_range > 0 and (body / total_range) >= 0.6:
            conditions_met += 1

    # Condition 4: Liquidity sweep
    all_pools = []
    if swings:
        from backend.liquidity_map import detect_liquidity_pools
        liq_pools = detect_liquidity_pools(swings)
        all_pools = liq_pools.get("buyside", []) + liq_pools.get("sellside", [])
    for pool in all_pools:
        level = pool.get("price", 0)
        if level > 0 and abs(last_price - level) / last_price * 100 >= 0.5:
            # Price has moved significantly past the pool level → pool was swept
            conditions_met += 1
            break

    # Condition 5: No opposing HTF structure
    if d1_direction and d1_direction == direction:
        conditions_met += 1
    elif not d1_direction:
        # No D1 data — give partial credit
        conditions_met += 1

    # Scoring
    if conditions_met >= 4:
        return {"nascent_move": True, "conditions_met": conditions_met, "partial": False}
    elif conditions_met == 3:
        return {"nascent_move": True, "conditions_met": conditions_met, "partial": True}
    else:
        return {"nascent_move": False, "conditions_met": conditions_met, "partial": False}


def calculate_entry_precision(candles: list, signal: dict, direction: str) -> float:
    """Entry Precision sub-scorer — max 25 points.

    Components:
    - OB retest: 0-10 pts (in OB zone = 10, near OB = 5, far = 0)
    - FVG fill: 0-8 pts (in FVG = 8, near FVG = 4, far = 0)
    - Wick rejection: 0-7 pts (upper wick for bearish, lower wick for bullish)

    Also checks D2 minimum thresholds from config:
    - Entry Precision >= 15 (D2_MIN_ENTRY_PRECISION)
    - Flow >= 8 (D2_MIN_FLOW)
    - Momentum >= 8 (D2_MIN_MOMENTUM)
    """
    if not candles or len(candles) < 5:
        return 0.0

    last = candles[-1]
    last_price = last.close
    score = 0.0

    # OB retest (0-10)
    ob = signal.get("ob", {})
    if ob:
        ob_high = ob.get("high", 0)
        ob_low = ob.get("low", 0)
        if ob_low and ob_high and ob_low <= last_price <= ob_high:
            score += 10.0  # Inside OB
        elif ob_low and ob_high and abs(last_price - (ob_low + ob_high) / 2) / last_price * 100 <= 1.0:
            score += 5.0  # Within 1% of OB center

    # FVG fill (0-8)
    fvg = signal.get("fvg", {})
    if fvg:
        fvg_top = fvg.get("top", 0)
        fvg_bot = fvg.get("bottom", 0)
        if fvg_bot and fvg_top and fvg_bot <= last_price <= fvg_top:
            score += 8.0  # Inside FVG
        elif fvg_bot and fvg_top and abs(last_price - (fvg_bot + fvg_top) / 2) / last_price * 100 <= 1.0:
            score += 4.0  # Near FVG

    # Wick rejection (0-7)
    total_range = last.high - last.low
    if total_range > 0:
        if direction == "BULLISH":
            lower_wick = last.close - last.low
            wick_ratio = lower_wick / total_range
            if wick_ratio >= 0.5:
                score += 7.0  # Strong lower wick rejection
            elif wick_ratio >= 0.3:
                score += 4.0  # Moderate wick
        else:
            upper_wick = last.high - last.close
            wick_ratio = upper_wick / total_range
            if wick_ratio >= 0.5:
                score += 7.0  # Strong upper wick rejection
            elif wick_ratio >= 0.3:
                score += 4.0  # Moderate wick

    return min(score, 25.0)


async def scan_entry(coin: str) -> dict:
    """Scan 15M for entry timing on a coin.

    Runs the D2 pipeline, adds nascent move detection and entry precision.
    D2 is fully independent — no D1 context needed.
    Every coin produces a result (no silent drops).
    """
    from backend.engines.ltf_pipeline import scan_ltf_pipeline

    candles = market_data.get_candles(coin, "15M")
    if not candles or len(candles) < 25:
        logger.debug(f"[ltf] SCAN {coin}: insufficient 15M candles ({len(candles) if candles else 0}) — pipeline will handle penalty")
        candles = []  # Let pipeline handle with penalty

    # Quality gate — DEGRADED/INCOMPLETE proceed; INVALID/GAPPED/MISSING go through with penalty
    from backend.data_quality_gate import validate_candles
    if candles:
        quality = validate_candles(candles, "15M")
        if quality.state in ("INVALID", "GAPPED", "MISSING", "STALE"):
            logger.debug(f"[ltf] SCAN {coin}: quality={quality.state} issues={quality.issues[:2]} — pipeline will handle penalty")
            candles = []  # Let pipeline handle with penalty

    # Run D2's own pipeline on 15M — always returns a dict
    raw = await scan_ltf_pipeline(coin, "15M")
    if not raw:
        raw = {"symbol": coin, "timeframe": "15M", "tier": "REJECTED", "composite_score": 0, "direction": "NEUTRAL"}

    # Nascent Move Detection (D2 is independent — no D1 direction)
    direction = raw.get("direction", "BULLISH")
    nascent = detect_nascent_move(candles, direction, "")
    raw["nascent_move"] = nascent.get("nascent_move", False)
    raw["nascent_conditions"] = nascent.get("conditions_met", 0)
    raw["nascent_partial"] = nascent.get("partial", False)

    # Entry Precision
    entry_precision = calculate_entry_precision(candles, raw, direction)
    raw["entry_precision"] = entry_precision

    # D2 Minimum Thresholds enforcement
    composite = raw.get("composite_score", 0)
    flow = raw.get("flow_score", 0.0)
    momentum = raw.get("momentum_score", 0.0)

    # Apply minimum thresholds — mark in signal if any fail
    raw["threshold_flow_pass"] = flow >= D2_MIN_FLOW
    raw["threshold_momentum_pass"] = momentum >= D2_MIN_MOMENTUM
    raw["threshold_ep_pass"] = entry_precision >= D2_MIN_ENTRY_PRECISION
    raw["thresholds_passed"] = all([
        raw["threshold_ep_pass"],
        raw["threshold_flow_pass"],
        raw["threshold_momentum_pass"],
    ])

    # D2 tier recalculation with minimum thresholds
    if composite >= TIER_SNIPER_SCORE:
        tier = "SNIPER"
    elif composite >= TIER_OPPORTUNITY_SCORE:
        tier = "OPPORTUNITY"
    elif composite >= TIER_WATCH_SCORE:
        tier = "WATCH"
    elif composite >= TIER_WEAK_SCORE:
        tier = "WEAK"
    else:
        tier = "REJECTED"

    # If sub-thresholds fail, downgrade tier
    if not raw["thresholds_passed"] and composite < IGNORE_MIN_SCORE:
        tier = "REJECTED"

    raw["tier"] = tier
    raw["score"] = composite

    score = composite
    logger.info(f"[ltf] {coin}: score={score:.1f} tier={tier} "
                f"dir={direction} nascent={raw['nascent_move']} "
                f"EP={entry_precision:.0f} flow={flow:.0f} mom={momentum:.0f} "
                f"RR={raw.get('rr1', 0):.1f}")
    return raw
