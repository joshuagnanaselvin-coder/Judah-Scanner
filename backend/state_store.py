"""Shared State Store — loose coupling between all dimensions.

D1, D2, and D3 communicate exclusively through this store.
No function calls between dimensions. No circular dependencies.

Thread-safe for async access via asyncio.Lock.

Phase 15 — State Ownership
===========================
Each field has a single owner (writer) and well-defined readers.
Cross-writes are prohibited — enforcement is by convention, validated in tests.

Field Ownership:
  d1_tiers          Owner: D1 scanner (scanner.py)         Reader: D3 (signal_fusion), API
  d2_signals        Owner: D2 engine (ltf_engine.py)       Reader: D3 (signal_fusion), API
  d3_decisions      Owner: D3 fusion (signal_fusion.py)    Reader: API, WS hub
  d1_status         Owner: D1 scanner                       Reader: D3, API, tests
  d2_status         Owner: D2 engine                        Reader: D3, API, tests
  last_snapshot_id  Owner: Bootstrap/snapshot system        Reader: D1, D2, D3 (provenance)
  last_d1_scan      Owner: D1 scanner                       Reader: Health checks, API
  last_d2_scan      Owner: D2 engine                        Reader: Health checks, API
  last_d3_fusion    Owner: D3 fusion                        Reader: Health checks, API

Lifecycle:
  Valid:   After scan cycle completes
  Expires: TTL per tier (SIGNAL_TTL_MINUTES from config)
  Restart: Cleared on /api/restart, otherwise survive
  Max:     MAX_COINS config limit
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

        # Phase 11: No Silent Failures — D1/D2 status tracking
        self.d1_status: str = "UNKNOWN"
        self.d2_status: str = "UNKNOWN"
        self.d1_status_reason: str = ""
        self.d2_status_reason: str = ""
        self.d1_status_updated_at: float = 0.0
        self.d2_status_updated_at: float = 0.0

        # Latest DecisionSnapshot provenance (Phase 1 of Top 1% plan)
        self.last_snapshot_id: str = ""
        self.last_snapshot_ts: float = 0.0

        # D2 snapshot (separate from D1 — prevents D2 overwriting D1's snapshot)
        self.last_d2_snapshot_id: str = ""
        self.last_d2_snapshot_ts: float = 0.0

    async def set_timestamp(self, field: str, ts: float = None):
        """Thread-safe timestamp setter for last_d1_scan / last_d2_scan / last_d3_fusion."""
        if ts is None:
            ts = datetime.now(timezone.utc).timestamp()
        async with self._lock:
            if hasattr(self, field):
                setattr(self, field, ts)

    async def set_snapshot_info(self, snapshot_id: str, snapshot_ts: float):
        """Record the latest DecisionSnapshot ID for provenance.

        D3 reads this to know which snapshot its decisions were derived from.
        Thread-safe — protected by the instance lock.
        """
        async with self._lock:
            self.last_snapshot_id = snapshot_id
            self.last_snapshot_ts = snapshot_ts

    async def set_d2_snapshot_info(self, snapshot_id: str, snapshot_ts: float):
        """Record D2's snapshot ID (separate from D1's).

        D2 builds its own SnapshotBuilder for 15M candle quality checks.
        This must NOT overwrite D1's snapshot_id — D3 needs both.
        """
        async with self._lock:
            self.last_d2_snapshot_id = snapshot_id
            self.last_d2_snapshot_ts = snapshot_ts

    # ── Phase 11: No Silent Failures — Status Tracking ─────────────────

    async def set_d1_status(self, status: str, reason: str = ""):
        """Set D1 scanner status. Protected by lock.

        Args:
            status: One of IDLE, SCANNING, DEGRADED, ERROR, COMPLETE
            reason: Optional machine-readable reason code
        """
        async with self._lock:
            self.d1_status = status
            self.d1_status_reason = reason
            self.d1_status_updated_at = datetime.now(timezone.utc).timestamp()
            if status in ("DEGRADED", "ERROR"):
                logger.warning(f"[state] D1 status={status} reason={reason}")
            else:
                logger.debug(f"[state] D1 status={status} reason={reason}")

    async def set_d2_status(self, status: str, reason: str = ""):
        """Set D2 scanner status. Protected by lock.

        Args:
            status: One of IDLE, SCANNING, DEGRADED, ERROR, COMPLETE
            reason: Optional machine-readable reason code
        """
        async with self._lock:
            self.d2_status = status
            self.d2_status_reason = reason
            self.d2_status_updated_at = datetime.now(timezone.utc).timestamp()
            if status in ("DEGRADED", "ERROR"):
                logger.warning(f"[state] D2 status={status} reason={reason}")
            else:
                logger.debug(f"[state] D2 status={status} reason={reason}")

    def get_d1_status(self) -> dict:
        """Get current D1 status with metadata."""
        return {
            "status": self.d1_status,
            "reason": self.d1_status_reason,
            "updated_at": self.d1_status_updated_at,
        }

    def get_d2_status(self) -> dict:
        """Get current D2 status with metadata."""
        return {
            "status": self.d2_status,
            "reason": self.d2_status_reason,
            "updated_at": self.d2_status_updated_at,
        }

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
        """Update D2 signal for a coin. Pass None to remove.

        Owner: LTF engine (backend/engines/ltf_engine.py)
        Readers: D3 fusion engine (backend/engines/signal_fusion.py), API
        Eviction: None when MAX_D2_SIGNALS is None (no cap)
        """
        from backend.config import MAX_D2_SIGNALS
        async with self._lock:
            if signal is None:
                self.d2_signals.pop(coin, None)
            else:
                # Enforce cap — evict weakest signal if at limit
                if MAX_D2_SIGNALS is not None and len(self.d2_signals) >= MAX_D2_SIGNALS:
                    weakest = min(self.d2_signals.items(),
                                  key=lambda x: float(getattr(x[1], 'score', 0)))
                    del self.d2_signals[weakest[0]]
                    logger.debug(f"[state] Evicted D2 signal {weakest[0]} (cap {MAX_D2_SIGNALS})")
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
        """Update D3 decision for a coin (signal type, position sizing, action).

        Owner: D3 fusion engine (backend/engines/signal_fusion.py)
        Readers: API endpoints, ws_hub (broadcasts to /ws-fusion)
        Eviction: Cleared by fusion engine on signal expiry or signal type=None
        """
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

    async def close_position(self, coin: str, pnl_pct: float = 0.0):
        """Remove position after close and notify risk authority."""
        async with self._lock:
            pos = self.positions.pop(coin, None)

        if pos is not None:
            try:
                from backend.risk_authority import risk_authority
                risk_authority.close_position(coin, pnl_pct)
                logger.info(f"[state_store] Closed position on {coin} (pnl_pct={pnl_pct:.2%})")
            except Exception as exc:
                logger.warning(f"[state_store] risk_authority notify failed for {coin}: {exc}")

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

    # ── Phase 20: Restart/Recovery ────────────────────────────────────

    async def clear(self, clear_signals: bool = True, clear_decisions: bool = True,
                    clear_regimes: bool = True):
        """Full state reset. Called during scanner restart.

        Args:
            clear_signals: Clear D1/D2 state (tiers + signals)
            clear_decisions: Clear D3 decisions
            clear_regimes: Clear market regimes

        Safe to call multiple times — idempotent.
        """
        async with self._lock:
            if clear_signals:
                self.d1_tiers.clear()
                self.d2_signals.clear()
                self.d1_status = "UNKNOWN"
                self.d2_status = ""
                self.d1_status_reason = ""
                self.d2_status_reason = ""
            if clear_decisions:
                self.d3_decisions.clear()
            if clear_regimes:
                self.regimes.clear()
                self.last_regime_update = 0.0
            # Reset timestamps (will be set by first scan cycle)
            self.last_d1_scan = 0.0
            self.last_d2_scan = 0.0
            self.last_d3_fusion = 0.0
            self.last_snapshot_id = ""
            self.last_snapshot_ts = 0.0
            self.last_d2_snapshot_id = ""
            self.last_d2_snapshot_ts = 0.0
        logger.info("[state_store] State cleared — restart ready")

    async def recover(self, snapshot_id: str) -> dict:
        """Recover state from a snapshot ID after crash/restart.

        Returns recovery report:
            - EvidenceStore records for this snapshot
            - Stale signals (> TTL) that need revalidation
            - State consistency check results

        This does NOT restore state — it reports what needs attention.
        Actual state is rebuilt by the next scan cycle.
        """
        report = {
            "snapshot_id": snapshot_id,
            "evidence_records": 0,
            "stale_signals": 0,
            "stale_d2_signals": 0,
            "consistency_issues": [],
        }

        # Check evidence store
        try:
            ev = __import__("backend.evidence_store", fromlist=["evidence_store"]).evidence_store
            ev_data = ev.get_for_snapshot(snapshot_id)
            total = sum(
                len(recs)
                for sym_dims in ev_data.values()
                for recs in sym_dims.values()
            )
            report["evidence_records"] = total
        except Exception as exc:
            report["consistency_issues"].append(f"evidence_store: {exc}")

        # Check for stale D1 signals (no recent scan timestamp)
        if self.last_d1_scan > 0:
            import time as _time
            age_sec = _time.time() - self.last_d1_scan
            if age_sec > 900:  # 15 min
                report["stale_signals"] = len(self.d1_tiers)

        # Check D2 signal freshness
        from backend.config import D2_SIGNAL_TTL_MINUTES
        ttl_sec = D2_SIGNAL_TTL_MINUTES * 60
        import time as _time
        now = _time.time()
        stale_d2 = 0
        for coin, sig in self.d2_signals.items():
            born = getattr(sig, 'born_at', None)
            if born:
                born_ts = born.timestamp() if hasattr(born, 'timestamp') else float(born)
                if (now - born_ts) > ttl_sec:
                    stale_d2 += 1
        report["stale_d2_signals"] = stale_d2

        # Verify no orphaned decisions (D3 decision for coin with no D2 signal)
        for coin in self.d3_decisions:
            if coin not in self.d2_signals:
                report["consistency_issues"].append(f"orphaned_d3_decision: {coin}")

        return report

    def get_stats(self) -> dict:
        """Get pipeline stats for health endpoint."""
        return {
            "d1_coins": len(self.d1_tiers),
            "d1_input_count": len(self.d1_tiers),
            "d1_output_count": sum(
                1 for entry in self.d1_tiers.values()
                if entry.get("tier") not in (None, "REJECTED")
            ),
            "d2_signals": len(self.d2_signals),
            "d3_fusion": len(self.d3_decisions),
            "last_d1_scan": self.last_d1_scan,
            "last_d2_scan": self.last_d2_scan,
            "last_d3_fusion": self.last_d3_fusion,
        }


# Singleton access
state_store = StateStore()
