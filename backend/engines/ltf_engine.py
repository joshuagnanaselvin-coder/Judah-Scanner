"""Dimension 2 — 15M Scanner Orchestrator.

15M-only deep scan: scans all 500 coins on 15M timeframe.
Triggered by candle close (every 15 minutes).
Pipeline: Flow → CRT → SMC → Signal Builder → Score → Tier

D2 is fully independent — no communication with D1.
"""
from backend.config import (
    D2_SCAN_INTERVAL_SECONDS, D2_SIGNAL_TTL_MINUTES,
    TIMEFRAMES_LTF,
    SCAN_CONCURRENCY,
)
import logging
import asyncio
import time as _time_module
from datetime import datetime, timezone
from typing import Any

from backend.engines.ltf_scanner import LTFSignal
from backend.market_data import market_data
from backend.state_store import state_store
from backend.engines.ltf_scanner import scan_entry

logger = logging.getLogger("judah.ltf")

from backend.decision_snapshot import SnapshotBuilder


class LTFEngine:
    """Phase 21: Observability — each engine instance carries a stable cycle ID."""

    _engine_count = 0
    _ids: dict[int, str] = {}

    def __init__(self):
        LTFEngine._engine_count += 1
        self._id_key = id(self)
        if self._id_key not in LTFEngine._ids:
            LTFEngine._ids[self._id_key] = f"D2-{LTFEngine._engine_count:04d}"
        self.cycle_id: str = LTFEngine._ids[self._id_key]

        self.symbols: list = []
        self.running: bool = False
        self.scan_task = None
        self._scan_semaphore: asyncio.Semaphore | None = None
        self._d3_notify = None  # Wired in main.py after fusion_engine starts

    async def start(self, symbols: list):
        """Start D2 engine — 15M scanner, fully independent of D1."""
        self.symbols = symbols
        self.running = True
        self._scan_semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        logger.info(f"[ltf] [{self.cycle_id}] Starting D2 engine — {len(symbols)} coins on 15M "
                    f"(candle-close driven, ~15min cycle)")
        self.scan_task = asyncio.create_task(self._scan_loop())

    async def _scan_loop(self):
        """D2 15M scan loop — candle-close driven.

        Waits for the next 15M candle to close, then runs a full deep scan
        of all 500 coins on the 15M timeframe.
        """
        # Wait for bootstrap to complete
        await self._wait_for_candles()

        while self.running:
            try:
                # Calculate and sleep until next 15M candle close
                sleep_sec = self._seconds_until_next_close("15M")
                logger.info(f"[ltf] [{self.cycle_id}] Sleeping {sleep_sec / 60:.1f}min "
                            f"until next 15M candle close")
                await asyncio.sleep(sleep_sec)

                if not self.running:
                    break

                # Run full deep scan of ALL coins on 15M
                logger.info(f"[ltf] [{self.cycle_id}] 15M candle closed — "
                            f"starting full scan of {len(self.symbols)} coins")
                t0 = _time_module.time()
                await self._run_batch_scan()
                elapsed = _time_module.time() - t0
                logger.info(f"[ltf] [{self.cycle_id}] 15M cycle complete in {elapsed:.1f}s")

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"[ltf] [{self.cycle_id}] Scan error")
                await asyncio.sleep(60)

    async def _wait_for_candles(self):
        """Wait until we have 15M candle data for at least some coins."""
        max_wait = 120
        waited = 0
        while waited < max_wait:
            has_candles = any(
                len(market_data.get_candles(s, "15M") or []) >= 25
                for s in self.symbols[:10]
            )
            if has_candles:
                logger.info(f"[ltf] [{self.cycle_id}] 15M candles available — "
                            f"starting initial scan")
                # Run initial scan immediately
                await self._run_batch_scan()
                return
            await asyncio.sleep(5)
            waited += 5
        logger.warning(f"[ltf] [{self.cycle_id}] Timeout waiting for 15M candles — "
                       f"proceeding anyway")
        await self._run_batch_scan()

    @staticmethod
    def _seconds_until_next_close(timeframe: str) -> float:
        """Calculate seconds until the next candle close for a timeframe.

        4H candles close at: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC
        15M candles close at: every 15 min (00, 15, 30, 45 past each hour)
        """
        now = datetime.now(timezone.utc)
        tf_minutes = {"4H": 240, "15M": 15, "1H": 60, "1D": 1440}
        interval_min = tf_minutes.get(timeframe, 60)

        # Minutes since midnight
        minutes_since_midnight = now.hour * 60 + now.minute
        # Current interval number
        current_interval = minutes_since_midnight // interval_min
        # Next close time (in minutes from midnight)
        next_close_min = (current_interval + 1) * interval_min
        # Build next close datetime
        next_close_hour = next_close_min // 60
        next_close_min_rem = next_close_min % 60
        next_close = now.replace(
            hour=next_close_hour % 24,
            minute=next_close_min_rem,
            second=0,
            microsecond=0,
        )
        # If we wrapped past midnight, add a day
        if next_close_min >= 1440:
            from datetime import timedelta
            next_close += timedelta(days=next_close_min // 1440)

        delta = (next_close - now).total_seconds()
        return max(delta, 1.0)  # never return 0 or negative

    async def _run_batch_scan(self):
        """Production batch scan for D2:

        PASS 1: Revalidate + refresh existing D2 signals
        PASS 2: Scan ALL symbols for 15M entry (independent of D1)
        PASS 3: Write D2 signals to state_store
        """
        refreshed = []
        revalidated = []

        # Scan ALL symbols — D2 is independent
        scan_targets = self.symbols
        if not scan_targets:
            logger.info("[ltf] No symbols configured, skipping cycle")
            return

        # Build immutable snapshot (D2's primary timeframe is 15M)
        snap = SnapshotBuilder(market_data).build(scan_targets, htf_timeframes=[], ltf_timeframes=["15M"])
        await state_store.set_d2_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)
        logger.info(f"[ltf] Snapshot {snap.snapshot_id[:8]} — "
                    f"{sum(1 for v in snap.data_quality.values() if v == 'VALID')}/{len(snap.data_quality)} pairs VALID")

        # === PASS 1: Revalidate + refresh existing D2 signals ===
        existing = dict(state_store.get_all_d2_signals())
        for coin, sig in list(existing.items()):
            candles = market_data.get_candles(coin, "15M")
            if not candles:
                continue

            sig = _ensure_ltf_signal(coin, sig)
            if not sig:
                continue

            if sig.is_expired():
                # TTL expired — remove signal (fresh scan will create new one)
                logger.info(f"[ltf] EXPIRED {coin}: TTL exceeded")
                await state_store.set_d2_signal(coin, None)
                continue

            # Revalidate at checkpoints
            if _should_revalidate(sig):
                logger.info(f"[ltf] [revalidate] {coin} at {_age_minutes(sig):.0f}min...")
                raw = await scan_entry(coin)
                if raw:
                    # Update in-place — preserves signal_id, no duplicate cards
                    sig.update(raw)
                    await state_store.set_d2_signal(coin, sig)
                    revalidated.append(sig)
                else:
                    # Setup broken — remove
                    await state_store.set_d2_signal(coin, None)
                continue

            # Light refresh — just update age/price
            refreshed.append(sig)

        # === PASS 2: Scan for 15M entry (all coins regardless of D1) ===
        # Incremental publish: scan in batches of 50, publish each batch immediately
        # so D3 can fuse and frontend updates progressively instead of waiting for all 500.
        BATCH_SIZE = 50
        new_signals = []
        scan_tasks = []
        reval_tasks = []
        reval_awaitables = []
        failed_coins = []

        for coin in scan_targets:
            # Skip if already have a D2 signal
            if state_store.get_d2_signal(coin):
                continue
            # Skip if recently scanned
            if _was_recently_scanned(coin):
                continue
            # Pipeline handles ATR/range/quality gates internally — no pre-filters

            scan_tasks.append(coin)

        logger.debug(f"[ltf] Batch: {len(scan_tasks)} candidates to scan in "
                     f"{(len(scan_tasks) + BATCH_SIZE - 1) // BATCH_SIZE} batches of {BATCH_SIZE}")

        semaphore = self._scan_semaphore or asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _scan_with_limit(coin):
            async with semaphore:
                try:
                    return await scan_entry(coin)
                except Exception as e:
                    logger.warning(f"[ltf] Error {coin}: {e}")
                    return None

        # Scan + publish in batches for incremental data_layer updates
        total_batches = (len(scan_tasks) + BATCH_SIZE - 1) // BATCH_SIZE
        for batch_idx in range(total_batches):
            batch_start = batch_idx * BATCH_SIZE
            batch_end = min(batch_start + BATCH_SIZE, len(scan_tasks))
            batch = scan_tasks[batch_start:batch_end]

            logger.debug(f"[ltf] Scanning batch {batch_idx + 1}/{total_batches}: "
                         f"coins {batch_start + 1}-{batch_end}")

            results = await asyncio.gather(
                *[_scan_with_limit(c) for c in batch],
            )

            batch_published = 0
            for coin, result in zip(batch, results):
                if isinstance(result, Exception) or not result:
                    failed_coins.append(coin)
                    _mark_scanned(coin)
                    continue

                # scan_entry returns raw dict — wrap in LTFSignal
                ltf_sig = LTFSignal(coin, result)
                await state_store.set_d2_signal(coin, ltf_sig)
                new_signals.append(ltf_sig)
                _mark_scanned(coin)
                batch_published += 1

            logger.debug(f"[ltf] Batch {batch_idx + 1}: published {batch_published}/{len(batch)} signals")

        # Phase 11: No Silent Failures — propagate DEGRADED if any scans failed
        if failed_coins:
            await state_store.set_d2_status(
                "DEGRADED",
                reason=f"{len(failed_coins)}_scans_failed: {','.join(failed_coins[:5])}"
            )

        # === PASS 3: Count D2 signals, update state_store ===
        d2_count_this_cycle = 0
        all_d2 = state_store.get_all_d2_signals()
        for coin, sig in all_d2.items():
            if coin not in scan_targets:
                # Coin removed from symbol list — clean up stale D2 signal
                await state_store.set_d2_signal(coin, None)
                continue
            if isinstance(sig, LTFSignal):
                d2_count_this_cycle += 1

        await state_store.set_timestamp("last_d2_scan")

        # Notify D3 fusion engine — new D2 data is ready (event-driven trigger).
        # Wired in main.py after startup to avoid circular imports.
        if self._d3_notify:
            try:
                self._d3_notify()
            except Exception:
                pass

        # Console output
        if new_signals:
            for s in new_signals:
                print(f"[ltf] NEW {s.coin}: {s.tier} "
                      f"score={s.score:.1f} dir={s.direction} "
                      f"RR={s.rr1:.1f}")
        if revalidated:
            for s in revalidated:
                print(f"[ltf] [reval] {s.coin}: {s.tier} score={s.score:.1f} freshness={s.freshness}")
        print(f"[ltf] {len(new_signals)} new, {len(refreshed)} refreshed, "
              f"{len(revalidated)} revalidated, "
              f"{d2_count_this_cycle} D2 signals, "
              f"{len(scan_tasks)} candidates scanned")

        # Pipeline stage breakdown (one-cycle counts)
        from backend.engines.ltf_pipeline import _log_stage_summary
        _log_stage_summary()

    async def restart(self) -> dict:
        """Full restart: clear signals, re-bootstrap candles, immediate scan."""
        logger.info(f"[ltf] [{self.cycle_id}] Restarting D2 engine...")

        # 1. Stop scan loop
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()
            try:
                await self.scan_task
            except (asyncio.CancelledError, Exception):
                pass

        # 2. Clear D2 signals
        for coin in list(state_store.d2_signals.keys()):
            await state_store.set_d2_signal(coin, None)
        _scanned_recently.clear()

        # 3. Re-bootstrap candles
        logger.info(f"[ltf] [{self.cycle_id}] Re-bootstrapping {len(self.symbols)} pairs...")
        count = await market_data.bootstrap(self.symbols)
        logger.info(f"[ltf] [{self.cycle_id}] Bootstrapped {count} candle sets")

        # 4. Restart scan loop
        self.running = True
        self.scan_task = asyncio.create_task(self._scan_loop())

        # 5. Trigger immediate scan (fire-and-forget)
        scan_task = asyncio.create_task(self._run_batch_scan())
        scan_task.add_done_callback(
            lambda t: logger.error(f"[ltf] [{self.cycle_id}] Immediate scan failed: {t.exception()}")
            if t.exception() else None
        )

        return {"symbols": len(self.symbols), "candle_sets": count}


    def stop(self):
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()


# ── Helpers ─────────────────────────────────────────────────────────

_REVALIDATE_AGE_MIN = 8  # Revalidate after 8 minutes (half of 15M TTL)
_scanned_recently: dict = {}

_PURGE_INTERVAL = 30  # Purge stale entries every 30 mark_scanned calls
_purge_counter = 0

# Phase 16: Memory safety — max entries and TTL for _scanned_recently
_SCANNED_RECENTLY_MAX = 500       # Max entries in the recently-scanned dict
_SCANNED_RECENTLY_TTL_SEC = 300   # 5 minutes TTL


def _age_minutes(sig: LTFSignal) -> float:
    return (datetime.now(timezone.utc) - sig.born_at).total_seconds() / 60


def _should_revalidate(sig: LTFSignal) -> bool:
    return _age_minutes(sig) >= _REVALIDATE_AGE_MIN


def _mark_scanned(coin: str):
    """Phase 16: Track recently-scanned coins with TTL + MAX eviction."""
    global _purge_counter
    _scanned_recently[coin] = datetime.now(timezone.utc).timestamp()
    _purge_counter += 1

    # Periodic purge of expired entries
    if _purge_counter >= _PURGE_INTERVAL:
        _purge_counter = 0
        _purge_scanned_recently()


def _purge_scanned_recently():
    """Remove expired entries and enforce MAX cap."""
    cutoff = datetime.now(timezone.utc).timestamp() - _SCANNED_RECENTLY_TTL_SEC
    # Remove expired
    expired = [k for k, v in _scanned_recently.items() if v < cutoff]
    for k in expired:
        del _scanned_recently[k]
    # Enforce MAX cap — evict oldest if over limit
    if len(_scanned_recently) > _SCANNED_RECENTLY_MAX:
        # Sort by timestamp, drop oldest
        oldest = sorted(_scanned_recently.items(), key=lambda x: x[1])[:len(_scanned_recently) - _SCANNED_RECENTLY_MAX]
        for k, _ in oldest:
            del _scanned_recently[k]
        logger.debug(f"[ltf] Purged {len(oldest)} from _scanned_recently (cap {_SCANNED_RECENTLY_MAX})")


def _was_recently_scanned(coin: str, max_age_sec: int = D2_SCAN_INTERVAL_SECONDS) -> bool:
    """Phase 16: Lazy TTL eviction on every read."""
    last = _scanned_recently.get(coin)
    if last is None:
        return False
    now = datetime.now(timezone.utc).timestamp()
    # Evict if past TTL
    if now - last > _SCANNED_RECENTLY_TTL_SEC:
        del _scanned_recently[coin]
        return False
    return (now - last) < max_age_sec


# Module-level singleton
ltf_engine = LTFEngine()


def _ensure_ltf_signal(coin: str, sig) -> Any:
    """Normalize stored signal to LTFSignal object.

    Handles both raw dicts (from older code) and LTFSignal objects.
    """
    if isinstance(sig, LTFSignal):
        return sig
    if isinstance(sig, dict):
        return LTFSignal(coin, sig)
    return None
