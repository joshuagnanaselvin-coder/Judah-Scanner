"""Dimension 2 — LTF Signal Scanner (15M timeframe).

Exact copy of the D1 pipeline, adapted for 15M:
  - Same 4-layer engine: scan() runs CRT → SMC → Flow → Momentum
  - Same scoring: CRT(25) + SMC(20) + Flow(25) + Momentum(20) = 90 max
  - Same tiers: SNIPER(≥70) / OPPORTUNITY(≥55) / WATCH(≥40) / REJECTED
  - Same freshness tracking and signal evolution

Difference from D1:
  - 15M timeframe only (not 1H/4H/1D)
  - Uses 15M candles from market_data
  - Wraps result in LTFSignal for D2-specific freshness/tracking
  - NO D1 boost, NO D1 context — completely independent score
"""
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from backend.engines.engine import scan
from backend.config import (
    TIER_SNIPER_SCORE,
    TIER_OPPORTUNITY_SCORE,
    TIER_WATCH_SCORE,
)

logger = logging.getLogger("judah.ltf_scanner")


class LTFSignal:
    """Persistent D2 signal — evolves across scans on 15M.

    Mirrors D1's signal dict structure but as a typed object
    with built-in freshness tracking.
    """

    __slots__ = (
        "signal_id", "coin", "timeframe", "direction",
        "score", "tier", "entry", "sl", "tp1", "tp2",
        "rr1", "rr2", "freshness", "born_at", "last_scan",
        "score_history", "raw_signal",
    )

    def __init__(self, coin: str, raw: dict):
        self.signal_id = raw.get("signal_id") or str(uuid.uuid4())[:12]
        self.coin = coin
        self.timeframe = "15M"
        self.direction = raw.get("direction", "BULLISH")
        self.score = float(raw.get("composite_score", 0))
        self.tier = _tier(self.score)
        self.entry = float(raw.get("entry", 0))
        self.sl = float(raw.get("stop_loss", 0))
        self.tp1 = float(raw.get("take_profit_1", 0))
        self.tp2 = float(raw.get("take_profit_2", 0))
        self.rr1 = float(raw.get("rr1", 0))
        self.rr2 = float(raw.get("rr2", 0))
        self.freshness = "HOT"
        self.born_at = datetime.now(timezone.utc)
        self.last_scan = datetime.now(timezone.utc)
        # Ring buffer: last 20 (timestamp, score) pairs
        self.score_history: deque = deque(maxlen=20)
        self.score_history.append((self.born_at.timestamp(), self.score))
        # Keep the full raw signal for D3 fusion and display
        self.raw_signal = raw

    def update(self, raw: dict):
        """Refresh with new scan results. Preserves born_at and signal_id."""
        self.score = float(raw.get("composite_score", 0))
        self.tier = _tier(self.score)
        self.direction = raw.get("direction", self.direction)
        self.entry = float(raw.get("entry", self.entry))
        self.sl = float(raw.get("stop_loss", self.sl))
        self.tp1 = float(raw.get("take_profit_1", self.tp1))
        self.tp2 = float(raw.get("take_profit_2", self.tp2))
        self.rr1 = float(raw.get("rr1", self.rr1))
        self.rr2 = float(raw.get("rr2", self.rr2))
        self.last_scan = datetime.now(timezone.utc)
        self.score_history.append((self.last_scan.timestamp(), self.score))
        self.raw_signal = raw
        self._update_freshness()

    def _update_freshness(self):
        """Recalculate freshness based on age and score stability."""
        age_min = (datetime.now(timezone.utc) - self.born_at).total_seconds() / 60

        # Score trajectory: improving, stable, or degrading
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
        """Signal expired if older than D2 TTL (15 minutes for 15M timeframe)."""
        age_min = (datetime.now(timezone.utc) - self.born_at).total_seconds() / 60
        return age_min > 15

    def to_dict(self) -> dict:
        """Serialize for state store / D3 fusion."""
        return {
            "signal_id": self.signal_id,
            "coin": self.coin,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "score": round(self.score, 1),
            "tier": self.tier,
            "entry": self.entry,
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "rr1": round(self.rr1, 2),
            "rr2": round(self.rr2, 2),
            "freshness": self.freshness,
            "born_at": self.born_at.isoformat(),
            "last_scan": self.last_scan.isoformat(),
            "score_history": list(self.score_history),
        }


def _tier(score: float) -> str:
    """D2 tier using the current sensitivity mode threshold.

    Reads directly from the config module so runtime mutations
    (via POST /api/d2-mode) take effect immediately.
    """
    import backend.config as cfg
    threshold = {
        "STRICT": cfg.D2_MIN_SCORE_STRICT,
        "BALANCED": cfg.D2_MIN_SCORE_BALANCED,
        "EXPLORATION": cfg.D2_MIN_SCORE_EXPLORATION,
        "DEBUG": cfg.D2_MIN_SCORE_DEBUG,
    }.get(cfg.D2_SENSITIVITY_MODE, cfg.D2_MIN_SCORE_STRICT)
    if score >= threshold:
        return "SNIPER"
    if score >= max(cfg.TIER_WATCH_SCORE, threshold - 15):
        return "OPPORTUNITY"
    if score >= cfg.TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


def scan_ltf(coin: str) -> Optional[LTFSignal]:
    """Run D2 scan for a coin on 15M timeframe.

    Runs the same 4-layer pipeline as D1 (engine.scan).
    Score is PURE LTF score — no D1 boost, no D1 context.
    Returns LTFSignal if scan succeeds, None otherwise.

    The engine will produce all tiers (SNIPER/OPPORTUNITY/WATCH/REJECTED).
    D3 decides later whether to filter or bucket the signal.
    """
    from backend.market_data import market_data

    candles = market_data.get_candles(coin, "15M")
    if not candles or len(candles) < 50:
        logger.debug(f"[ltf] SKIP {coin}: no 15M candles")
        return None

    # Run the same 4-layer pipeline on 15M candles
    raw = scan(coin, "15M")
    if not raw:
        logger.debug(f"[ltf] SKIP {coin}: engine returned None")
        return None

    score = float(raw.get("composite_score", 0))
    tier = _tier(score)

    # Return the signal — D3 will filter/bucket later
    signal = LTFSignal(coin, raw)
    logger.info(f"[ltf] {coin}: score={score:.1f} tier={tier} "
                f"dir={signal.direction} entry={signal.entry:.5f} "
                f"SL={signal.sl:.5f} TP1={signal.tp1:.5f} RR={signal.rr1:.1f}")
    return signal
