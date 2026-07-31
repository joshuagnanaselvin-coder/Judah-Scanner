"""Dimension 2 — LTF Entry Scanner.

Runs the same 4-layer pipeline (CRT → SMC → Flow → Momentum) on 15M candles.
Outputs persistent LTF signal objects, SNIPER-only (score ≥ 70).

Reuses the existing engine.py pipeline — no duplication.
"""
import uuid
import logging
from datetime import datetime, timezone
from collections import deque
from typing import Any, Optional
from backend.config import (
    D2_SIGNAL_TTL_MINUTES,
    D2_MIN_SCORE,
    TIER_SNIPER_SCORE,
    TIER_OPPORTUNITY_SCORE,
    TIER_WATCH_SCORE,
    SCAN_INTERVAL_SECONDS,
)
from backend.engines.engine import scan
from backend.market_data import market_data
from backend.state_store import state_store

logger = logging.getLogger("judah.ltf")


# D1 context boost applied to raw D2 score before tier classification
D1_TIER_BOOST = {
    "SNIPER": 10,
    "OPPORTUNITY": 5,
    "WATCH": 0,
    "REJECTED": -10,
    "": 0,           # No D1 data yet — neutral
}
MAX_SCORE = 100.0


class LTFSignal:
    """Persistent signal object that evolves across scans."""

    __slots__ = (
        "signal_id", "coin", "timeframe", "direction",
        "score", "tier", "entry", "sl", "tp1", "tp2",
        "rr1", "rr2", "freshness", "born_at", "last_scan",
        "score_history", "d1_tier", "bucket",
    )

    def __init__(self, coin: str, raw: dict):
        self.signal_id = raw.get("signal_id") or str(uuid.uuid4())[:12]
        self.coin = coin
        self.timeframe = "15M"
        self.direction = raw.get("direction", "BULLISH")
        self.score = float(raw.get("composite_score", 0))
        self.tier = _tier(self.score)
        # Canonical field names from signal_builder
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
        # D1 context (set by fusion engine)
        self.d1_tier: str = ""
        self.bucket: str = ""

    def update(self, raw: dict):
        """Update signal with fresh scan results. Preserves signal_id and born_at."""
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
        self._update_freshness()

    def _update_freshness(self):
        """Recalculate freshness based on age and score stability."""
        age_sec = (datetime.now(timezone.utc) - self.born_at).total_seconds()
        age_min = age_sec / 60

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
        age_min = (datetime.now(timezone.utc) - self.born_at).total_seconds() / 60
        return age_min > D2_SIGNAL_TTL_MINUTES

    def to_dict(self) -> dict:
        """Serialize for frontend/state store."""
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
            "d1_tier": self.d1_tier,
            "bucket": self.bucket,
        }


def _tier(score: float) -> str:
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


def scan_ltf(coin: str) -> Optional[LTFSignal]:
    """Run D2 scan for a coin on 15M.

    Uses the same 4-layer pipeline as D1 (engine.scan).
    Returns LTFSignal only if score ≥ D2_MIN_SCORE (SNIPER-only).
    Returns None if below threshold or scan fails.
    """
    candles = market_data.get_candles(coin, "15M")
    if not candles or len(candles) < 50:
        logger.debug(f"[ltf] SKIP {coin}: no 15M candles")
        return None

    # Run the shared 4-layer pipeline
    raw = scan(coin, "15M")
    if not raw:
        logger.debug(f"[ltf] SKIP {coin}: engine returned None")
        return None

    # D1 context boost: read D1 tier and adjust score before gate check
    d1_tier_str = state_store.get_d1_tier_str(coin)
    boost = D1_TIER_BOOST.get(d1_tier_str, 0)
    raw_score = float(raw.get("composite_score", 0))
    boosted_score = min(MAX_SCORE, raw_score + boost)
    raw["composite_score"] = boosted_score

    # Context-aware logging
    if boost > 0:
        logger.debug(f"[ltf] {coin}: raw={raw_score:.1f} D1={d1_tier_str} +{boost} → {boosted_score:.1f}")

    if boosted_score < D2_MIN_SCORE:
        logger.debug(f"[ltf] SKIP {coin}: boosted score {boosted_score:.1f} < {D2_MIN_SCORE}")
        return None

    # Check if existing signal exists for this coin — update it
    existing = state_store.get_d2_signal(coin)
    if existing and isinstance(existing, LTFSignal):
        existing.update(raw)
        logger.info(f"[ltf] UPDATE {coin}: score={boosted_score:.1f} tier={existing.tier} "
                     f"dir={existing.direction} freshness={existing.freshness}")
        return existing

    # New signal
    signal = LTFSignal(coin, raw)
    logger.info(f"[ltf] NEW {coin}: score={boosted_score:.1f} tier={signal.tier} "
                f"dir={signal.direction} entry={signal.entry} "
                f"SL={signal.sl} TP1={signal.tp1} RR={signal.rr1:.1f}")
    return signal
