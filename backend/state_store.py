"""Shared State Store — loose coupling between all dimensions.

D1, D2, and D3 communicate exclusively through this store.
No function calls between dimensions. No circular dependencies.

Thread-safe for async access via asyncio.Lock.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("judah.state")


class StateStore:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init = False
        return cls._instance

    def __init__(self):
        if self._init:
            return
        self._init = True
        self._lock = asyncio.Lock()

        # D1: best tier per coin across all HTF timeframes
        # key="KAVAUSDT" → {"tier": "SNIPER", "score": 72, "timeframes": {...}, "updated_at": ts}
        self.d1_tiers: dict[str, dict] = {}

        # D2: persistent LTF signal objects (SNIPER-only)
        # key="KAVAUSDT" → LTFSignal dataclass
        self.d2_signals: dict[str, Any] = {}

        # D3: Decision Layer — position sizing decisions with decay tracking
        # key="KAVAUSDT" → {"signal_type": "A", "position_multiplier": 0.75, "decayed_score": 78, ...}
        self.d3_decisions: dict[str, dict] = {}

        # Active positions (open trades)
        self.positions: dict[str, dict] = {}

        # Current regime per coin (updated hourly by regime engine)
        self.regimes: dict[str, dict] = {}

        # Global timestamps
        self.last_d1_scan: float = 0.0
        self.last_d2_scan: float = 0.0
        self.last_d3_fusion: float = 0.0
        self.last_regime_update: float = 0.0

    async def set_timestamp(self, field: str, ts: float = None):
        """Thread-safe timestamp setter for last_d1_scan / last_d2_scan / last_d3_fusion."""
        if ts is None:
            ts = datetime.now(timezone.utc).timestamp()
        async with self._lock:
            if hasattr(self, field):
                setattr(self, field, ts)

    # ── D1 Methods ──────────────────────────────────────────────────

    async def set_d1_tier(self, coin: str, tier: str, score: float, timeframes: dict, direction: str = ""):
        """Update D1 tier for a coin. Called by D1 scanner."""
        async with self._lock:
            self.d1_tiers[coin] = {
                "tier": tier,
                "score": score,
                "direction": direction,
                "timeframes": timeframes,
                "updated_at": datetime.now(timezone.utc).timestamp(),
            }
            self.last_d1_scan = datetime.now(timezone.utc).timestamp()

    def get_d1_tier(self, coin: str) -> dict | None:
        """Get D1 tier for a coin. Called by D2 and D3."""
        return self.d1_tiers.get(coin)

    def get_d1_tier_str(self, coin: str) -> str:
        """Get D1 tier string (defaults to WATCH if not found)."""
        entry = self.d1_tiers.get(coin)
        return entry["tier"] if entry else "WATCH"

    def get_d1_score(self, coin: str) -> float:
        """Get D1 best score for a coin."""
        entry = self.d1_tiers.get(coin)
        return entry["score"] if entry else 0.0

    def is_all_watch(self, coin: str) -> bool:
        """Check if ALL D1 timeframes for this coin are REJECTED (no valid setup).

        WATCH-tier coins flow to D2 for 15M resolution.
        Only coins where every TF is REJECTED are excluded.
        Coins with no D1 data yet are NOT excluded — D2 scans independently.
        """
        entry = self.d1_tiers.get(coin)
        if not entry:
            return False  # No D1 data yet — don't block D2 scanning
        tfs = entry.get("timeframes", {})
        if not tfs:
            return False  # D1 has entry but no TF breakdown — don't block
        return all(v.get("tier") == "REJECTED" for v in tfs.values())

    # ── D2 Methods ──────────────────────────────────────────────────

    async def set_d2_signal(self, coin: str, signal: Any):
        """Update D2 signal for a coin. Pass None to remove."""
        async with self._lock:
            if signal is None:
                self.d2_signals.pop(coin, None)
            else:
                self.d2_signals[coin] = signal
            self.last_d2_scan = datetime.now(timezone.utc).timestamp()

    def get_d2_signal(self, coin: str) -> Any | None:
        """Get D2 signal for a coin. Called by D3."""
        return self.d2_signals.get(coin)

    def get_all_d2_signals(self) -> dict:
        """Get all D2 signals. Called by D3 for fusion."""
        return dict(self.d2_signals)

    # ── D3 Decision Layer ───────────────────────────────────────────

    async def set_d3_decision(self, coin: str, decision: dict):
        """Update D3 decision for a coin (signal type, position sizing, action)."""
        async with self._lock:
            self.d3_decisions[coin] = {
                **decision,
                "updated_at": datetime.now(timezone.utc).timestamp(),
            }

    def get_d3_decision(self, coin: str) -> dict | None:
        """Get D3 decision for a coin."""
        return self.d3_decisions.get(coin)

    def get_all_decisions(self) -> dict:
        """Get all D3 decisions for frontend push."""
        return dict(self.d3_decisions)

    # ── Position Management ─────────────────────────────────────────

    async def set_position(self, coin: str, position: dict):
        """Record an open position."""
        async with self._lock:
            self.positions[coin] = {
                **position,
                "opened_at": datetime.now(timezone.utc).timestamp(),
            }

    def get_position(self, coin: str) -> dict | None:
        """Get position for a coin."""
        return self.positions.get(coin)

    def get_all_positions(self) -> dict:
        """Get all open positions."""
        return dict(self.positions)

    async def close_position(self, coin: str):
        """Remove position after close."""
        async with self._lock:
            self.positions.pop(coin, None)

    # ── Regime Tracking ─────────────────────────────────────────────

    async def set_regime(self, coin: str, regime_data: dict):
        """Update regime for a coin."""
        async with self._lock:
            self.regimes[coin] = {
                **regime_data,
                "updated_at": datetime.now(timezone.utc).timestamp(),
            }
            self.last_regime_update = datetime.now(timezone.utc).timestamp()

    def get_regime(self, coin: str) -> dict | None:
        """Get regime for a coin."""
        return self.regimes.get(coin)

    def get_all_regimes(self) -> dict:
        """Get all regimes."""
        return dict(self.regimes)

    def get_stats(self) -> dict:
        """Get pipeline stats for health endpoint."""
        return {
            "d1_coins": len(self.d1_tiers),
            "d2_signals": len(self.d2_signals),
            "d3_fusion": len(self.d3_decisions),
            "last_d1_scan": self.last_d1_scan,
            "last_d2_scan": self.last_d2_scan,
            "last_d3_fusion": self.last_d3_fusion,
        }


# Singleton access
state_store = StateStore()
