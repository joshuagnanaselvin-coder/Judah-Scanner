"""Dimension 2 — LTF Entry Scanner (15M timeframe).

D2 receives its coin list EXCLUSIVELY from D1's HTF output.
It does NOT scan independently — only coins D1 flagged as SNIPER/OPPORTUNITY
are scanned on 15M for precise entry timing.

Architecture:
  D1 (HTF: 1H/4H/1D) → produces tier per coin
    ↓
  D2 (15M) → scans only D1's SNIPER/OPPORTUNITY coins for entry timing
    ↓
  D3 (Fusion) → D1 tier × D2 entry signal → bucket
"""
import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from backend.market_data import market_data
from backend.state_store import state_store
from backend.config import (
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE,
)

logger = logging.getLogger("judah.ltf")


class LTFSignal:
    """Persistent D2 signal — 15M entry timing for a D1-approved coin."""

    __slots__ = (
        "signal_id", "coin", "timeframe", "direction",
        "score", "tier", "entry", "sl", "tp1", "tp2",
        "rr1", "rr2", "freshness", "born_at", "last_scan",
        "score_history", "raw_signal", "d1_tier", "d1_score",
    )

    def __init__(self, coin: str, raw: dict, d1_tier: str = "", d1_score: float = 0):
        self.signal_id = raw.get("signal_id") or str(uuid.uuid4())[:12]
        self.coin = coin
        self.timeframe = "15M"
        self.direction = raw.get("direction", "BULLISH")
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
        self.d1_tier = d1_tier
        self.d1_score = d1_score

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
            "timeframe": self.timeframe,
            "direction": self.direction,
            "score": round(self.score, 1),
            "tier": self.tier,
            "entry": self.entry,
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "rr1": self.rr1,
            "rr2": self.rr2,
            "freshness": self.freshness,
            "born_at": self.born_at.isoformat(),
            "last_scan": self.last_scan.isoformat(),
            "score_history": list(self.score_history),
            "d1_tier": self.d1_tier,
            "d1_score": self.d1_score,
        }


def get_d1_approved_coins() -> list[str]:
    """Get coins that D1 has flagged as SNIPER or OPPORTUNITY.

    This is D2's ONLY source of coins — no independent scanning.
    """
    active = state_store.get_active_coins()
    approved = []
    for coin in active:
        d1 = state_store.get_d1_tier(coin)
        if d1 and d1.get("tier") in ("SNIPER", "OPPORTUNITY"):
            approved.append(coin)
    return approved


def scan_entry(coin: str, d1_tier: str = "", d1_score: float = 0) -> Optional[LTFSignal]:
    """Scan 15M for entry timing on a D1-approved coin.

    Runs the same 4-layer pipeline but ONLY on coins D1 already approved.
    D1's tier/direction is used as context — D2 score is independent.
    """
    from backend.engines.ltf_pipeline import scan_ltf_pipeline

    candles = market_data.get_candles(coin, "15M")
    if not candles or len(candles) < 25:
        logger.debug(f"[ltf] SKIP {coin}: insufficient 15M candles ({len(candles) if candles else 0})")
        return None

    # Run D2's own pipeline on 15M
    raw = scan_ltf_pipeline(coin, "15M")
    if not raw:
        return None

    score = float(raw.get("composite_score", 0))
    signal = LTFSignal(coin, raw, d1_tier=d1_tier, d1_score=d1_score)
    logger.info(f"[ltf] {coin}: score={score:.1f} tier={signal.tier} "
                f"dir={signal.direction} entry={signal.entry:.5f} "
                f"SL={signal.sl:.5f} TP1={signal.tp1:.5f} RR={signal.rr1:.1f}")
    return signal
