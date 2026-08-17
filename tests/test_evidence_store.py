"""Phase 3 — EvidenceStore verification tests.

Each test creates a fresh EvidenceStore so tests are fully isolated.
"""
import asyncio
import time
import pytest
import concurrent.futures

from backend.evidence_store import EvidenceStore, next_evidence_id
from backend.evidence_record import (
    EvidenceRecord, EvidenceCategory, EvidenceStrength,
)


def _make_record(symbol="BTCUSDT", category=EvidenceCategory.ORDER_BLOCK,
                 direction="BULLISH", price=100.0,
                 strength=EvidenceStrength.MODERATE, confidence=0.7,
                 snapshot_id="snap-001", detected_at=None) -> EvidenceRecord:
    now = detected_at or time.time()
    return EvidenceRecord(
        evidence_id=next_evidence_id(symbol),
        category=category, symbol=symbol, timeframe="1H",
        price=price, strength=strength, direction=direction,
        confidence=confidence, candle_time=now, detected_at=now,
        source="test", snapshot_id=snapshot_id,
    )


def _run(store, coro_factory):
    """Run a coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ── Basic CRUD ────────────────────────────────────────────────────────

class TestEvidenceStoreBasic:

    def _loop(self):
        return asyncio.new_event_loop()

    def test_append_and_query_by_symbol(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            rec = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")
            eid = loop.run_until_complete(store.append(rec))
            results = loop.run_until_complete(store.query(symbol="BTCUSDT"))
            assert len(results) == 1
            assert results[0].evidence_id == eid
            assert results[0].symbol == "BTCUSDT"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_empty_returns_empty(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(store.query(symbol="NOCOIN"))
            assert results == []
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_by_category(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("ETHUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            loop.run_until_complete(
                store.append(_make_record("ETHUSDT", EvidenceCategory.FAIR_VALUE_GAP, "BEARISH")))

            ob = loop.run_until_complete(
                store.query(symbol="ETHUSDT", category=EvidenceCategory.ORDER_BLOCK))
            assert len(ob) == 1 and ob[0].category == EvidenceCategory.ORDER_BLOCK

            fvg = loop.run_until_complete(
                store.query(symbol="ETHUSDT", category=EvidenceCategory.FAIR_VALUE_GAP))
            assert len(fvg) == 1 and fvg[0].category == EvidenceCategory.FAIR_VALUE_GAP
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_by_direction(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("SOLUSDT", EvidenceCategory.MSB_BREAK, "BULLISH")))
            loop.run_until_complete(
                store.append(_make_record("SOLUSDT", EvidenceCategory.MSB_BREAK, "BEARISH")))

            bull = loop.run_until_complete(store.query(symbol="SOLUSDT", direction="BULLISH"))
            bear = loop.run_until_complete(store.query(symbol="SOLUSDT", direction="BEARISH"))
            assert len(bull) == 1 and bull[0].direction == "BULLISH"
            assert len(bear) == 1 and bear[0].direction == "BEARISH"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_by_min_strength(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("AVAXUSDT", EvidenceCategory.ORDER_BLOCK,
                                           "BULLISH", strength=EvidenceStrength.WEAK)))
            loop.run_until_complete(
                store.append(_make_record("AVAXUSDT", EvidenceCategory.ORDER_BLOCK,
                                           "BULLISH", strength=EvidenceStrength.STRONG)))

            strong_only = loop.run_until_complete(
                store.query(symbol="AVAXUSDT", min_strength=EvidenceStrength.STRONG))
            assert len(strong_only) == 1
            assert strong_only[0].strength == EvidenceStrength.STRONG
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_by_min_confidence(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("LINKUSDT", EvidenceCategory.DISPLACEMENT,
                                           "BULLISH", confidence=0.3)))
            loop.run_until_complete(
                store.append(_make_record("LINKUSDT", EvidenceCategory.DISPLACEMENT,
                                           "BULLISH", confidence=0.9)))

            high_conf = loop.run_until_complete(
                store.query(symbol="LINKUSDT", min_confidence=0.7))
            assert len(high_conf) == 1
            assert high_conf[0].confidence >= 0.7
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_since_timestamp(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            old_time = time.time() - 10000
            old_rec = _make_record("DOTUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH",
                                    detected_at=old_time)
            new_rec = _make_record("DOTUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")
            loop.run_until_complete(store.append(old_rec))
            loop.run_until_complete(store.append(new_rec))

            recent = loop.run_until_complete(
                store.query(symbol="DOTUSDT", since=time.time() - 100))
            assert len(recent) == 1
            assert recent[0].evidence_id == new_rec.evidence_id
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_query_limit(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            for _ in range(10):
                loop.run_until_complete(
                    store.append(_make_record("MATICUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            limited = loop.run_until_complete(store.query(symbol="MATICUSDT", limit=3))
            assert len(limited) == 3
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_get_for_signal(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            r1 = _make_record("ARBUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")
            r2 = _make_record("ARBUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")
            eid1 = loop.run_until_complete(store.append(r1))
            eid2 = loop.run_until_complete(store.append(r2))

            one = loop.run_until_complete(store.get_for_signal([eid1]))
            assert len(one) == 1 and one[0].evidence_id == eid1

            both = loop.run_until_complete(store.get_for_signal([eid1, eid2]))
            assert len(both) == 2
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_count(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("OPUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            loop.run_until_complete(
                store.append(_make_record("OPUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            assert loop.run_until_complete(store.count()) == 2
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_count_for(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("INJUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            loop.run_until_complete(
                store.append(_make_record("INJUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            loop.run_until_complete(
                store.append(_make_record("INJUSDT", EvidenceCategory.FAIR_VALUE_GAP, "BEARISH")))

            ob_count = loop.run_until_complete(
                store.count_for("INJUSDT", EvidenceCategory.ORDER_BLOCK))
            assert ob_count == 2

            total = loop.run_until_complete(store.count_for("INJUSDT"))
            assert total == 3
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_stats(self):
        store = EvidenceStore()
        loop = self._loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                store.append(_make_record("SEIUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH")))
            loop.run_until_complete(
                store.append(_make_record("SEIUSDT", EvidenceCategory.MSB_BREAK, "BEARISH")))
            stats = loop.run_until_complete(store.get_stats())
            assert stats["total"] == 2
            assert stats["symbols_tracked"] == 1
            assert stats["ttl_seconds"] == 14400
            assert "order_block" in stats["by_category"]
            assert "msb_break" in stats["by_category"]
        finally:
            loop.close()
            asyncio.set_event_loop(None)


# ── Snapshot isolation ────────────────────────────────────────────────

class TestSnapshotIsolation:

    def test_purge_by_snapshot_removes_only_that_snapshot(self):
        store = EvidenceStore()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            r_a1 = _make_record("ATOMUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", snapshot_id="snap-A")
            r_a2 = _make_record("ATOMUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", snapshot_id="snap-A")
            r_b1 = _make_record("ATOMUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", snapshot_id="snap-B")
            loop.run_until_complete(store.append(r_a1))
            loop.run_until_complete(store.append(r_a2))
            loop.run_until_complete(store.append(r_b1))

            purged = loop.run_until_complete(store.purge_by_snapshot("snap-A"))
            assert purged == 2

            remaining = loop.run_until_complete(store.query(symbol="ATOMUSDT"))
            assert len(remaining) == 1
            assert remaining[0].snapshot_id == "snap-B"
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_purge_nonexistent_snapshot(self):
        store = EvidenceStore()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            purged = loop.run_until_complete(store.purge_by_snapshot("snap-NONEXISTENT"))
            assert purged == 0
        finally:
            loop.close()
            asyncio.set_event_loop(None)


# ── TTL / Expiry ──────────────────────────────────────────────────────

class TestPurgeExpired:

    def test_purge_removes_only_old_records(self):
        store = EvidenceStore()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            old_time = time.time() - 10000
            new_time = time.time()
            loop.run_until_complete(
                store.append(_make_record("NEARUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH",
                                          detected_at=old_time)))
            loop.run_until_complete(
                store.append(_make_record("NEARUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH",
                                          detected_at=new_time)))

            purged = loop.run_until_complete(store.purge_expired(max_age_sec=5000))
            assert purged == 1

            remaining = loop.run_until_complete(store.query(symbol="NEARUSDT"))
            assert len(remaining) == 1
            assert remaining[0].evidence_id  # the new one
        finally:
            loop.close()
            asyncio.set_event_loop(None)


# ── EvidenceRecord methods ────────────────────────────────────────────

class TestEvidenceRecordMethods:

    def test_is_stale_fresh(self):
        rec = _make_record("TEST1", EvidenceCategory.ORDER_BLOCK, "BULLISH")
        assert not rec.is_stale(max_age_sec=3600)

    def test_is_stale_old(self):
        old_time = time.time() - 7200
        rec = _make_record("TEST2", EvidenceCategory.ORDER_BLOCK, "BULLISH",
                            detected_at=old_time)
        assert rec.is_stale(max_age_sec=3600)

    def test_aligns_with_same_direction(self):
        r1 = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", price=50000.0)
        r2 = _make_record("BTCUSDT", EvidenceCategory.FAIR_VALUE_GAP, "BULLISH", price=50050.0)
        assert r1.aligns_with(r2)

    def test_aligns_with_opposing_direction(self):
        r1 = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", price=50000.0)
        r2 = _make_record("BTCUSDT", EvidenceCategory.FAIR_VALUE_GAP, "BEARISH", price=50050.0)
        assert not r1.aligns_with(r2)

    def test_aligns_with_different_symbol(self):
        r1 = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", price=50000.0)
        r2 = _make_record("ETHUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", price=50000.0)
        assert not r1.aligns_with(r2)

    def test_aligns_with_neutral_blocks(self):
        r1 = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", price=50000.0)
        r2 = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "NEUTRAL", price=50000.0)
        assert not r1.aligns_with(r2)

    def test_summary_format(self):
        rec = _make_record("BTCUSDT", EvidenceCategory.ORDER_BLOCK, "BULLISH", price=50000.0)
        s = rec.summary()
        assert "order_block" in s
        assert "BTCUSDT" in s
        assert "BULLISH" in s


# ── Evidence ID generation ────────────────────────────────────────────

class TestNextEvidenceId:

    def test_unique_ids(self):
        ids = [next_evidence_id("BTCUSDT") for _ in range(20)]
        assert len(set(ids)) == 20, "Evidence IDs must be unique"

    def test_format(self):
        eid = next_evidence_id("BTCUSDT")
        assert eid.startswith("EV-")
        assert "BTC" in eid


# ── Concurrent safety ─────────────────────────────────────────────────

class TestConcurrentAppend:

    def test_concurrent_appends_same_store_unique_ids(self):
        """Multiple coroutines appending to the same store concurrently
        must all succeed with unique IDs (lock must not deadlock)."""
        store = EvidenceStore()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:

            async def do():
                tasks = []
                for i in range(25):
                    rec = _make_record(
                        f"CONC{i % 5}USDT",
                        EvidenceCategory.ORDER_BLOCK,
                        "BULLISH" if i % 2 == 0 else "BEARISH",
                    )
                    tasks.append(store.append(rec))
                return await asyncio.gather(*tasks)

            eids = loop.run_until_complete(do())
            assert len(eids) == 25
            assert len(set(eids)) == 25, "All evidence IDs must be unique"
            assert loop.run_until_complete(store.count()) == 25
        finally:
            loop.close()
            asyncio.set_event_loop(None)
