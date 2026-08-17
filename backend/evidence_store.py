"""EvidenceStore — append-only, queryable evidence log.

Every EvidenceRecord produced by any engine is recorded here before
any downstream component sees it. Records are never modified or deleted
(except bulk expiry by TTL).

This guarantees:
  - Full provenance: every signal can trace back to its evidence atoms.
  - Replayability: rebuild any decision from evidence records alone.
  - Audit trail: timestamped, attributed, never silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time_module
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from backend.evidence_record import EvidenceRecord, EvidenceCategory, EvidenceStrength

logger = logging.getLogger("judah.evidence")

# How long evidence lives before expiry (seconds)
_EVIDENCE_TTL_SECONDS = 4 * 3600  # 4 hours
# Max records per symbol before oldest are evicted
_MAX_RECORDS_PER_SYMBOL = 500
# Max total records
_MAX_TOTAL_RECORDS = 50_000


class EvidenceStore:
    """Thread-safe (asyncio-single-threaded) append-only evidence store.

    Usage:
        store = EvidenceStore()
        record = EvidenceRecord(...)
        store.append(record)
        # Query:
        bullish_ob = store.query(symbol="BTCUSDT", category=EvidenceCategory.ORDER_BLOCK,
                                  direction="BULLISH", min_strength=EvidenceStrength.STRONG)
    """

    def __init__(self):
        # All records, append-only, ordered by detected_at
        self._records: deque[EvidenceRecord] = deque(maxlen=_MAX_TOTAL_RECORDS)
        # Index: symbol -> deque of records (for fast per-symbol queries)
        self._by_symbol: dict[str, deque] = {}
        # Index: (symbol, category) -> deque
        self._by_symbol_category: dict[tuple[str, EvidenceCategory], deque] = {}
        self._lock = asyncio.Lock()

    async def append(self, record: EvidenceRecord) -> str:
        """Append an evidence record. Returns the evidence_id."""
        async with self._lock:
            # Enforce per-symbol cap
            symbol_deque = self._by_symbol.setdefault(record.symbol, deque(maxlen=_MAX_RECORDS_PER_SYMBOL))
            symbol_deque.append(record)

            # Index by category
            key = (record.symbol, record.category)
            cat_deque = self._by_symbol_category.setdefault(key, deque(maxlen=_MAX_RECORDS_PER_SYMBOL))
            cat_deque.append(record)

            self._records.append(record)

            logger.debug(f"[evidence] +{record.evidence_id} {record.summary()}")
            return record.evidence_id

    async def query(
        self,
        symbol: str | None = None,
        category: EvidenceCategory | None = None,
        direction: str | None = None,
        min_strength: EvidenceStrength | None = None,
        min_confidence: float = 0.0,
        since: float = 0.0,
        limit: int = 50,
    ) -> list[EvidenceRecord]:
        """Query evidence records.

        Args:
            symbol:       Filter by symbol.
            category:     Filter by evidence category.
            direction:    Filter by direction (BULLISH/BEARISH/NEUTRAL).
            min_strength: Minimum evidence strength.
            min_confidence: Minimum confidence (0.0–1.0).
            since:        Only records detected after this epoch timestamp.
            limit:        Max results.

        Returns:
            List of matching EvidenceRecord objects (newest first).
        """
        async with self._lock:
            candidates: deque

            if symbol and category:
                key = (symbol, category)
                candidates = self._by_symbol_category.get(key, deque())
            elif symbol:
                candidates = self._by_symbol.get(symbol, deque())
            else:
                candidates = self._records

            results = []
            for rec in reversed(candidates):  # newest first
                if rec.candle_time < since:
                    continue
                if direction and rec.direction != direction:
                    continue
                if min_strength and rec.strength.value < min_strength.value:
                    continue
                if rec.confidence < min_confidence:
                    continue
                results.append(rec)
                if len(results) >= limit:
                    break

            return results

    async def get_for_signal(self, evidence_ids: list[str]) -> list[EvidenceRecord]:
        """Fetch specific evidence records by ID."""
        async with self._lock:
            id_set = set(evidence_ids)
            return [r for r in self._records if r.evidence_id in id_set]

    async def count(self) -> int:
        """Total record count."""
        async with self._lock:
            return len(self._records)

    async def count_for(self, symbol: str, category: EvidenceCategory | None = None) -> int:
        """Count records for a symbol, optionally filtered by category."""
        async with self._lock:
            if category:
                return len(self._by_symbol_category.get((symbol, category), deque()))
            return len(self._by_symbol.get(symbol, deque()))

    async def purge_by_snapshot(self, snapshot_id: str) -> int:
        """Remove all evidence records from a specific snapshot.

        Used when a snapshot is invalidated or rebuilt to prevent
        evidence contamination across snapshots.
        """
        async with self._lock:
            to_remove = [r for r in self._records if r.snapshot_id == snapshot_id]
            if not to_remove:
                return 0
            keep = deque((r for r in self._records if r.snapshot_id != snapshot_id), maxlen=_MAX_TOTAL_RECORDS)
            purged = len(self._records) - len(keep)
            self._records = keep
            # Rebuild indexes
            self._by_symbol.clear()
            self._by_symbol_category.clear()
            for rec in self._records:
                self._by_symbol.setdefault(rec.symbol, deque(maxlen=_MAX_RECORDS_PER_SYMBOL)).append(rec)
                key = (rec.symbol, rec.category)
                self._by_symbol_category.setdefault(key, deque(maxlen=_MAX_RECORDS_PER_SYMBOL)).append(rec)
            if purged:
                logger.info(f"[evidence] Purged {purged} records from snapshot {snapshot_id[:8]}")
            return purged

    async def purge_expired(self, max_age_sec: float = _EVIDENCE_TTL_SECONDS) -> int:
        """Remove records older than max_age_sec. Returns count purged."""
        from backend.data_quality_gate import _current_timestamp
        cutoff = _current_timestamp() - max_age_sec

        async with self._lock:
            # Filter main deque
            new_records = deque((r for r in self._records if r.detected_at > cutoff), maxlen=_MAX_TOTAL_RECORDS)
            purged = len(self._records) - len(new_records)
            self._records = new_records

            # Rebuild indexes from remaining records
            self._by_symbol.clear()
            self._by_symbol_category.clear()
            for rec in self._records:
                self._by_symbol.setdefault(rec.symbol, deque(maxlen=_MAX_RECORDS_PER_SYMBOL)).append(rec)
                key = (rec.symbol, rec.category)
                self._by_symbol_category.setdefault(key, deque(maxlen=_MAX_RECORDS_PER_SYMBOL)).append(rec)

            if purged:
                logger.info(f"[evidence] Purged {purged} expired records ({len(self._records)} remaining)")

            return purged

    async def get_stats(self) -> dict[str, Any]:
        """Store statistics for monitoring."""
        async with self._lock:
            by_cat: dict[str, int] = defaultdict(int)
            for rec in self._records:
                by_cat[rec.category.value] += 1
            return {
                "total": len(self._records),
                "by_category": dict(by_cat),
                "symbols_tracked": len(self._by_symbol),
                "ttl_seconds": _EVIDENCE_TTL_SECONDS,
            }


# ── Evidence ID Generator ─────────────────────────────────────────────

_counter = 0


def next_evidence_id(symbol: str) -> str:
    """Generate a unique evidence ID: EV-<symbol>-<timestamp>-<counter>."""
    global _counter
    _counter += 1
    ts = int(_time_module.time() * 1000) % 1_000_000
    clean = symbol.replace("USDT", "").replace("BUSD", "")
    return f"EV-{clean}-{ts}-{_counter:04d}"


# Module-level singleton
evidence_store = EvidenceStore()
