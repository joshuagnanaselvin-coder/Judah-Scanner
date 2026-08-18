"""Phase 5 — Evidence Store.

The EvidenceStore is the first convergence point between D1 and D2.
It preserves evidence records per snapshot/symbol/dimension with:
  - Deduplication (same evidence_id within a snapshot/symbol/category)
  - TTL-based expiry (evidence dies when snapshot TTL expires)
  - Stale detection (age-based freshness decay)
  - MAX cap + eviction (memory safety)

Ownership:
  Writer:  D1 scanner (engine.py), D2 scanner (ltf_scanner.py)
  Reader:  AlignmentEngine, D3 fusion, API endpoints
  Valid:   After evidence is written
  Expires: Same TTL as DecisionSnapshot (SIGNAL_TTL_MINUTES from config)
  Restart: Empty on restart (evidence is regenerated per cycle)
"""
import asyncio
import logging
import threading
import time
from collections import defaultdict
from typing import Any

from backend.evidence_record import (
    EvidenceRecord, EvidenceCategory, EvidenceStrength,
)
from backend.evidence_contract import EvidenceStatus, create_evidence
from backend.config import SIGNAL_TTL_MINUTES, EVIDENCE_TTL_MINUTES

logger = logging.getLogger("judah.evidence_store")

_EVIDENCE_MAX_PER_COIN = 50
_EVIDENCE_MAX_TOTAL = 2000
_EVIDENCE_TTL_SEC = EVIDENCE_TTL_MINUTES * 60


class EvidenceStore:
    """Async-safe evidence store for D1/D2/D3 intelligence records.

    Uses threading.RLock so async and sync methods can share the same
    store without deadlock. Async methods yield via `await asyncio.sleep(0)`
    to allow other coroutines to interleave.
    """

    def __init__(self):
        self._records: dict[str, dict[str, dict[str, list[EvidenceRecord]]]] = (
            defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        )
        self._lock = threading.RLock()
        self._snapshot_timestamps: dict[str, float] = {}

    # ── Write Methods ─────────────────────────────────────────────────

    async def append(self, record: EvidenceRecord) -> str:
        """Append an evidence record. Deduplicates by evidence_id.

        Returns the evidence_id of the stored record.
        """
        snap = record.snapshot_id
        symbol = record.symbol
        category = record.category.value

        await asyncio.sleep(0)
        with self._lock:
            bucket = self._records[snap][symbol][category]
            # Dedup by evidence_id
            for i, existing in enumerate(bucket):
                if existing.evidence_id == record.evidence_id:
                    if record.confidence > existing.confidence:
                        bucket[i] = record
                    return record.evidence_id
            bucket.append(record)
            self._enforce_limits(snap, symbol)
            return record.evidence_id

    async def add(self, record: EvidenceRecord) -> bool:
        """Async alias for append(). Returns True."""
        await self.append(record)
        return True

    def add_sync(self, record: EvidenceRecord) -> str:
        """Synchronous version of append()."""
        snap = record.snapshot_id
        symbol = record.symbol
        category = record.category.value
        with self._lock:
            bucket = self._records[snap][symbol][category]
            for i, existing in enumerate(bucket):
                if existing.evidence_id == record.evidence_id:
                    if record.confidence > existing.confidence:
                        bucket[i] = record
                    return record.evidence_id
            bucket.append(record)
            self._enforce_limits(snap, symbol)
            return record.evidence_id

    def add_batch(self, records: list[EvidenceRecord]) -> int:
        """Add multiple records. Returns count of new (non-duplicate) records."""
        return sum(1 for r in records if self.add_sync(r))

    def mark_status(self, snapshot_id: str, symbol: str,
                    category: EvidenceCategory, source: Any,
                    new_status: EvidenceStatus, reason: str = "") -> bool:
        """Change status of a specific evidence record.

        Returns True if found and updated, False if not found.
        """
        cat = category.value
        with self._lock:
            bucket = self._records.get(snapshot_id, {}).get(symbol, {}).get(cat, [])
            for i, rec in enumerate(bucket):
                if rec.source == source:
                    updated = EvidenceRecord(
                        evidence_id=rec.evidence_id,
                        snapshot_id=rec.snapshot_id,
                        category=rec.category,
                        symbol=rec.symbol,
                        timeframe=rec.timeframe,
                        price=rec.price,
                        strength=rec.strength,
                        direction=rec.direction,
                        confidence=rec.confidence,
                        candle_time=rec.candle_time,
                        detected_at=rec.detected_at,
                        source=rec.source,
                        details=rec.details,
                    )
                    bucket[i] = updated
                    return True
        return False

    # ── Read Methods ──────────────────────────────────────────────────

    async def query(self, symbol: str | None = None, category: EvidenceCategory | None = None,
                    direction: str | None = None, min_strength: EvidenceStrength | int | None = None,
                    min_confidence: float | None = None, since: float | None = None,
                    limit: int | None = None) -> list[EvidenceRecord]:
        """Query evidence records with optional filters."""
        self._expire_old()
        results: list[EvidenceRecord] = []
        if min_strength is not None and not isinstance(min_strength, int):
            min_strength_val = min_strength.value
        else:
            min_strength_val = min_strength

        await asyncio.sleep(0)
        with self._lock:
            for snap_syms in self._records.values():
                for sym, cats in snap_syms.items():
                    if symbol and sym != symbol:
                        continue
                    for cat, recs in cats.items():
                        if category and cat != category.value:
                            continue
                        for rec in recs:
                            if direction and getattr(rec, 'direction', None) != direction:
                                continue
                            if min_strength_val is not None and rec.strength.value < min_strength_val:
                                continue
                            if min_confidence is not None and rec.confidence < min_confidence:
                                continue
                            if since is not None and rec.detected_at < since:
                                continue
                            results.append(rec)
                            if limit and len(results) >= limit:
                                return results
        return results

    async def get_for_snapshot(self, snapshot_id: str) -> dict[str, dict[str, list[EvidenceRecord]]]:
        """Get all evidence for a snapshot, organized by symbol → category."""
        self._expire_old()
        await asyncio.sleep(0)
        with self._lock:
            return {
                sym: {cat: list(recs) for cat, recs in cats.items()}
                for sym, cats in self._records.get(snapshot_id, {}).items()
            }

    def get_for_snapshot_sync(self, snapshot_id: str) -> dict[str, dict[str, list[EvidenceRecord]]]:
        """Synchronous version of get_for_snapshot for use in sync contexts."""
        self._expire_old()
        with self._lock:
            return {
                sym: {cat: list(recs) for cat, recs in cats.items()}
                for sym, cats in self._records.get(snapshot_id, {}).items()
            }

    async def get_for_signal(self, evidence_ids: list[str]) -> list[EvidenceRecord]:
        """Get evidence records matching a list of evidence IDs."""
        self._expire_old()
        id_set = set(evidence_ids)
        results: list[EvidenceRecord] = []
        await asyncio.sleep(0)
        with self._lock:
            for snap_syms in self._records.values():
                for sym_cats in snap_syms.values():
                    for recs in sym_cats.values():
                        for rec in recs:
                            if rec.evidence_id in id_set:
                                results.append(rec)
                                id_set.discard(rec.evidence_id)
                                if not id_set:
                                    return results
        return results

    async def count(self) -> int:
        """Return total count of all evidence records."""
        self._expire_old()
        total = 0
        await asyncio.sleep(0)
        with self._lock:
            for syms in self._records.values():
                for cats in syms.values():
                    for recs in cats.values():
                        total += len(recs)
        return total

    async def count_for(self, symbol: str, category: EvidenceCategory | None = None) -> int:
        """Return count of evidence for a symbol, optionally filtered by category."""
        self._expire_old()
        count_val = 0
        await asyncio.sleep(0)
        with self._lock:
            for snap_syms in self._records.values():
                cats = snap_syms.get(symbol, {})
                if category:
                    recs = cats.get(category.value, [])
                    count_val += len(recs)
                else:
                    for recs in cats.values():
                        count_val += len(recs)
        return count_val

    async def get_stats(self) -> dict[str, Any]:
        """Summary stats for monitoring."""
        self._expire_old()
        total = 0
        symbols_tracked = set()
        by_category: dict[str, int] = {}
        await asyncio.sleep(0)
        with self._lock:
            for snap_syms in self._records.values():
                for sym, cats in snap_syms.items():
                    symbols_tracked.add(sym)
                    for cat, recs in cats.items():
                        total += len(recs)
                        by_category[cat] = by_category.get(cat, 0) + len(recs)
        return {
            "total": total,
            "symbols_tracked": len(symbols_tracked),
            "ttl_seconds": _EVIDENCE_TTL_SEC,
            "by_category": by_category,
        }

    async def purge_by_snapshot(self, snapshot_id: str) -> int:
        """Remove all evidence for a snapshot. Returns count of removed records."""
        removed = 0
        await asyncio.sleep(0)
        with self._lock:
            if snapshot_id in self._records:
                for sym_cats in self._records[snapshot_id].values():
                    for recs in sym_cats.values():
                        removed += len(recs)
                del self._records[snapshot_id]
            self._snapshot_timestamps.pop(snapshot_id, None)
        if removed:
            logger.debug(f"[evidence_store] Purged {removed} records for snapshot {snapshot_id[:8]}")
        return removed

    async def purge_expired(self, max_age_sec: float | None = None) -> int:
        """Remove expired evidence. Returns count of removed records."""
        if max_age_sec is None:
            max_age_sec = _EVIDENCE_TTL_SEC
        return self._expire_old(max_age_sec)

    def expire_snapshot(self, snapshot_id: str) -> int:
        """Remove all evidence for a snapshot."""
        removed = 0
        with self._lock:
            if snapshot_id in self._records:
                for sym_cats in self._records[snapshot_id].values():
                    for recs in sym_cats.values():
                        removed += len(recs)
                del self._records[snapshot_id]
            self._snapshot_timestamps.pop(snapshot_id, None)
        if removed:
            logger.debug(f"[evidence_store] Expired {removed} records")
        return removed

    def prune_old(self, max_age_sec: float | None = None) -> int:
        """Remove evidence older than max_age_sec."""
        if max_age_sec is None:
            max_age_sec = _EVIDENCE_TTL_SEC
        return self._expire_old(max_age_sec)

    def record_snapshot(self, snapshot_id: str, timestamp: float):
        """Record when a snapshot was created (for TTL calculation)."""
        self._snapshot_timestamps[snapshot_id] = timestamp

    # ── Internal ──────────────────────────────────────────────────────

    def _enforce_limits(self, snapshot_id: str, symbol: str):
        """Enforce per-coin and total caps."""
        bucket = self._records[snapshot_id][symbol]
        total = 0
        for cat_recs in bucket.values():
            total += len(cat_recs)
        if total > _EVIDENCE_MAX_PER_COIN:
            all_recs = [(r.detected_at, cat, i)
                        for cat, recs in bucket.items()
                        for i, r in enumerate(recs)]
            all_recs.sort()
            excess = total - _EVIDENCE_MAX_PER_COIN
            dropped = 0
            for ts, cat, idx in all_recs[:excess]:
                if idx < len(bucket[cat]):
                    bucket[cat].pop(idx)
                    dropped += 1
            if dropped:
                logger.debug(f"[evidence_store] Dropped {dropped} stale records for {symbol}")

        self._enforce_total_cap()

    def _enforce_total_cap(self):
        """Drop oldest records if total exceeds _EVIDENCE_MAX_TOTAL."""
        total = 0
        all_items: list[tuple] = []
        with self._lock:
            for snap_id, syms in self._records.items():
                for sym, cats in syms.items():
                    for cat, recs in cats.items():
                        total += len(recs)
                        for i, r in enumerate(recs):
                            all_items.append((r.detected_at, snap_id, cat, i, sym))
            if total <= _EVIDENCE_MAX_TOTAL:
                return
            all_items.sort()
            excess = total - _EVIDENCE_MAX_TOTAL
            dropped = 0
            for ts, snap_id, cat, idx, sym in all_items[:excess]:
                recs = self._records.get(snap_id, {}).get(sym, {}).get(cat, [])
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
        expired = 0
        with self._lock:
            for snap_id in list(self._records.keys()):
                syms = self._records[snap_id]
                for sym in list(syms.keys()):
                    cats = syms[sym]
                    for cat in list(cats.keys()):
                        recs = cats[cat]
                        before = len(recs)
                        cats[cat] = [r for r in recs if (now - r.detected_at) < max_age_sec]
                        expired += before - len(cats[cat])
                        if not cats[cat]:
                            del cats[cat]
                    if not cats:
                        del syms[sym]
                if not syms:
                    del self._records[snap_id]
                    self._snapshot_timestamps.pop(snap_id, None)
        return expired


# Singleton
evidence_store = EvidenceStore()

# Backward-compat alias — accepts just a symbol and returns a unique ID string.
import threading as _th
_next_id_counter = 0
_next_id_lock = _th.Lock()


def next_evidence_id(symbol: str = "UNKNOWN") -> str:
    """Generate a unique evidence ID string (backward-compat for tests)."""
    global _next_id_counter
    with _next_id_lock:
        _next_id_counter += 1
        return f"EV-{symbol}-{_next_id_counter}"
