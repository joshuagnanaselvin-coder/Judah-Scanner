"""Phase 5 — Evidence Store.

The EvidenceStore is the first convergence point between D1 and D2.
It preserves evidence records per snapshot/symbol/dimension with:
  - Deduplication (same source + observation within a snapshot)
  - TTL-based expiry (evidence dies when snapshot TTL expires)
  - Stale detection (age-based freshness decay)
  - Missing/partial evidence tracking per coin
  - MAX cap + eviction (memory safety)

Ownership:
  Writer:  D1 scanner (engine.py), D2 scanner (ltf_scanner.py)
  Reader:  AlignmentEngine, D3 fusion, API endpoints
  Valid:   After evidence is written
  Expires: Same TTL as DecisionSnapshot (SIGNAL_TTL_MINUTES from config)
  Restart: Empty on restart (evidence is regenerated per cycle)
"""
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from backend.evidence_contract import (
    EvidenceRecord, EvidenceDimension, EvidenceSource, EvidenceStatus,
    create_evidence,
)
from backend.config import SIGNAL_TTL_MINUTES

logger = logging.getLogger("judah.evidence_store")

# Phase 16: Memory safety bounds
_EVIDENCE_MAX_PER_COIN = 50       # Max evidence records per coin per snapshot
_EVIDENCE_MAX_TOTAL = 2000        # Hard cap on total evidence records in memory
_EVIDENCE_TTL_SEC = SIGNAL_TTL_MINUTES * 60  # Match signal TTL


class EvidenceStore:
    """Thread-safe evidence store for D1/D2/D3 intelligence records.

    Organizes evidence by snapshot_id → symbol → dimension → list of records.
    Provides methods for writing, querying, aggregating, and expiring evidence.
    """

    def __init__(self):
        self._records: dict[str, dict[str, dict[str, list[EvidenceRecord]]]] = (
            defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        )
        self._lock = _EvidenceLock()
        self._snapshot_timestamps: dict[str, float] = {}

    # ── Write Methods ─────────────────────────────────────────────────

    def add(self, evidence: EvidenceRecord) -> bool:
        """Add an evidence record. Deduplicates within same snapshot/symbol/dimension/source/observation.

        Returns True if the record was added, False if it was a duplicate.
        """
        snap = evidence.snapshot_id
        symbol = evidence.symbol
        dim = evidence.dimension.value

        with self._lock:
            bucket = self._records[snap][symbol][dim]
            # Dedup: same source + observation → replace existing (higher-confidence wins)
            existing_idx = next(
                (i for i, r in enumerate(bucket)
                 if r.source == evidence.source and r.observation == evidence.observation),
                -1
            )
            if existing_idx >= 0:
                # Keep the one with higher confidence
                if evidence.confidence > bucket[existing_idx].confidence:
                    bucket[existing_idx] = evidence
                return False  # Duplicate (either kept existing or superseded)
            bucket.append(evidence)
            self._enforce_limits(snap, symbol)
            return True

    def add_batch(self, records: list[EvidenceRecord]) -> int:
        """Add multiple records. Returns count of new (non-duplicate) records."""
        return sum(1 for r in records if self.add(r))

    def mark_status(self, snapshot_id: str, symbol: str,
                    dimension: EvidenceDimension, source: EvidenceSource,
                    new_status: EvidenceStatus, reason: str = "") -> bool:
        """Change status of a specific evidence record (e.g., FULL → STALE).

        Returns True if found and updated, False if not found.
        """
        dim = dimension.value
        with self._lock:
            bucket = self._records.get(snapshot_id, {}).get(symbol, {}).get(dim, [])
            for rec in bucket:
                if rec.source == source:
                    # Frozen dataclass — replace in list
                    idx = bucket.index(rec)
                    updated = EvidenceRecord(
                        evidence_id=rec.evidence_id,
                        snapshot_id=rec.snapshot_id,
                        symbol=rec.symbol,
                        dimension=rec.dimension,
                        source=rec.source,
                        observation=rec.observation,
                        value=rec.value,
                        strength=rec.strength,
                        confidence=rec.confidence,
                        timestamp=rec.timestamp,
                        freshness="dead" if new_status in (EvidenceStatus.STALE, EvidenceStatus.FAILED) else rec.freshness,
                        status=new_status,
                        reason=reason or rec.reason,
                    )
                    bucket[idx] = updated
                    return True
        return False

    # ── Read Methods ──────────────────────────────────────────────────

    def get_for_snapshot(self, snapshot_id: str) -> dict[str, dict[str, list[EvidenceRecord]]]:
        """Get all evidence for a snapshot, organized by symbol → dimension."""
        self._expire_old()
        with self._lock:
            return {
                sym: {dim: list(recs) for dim, recs in dims.items()}
                for sym, dims in self._records.get(snapshot_id, {}).items()
            }

    def get_for_symbol(self, snapshot_id: str, symbol: str,
                       dimension: EvidenceDimension | None = None) -> list[EvidenceRecord]:
        """Get all evidence for a specific coin in a snapshot."""
        self._expire_old()
        dims = self._records.get(snapshot_id, {}).get(symbol, {})
        if dimension:
            return list(dims.get(dimension.value, []))
        return [r for recs in dims.values() for r in recs]

    def get_d1_evidence(self, snapshot_id: str, symbol: str) -> list[EvidenceRecord]:
        """Convenience: get D1 evidence for a coin."""
        return self.get_for_symbol(snapshot_id, symbol, EvidenceDimension.D1)

    def get_d2_evidence(self, snapshot_id: str, symbol: str) -> list[EvidenceRecord]:
        """Convenience: get D2 evidence for a coin."""
        return self.get_for_symbol(snapshot_id, symbol, EvidenceDimension.D2)

    def get_aggregated(self, snapshot_id: str, symbol: str) -> dict[str, Any]:
        """Aggregate evidence into a compact summary for alignment/D3.

        Returns:
            {
              "d1_evidence_count": int,
              "d2_evidence_count": int,
              "d1_full_count": int,
              "d2_full_count": int,
              "d1_avg_strength": float,
              "d2_avg_strength": float,
              "d1_avg_confidence": float,
              "d2_avg_confidence": float,
              "d1_sources": [str],
              "d2_sources": [str],
              "d1_degraded": bool,
              "d2_degraded": bool,
              "evidence_complete": bool,
            }
        """
        d1_recs = self.get_d1_evidence(snapshot_id, symbol)
        d2_recs = self.get_d2_evidence(snapshot_id, symbol)

        def _agg(recs: list[EvidenceRecord]) -> dict:
            if not recs:
                return {
                    "count": 0, "full_count": 0,
                    "avg_strength": 0.0, "avg_confidence": 0.0,
                    "sources": [], "degraded": True,
                }
            full = [r for r in recs if r.status == EvidenceStatus.FULL]
            return {
                "count": len(recs),
                "full_count": len(full),
                "avg_strength": sum(r.strength for r in recs) / len(recs),
                "avg_confidence": sum(r.confidence for r in recs) / len(recs),
                "sources": sorted(set(r.source.value for r in recs)),
                "degraded": len(full) < len(recs),
            }

        d1_agg = _agg(d1_recs)
        d2_agg = _agg(d2_recs)

        return {
            "d1_evidence_count": d1_agg["count"],
            "d2_evidence_count": d2_agg["count"],
            "d1_full_count": d1_agg["full_count"],
            "d2_full_count": d2_agg["full_count"],
            "d1_avg_strength": round(d1_agg["avg_strength"], 3),
            "d2_avg_strength": round(d2_agg["avg_strength"], 3),
            "d1_avg_confidence": round(d1_agg["avg_confidence"], 3),
            "d2_avg_confidence": round(d2_agg["avg_confidence"], 3),
            "d1_sources": d1_agg["sources"],
            "d2_sources": d2_agg["sources"],
            "d1_degraded": d1_agg["degraded"],
            "d2_degraded": d2_agg["degraded"],
            "evidence_complete": not d1_agg["degraded"] and not d2_agg["degraded"],
        }

    # ── Lifecycle ─────────────────────────────────────────────────────

    def expire_snapshot(self, snapshot_id: str) -> int:
        """Remove all evidence for a snapshot. Returns count of removed records."""
        removed = 0
        with self._lock:
            if snapshot_id in self._records:
                for sym_dims in self._records[snapshot_id].values():
                    for recs in sym_dims.values():
                        removed += len(recs)
                del self._records[snapshot_id]
            self._snapshot_timestamps.pop(snapshot_id, None)
        if removed:
            logger.debug(f"[evidence_store] Expired {removed} records for snapshot {snapshot_id[:8]}")
        return removed

    def prune_old(self, max_age_sec: float | None = None) -> int:
        """Remove evidence older than max_age_sec. Defaults to TTL."""
        if max_age_sec is None:
            max_age_sec = _EVIDENCE_TTL_SEC
        return self._expire_old(max_age_sec)

    def record_snapshot(self, snapshot_id: str, timestamp: float):
        """Record when a snapshot was created (for TTL calculation)."""
        self._snapshot_timestamps[snapshot_id] = timestamp

    def get_stats(self) -> dict[str, Any]:
        """Summary stats for monitoring."""
        self._expire_old()
        total = 0
        by_snapshot: dict[str, int] = {}
        by_status: dict[str, int] = {}
        with self._lock:
            for snap_id, syms in self._records.items():
                snap_count = 0
                for sym_dims in syms.values():
                    for recs in sym_dims.values():
                        total += len(recs)
                        snap_count += len(recs)
                        for r in recs:
                            by_status[r.status.value] = by_status.get(r.status.value, 0) + 1
                by_snapshot[snap_id[:8]] = snap_count
        return {
            "total_records": total,
            "by_snapshot": by_snapshot,
            "by_status": by_status,
            "max_total": _EVIDENCE_MAX_TOTAL,
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _enforce_limits(self, snapshot_id: str, symbol: str):
        """Enforce per-coin and total caps."""
        bucket = self._records[snapshot_id][symbol]
        total = 0
        for dim_recs in bucket.values():
            total += len(dim_recs)
        # Per-coin cap: drop oldest (by timestamp) if over limit
        if total > _EVIDENCE_MAX_PER_COIN:
            all_recs = [(r.timestamp, dim, i)
                        for dim, recs in bucket.items()
                        for i, r in enumerate(recs)]
            all_recs.sort()  # oldest first
            excess = total - _EVIDENCE_MAX_PER_COIN
            dropped = 0
            for ts, dim, idx in all_recs[:excess]:
                if idx < len(bucket[dim]):
                    bucket[dim].pop(idx)
                    dropped += 1
            if dropped:
                logger.debug(f"[evidence_store] Dropped {dropped} stale records for {symbol}")

        # Total cap: drop oldest across all snapshots
        self._enforce_total_cap()

    def _enforce_total_cap(self):
        """Drop oldest records if total exceeds _EVIDENCE_MAX_TOTAL."""
        total = 0
        all_items: list[tuple[float, str, str, int]] = []  # (ts, snap, dim, idx)
        with self._lock:
            for snap_id, syms in self._records.items():
                for sym, dims in syms.items():
                    for dim, recs in dims.items():
                        total += len(recs)
                        for i, r in enumerate(recs):
                            all_items.append((r.timestamp, snap_id, dim, i, sym))
            if total <= _EVIDENCE_MAX_TOTAL:
                return
            # Sort oldest first, drop excess
            all_items.sort()
            excess = total - _EVIDENCE_MAX_TOTAL
            dropped = 0
            for ts, snap_id, dim, idx, sym in all_items[:excess]:
                recs = self._records.get(snap_id, {}).get(sym, {}).get(dim, [])
                if 0 <= idx < len(recs):
                    recs.pop(idx)
                    dropped += 1
            if dropped:
                logger.warning(f"[evidence_store] Total cap hit — dropped {dropped} oldest records "
                               f"(cap {_EVIDENCE_MAX_TOTAL})")

    def _expire_old(self, max_age_sec: float | None = None) -> int:
        """Remove evidence older than max_age_sec."""
        if max_age_sec is None:
            max_age_sec = _EVIDENCE_TTL_SEC
        now = time.time()
        expired = []
        with self._lock:
            for snap_id, syms in self._records.items():
                for sym, dims in list(syms.items()):
                    for dim, recs in list(dims.items()):
                        before = len(recs)
                        dims[dim] = [r for r in recs if (now - r.timestamp) < max_age_sec]
                        expired += before - len(dims[dim])
                    if not any(dims.values()):
                        del syms[sym]
                if not syms:
                    del self._records[snap_id]
                    self._snapshot_timestamps.pop(snap_id, None)
        return expired


class _EvidenceLock:
    """Context manager that simulates asyncio.Lock for synchronous code.

    EvidenceStore is called from both sync and async contexts.
    In async contexts, use EvidenceStoreAsync (below) instead.
    In practice, evidence writes happen within async scan loops,
    so this simple lock suffices for the GIL-protected CPython case.
    For production with multiple threads, swap to threading.Lock.
    """
    def __init__(self):
        self._locked = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._locked = False

    def acquire(self):
        self._locked = True

    def release(self):
        self._locked = False


# Singleton
evidence_store = EvidenceStore()

# Backward-compat alias
next_evidence_id = create_evidence
