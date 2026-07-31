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

        # D3: fusion results per coin
        # key="KAVAUSDT" → {"bucket": "READY", "updated_at": ts, "d1_tier": "SNIPER", "d2_score": 75}
        self.d3_fusion: dict[str, dict] = {}

        # Global timestamps
        self.last_d1_scan: float = 0.0
        self.last_d2_scan: float = 0.0
        self.last_d3_fusion: float = 0.0

    async def set_timestamp(self, field: str, ts: float = None):
        """Thread-safe timestamp setter for last_d1_scan / last_d2_scan / last_d3_fusion."""
        if ts is None:
            ts = datetime.now(timezone.utc).timestamp()
        async with self._lock:
            if hasattr(self, field):
                setattr(self, field, ts)

    # ── D1 Methods ──────────────────────────────────────────────────

    async def set_d1_tier(self, coin: str, tier: str, score: float, timeframes: dict):
        """Update D1 tier for a coin. Called by D1 scanner."""
        async with self._lock:
            self.d1_tiers[coin] = {
                "tier": tier,
                "score": score,
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
        """Check if ALL D1 timeframes for this coin are WATCH."""
        entry = self.d1_tiers.get(coin)
        if not entry:
            return True  # No D1 data = treat as WATCH
        tfs = entry.get("timeframes", {})
        if not tfs:
            return True
        return all(v.get("tier") == "WATCH" for v in tfs.values())

    # ── D2 Methods ──────────────────────────────────────────────────

    async def set_d2_signal(self, coin: str, signal: Any):
        """Update D2 signal for a coin. Called by D2 engine."""
        async with self._lock:
            self.d2_signals[coin] = signal
            self.last_d2_scan = datetime.now(timezone.utc).timestamp()

    def get_d2_signal(self, coin: str) -> Any | None:
        """Get D2 signal for a coin. Called by D3."""
        return self.d2_signals.get(coin)

    def get_all_d2_signals(self) -> dict:
        """Get all D2 signals. Called by D3 for fusion."""
        return dict(self.d2_signals)

    # ── D3 Methods ──────────────────────────────────────────────────

    async def set_d3_fusion(self, coin: str, fusion: dict):
        """Update D3 fusion result. Called by D3 engine."""
        async with self._lock:
            self.d3_fusion[coin] = {
                **fusion,
                "updated_at": datetime.now(timezone.utc).timestamp(),
            }
            self.last_d3_fusion = datetime.now(timezone.utc).timestamp()

    def get_d3_fusion(self, coin: str) -> dict | None:
        """Get D3 fusion for a coin. Called by frontend."""
        return self.d3_fusion.get(coin)

    def get_all_fusion(self) -> list:
        """Get all fusion results for frontend push."""
        now = datetime.now(timezone.utc).timestamp()
        results = []
        for coin, fusion in self.d3_fusion.items():
            age = now - fusion.get("updated_at", 0)
            # Only include non-expired signals
            if age < 1800:  # 30 min TTL for display
                results.append(fusion)
        return sorted(results, key=lambda x: x.get("d2_score", 0), reverse=True)

    # ── Utility ─────────────────────────────────────────────────────

    def should_scan_d2(self, coin: str) -> bool:
        """Check if D2 should scan this coin (not all-WATCH on D1)."""
        return not self.is_all_watch(coin)

    def get_active_coins(self) -> list[str]:
        """Get coins with any D1 signal (for D2 targeting).

        MTF leak mitigation: only include coins whose D1 data is fresh
        (updated_at < D1_TTL_SECONDS ago). Prevents stale HTF context
        from driving LTF entry scans.
        """
        import time
        from backend.config import D1_TTL_SECONDS
        cutoff = time.time() - D1_TTL_SECONDS
        return [c for c, d in self.d1_tiers.items()
                if not self.is_all_watch(c) and d.get("updated_at", 0) > cutoff]

    def get_stats(self) -> dict:
        """Get pipeline stats for health endpoint."""
        return {
            "d1_coins": len(self.d1_tiers),
            "d2_signals": len(self.d2_signals),
            "d3_fusion": len(self.d3_fusion),
            "last_d1_scan": self.last_d1_scan,
            "last_d2_scan": self.last_d2_scan,
            "last_d3_fusion": self.last_d3_fusion,
        }


# Singleton access
state_store = StateStore()
