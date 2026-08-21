"""Dimension 2 — LTF Scanner Orchestrator (15M timeframe by default).

V5.1: delegates analysis to the shared scanner_engine.
The analyzer itself (Flow → CRT → SMC → Momentum → Score → Tier) lives in
backend.engines.scanner_engine. This module orchestrates the scan loop:

  PASS 1: Revalidate + refresh existing D2 signals
  PASS 2: Candidate filter → concurrent scan for new signals
  PASS 3: Write D2 tiers to state_store

Key differences from D1:
  - LTF timeframes (15M/5M, configurable)
  - Uses LTFSignal objects (not plain dicts)
  - Stores to state_store.d2_signals (not signal_store)
  - Writes state_store timestamp — D3 watches for changes
  - Scans ALL coins regardless of D1 tier (REJECTED D1 → D2 Type B play)

Input: all D1 coins (SNIPER, OPPORTUNITY, WATCH, REJECTED — all flow to D2).
Dimensions work independently during the process.
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

# Phase 21: Observability — per-task cycle ID tracking
_cycle_counter = 0
_cycle_ids: dict[int, str] = {}


def _get_cycle_id() -> str:
    """Return a short cycle ID for the current asyncio task."""
    global _cycle_counter
    try:
        task = asyncio.current_task()
        key = id(task) if task else 0
        if key not in _cycle_ids:
            _cycle_counter += 1
            _cycle_ids[key] = f"D2-{_cycle_counter:04d}"
        return _cycle_ids[key]
    except RuntimeError:
        return "D2-????"


from backend.engines.ltf_scanner import LTFSignal
from backend.market_data import market_data
from backend.state_store import state_store
from backend.engines.ltf_scanner import scan_entry
from backend.candidate_selector import should_select

logger = logging.getLogger("judah.ltf_engine")

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
        self._callback = None
        self._scan_semaphore: asyncio.Semaphore | None = None

    async def start(self, symbols: list):
        """Start D2 engine with the coin list from D1."""
        self.symbols = symbols
        self.running = True
        self._scan_semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        print(f"[ltf] [{self.cycle_id}] Starting D2 engine — {len(symbols)} coins on 15M")
        self.scan_task = asyncio.create_task(self._scan_loop())
        print(f"[ltf] [{self.cycle_id}] Live — {len(symbols)} coins x 15M (SNIPER/OPPORTUNITY/WATCH)")

    async def _scan_loop(self):
        """Main scan cycle: timer + WS event drain."""
        while self.running:
            try:
                t0 = _time_module.time()
                logger.info(f"[ltf] [{self.cycle_id}] Cycle starting")
                await self._run_batch_scan()
                elapsed = _time_module.time() - t0
                logger.info(f"[ltf] [{self.cycle_id}] Cycle complete in {elapsed:.1f}s")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"[ltf] [{self.cycle_id}] Scan error")
            await asyncio.sleep(D2_SCAN_INTERVAL_SECONDS)

    async def _run_batch_scan(self):
        """Production batch scan for D2:

        PASS 1: Revalidate + refresh existing D2 signals
        PASS 2: Scan ALL symbols for 15M entry (independent of D1)
        PASS 3: Write D2 tiers to state_store
        """
        refreshed = []
        revalidated = []

        # Scan ALL symbols — D2 is independent of D1 approval
        scan_targets = self.symbols
        if not scan_targets:
            logger.info("[ltf] No symbols configured, skipping cycle")
            return

        # Build immutable snapshot (D2's primary timeframe is 15M)
        snap = SnapshotBuilder(market_data).build(scan_targets, htf_timeframes=[], ltf_timeframes=["15M"])
        await state_store.set_d2_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)
        logger.info(f"[ltf] Snapshot {snap.snapshot_id[:8]} — "
                    f"{sum(1 for v in snap.data_quality.values() if v == 'VALID')}/{len(snap.data_quality)} pairs VALID")

        d1_approved = []
        for coin in scan_targets:
            d1 = state_store.get_d1_tier(coin)
            if d1 and d1.get("tier") in ("SNIPER", "OPPORTUNITY", "WATCH", "REJECTED"):
                d1_approved.append((coin, d1.get("tier", ""), d1.get("score", 0)))

        logger.info(f"[ltf] Scanning {len(scan_targets)} coins on 15M "
                     f"({len(d1_approved)} with D1 context)")

        # DEBUG: show all D1 tiers received
        for coin in scan_targets:
            d1 = state_store.get_d1_tier(coin)
            if d1:
                logger.debug(f"[ltf] D1→D2: {coin} tier={d1.get('tier')} score={d1.get('score',0):.0f}")

        # === PASS 1: Revalidate + refresh existing D2 signals ===
        # Only revalidate coins that D1 still has active data for
        existing = {c: s for c, s in state_store.get_all_d2_signals().items()
                    if c in [c for c, _, _ in d1_approved]}
        for coin, sig in list(existing.items()):
            candles = market_data.get_candles(coin, "15M")
            if not candles:
                continue

            sig = _ensure_ltf_signal(coin, sig)
            if not sig:
                continue

            if sig.is_expired():
                # TTL expired — remove signal
                logger.info(f"[ltf] EXPIRED {coin}: TTL exceeded")
                await state_store.set_d2_signal(coin, None)
                continue

            # Revalidate at checkpoints (same logic as D1)
            if _should_revalidate(sig):
                logger.info(f"[ltf] [revalidate] {coin} at {_age_minutes(sig):.0f}min...")
                d1_tier = next((t for c, t, _ in d1_approved if c == coin), "")
                d1_score = next((s for c, _, s in d1_approved if c == coin), 0)
                raw = await scan_entry(coin, d1_tier=d1_tier, d1_score=d1_score)
                if raw:
                    # Update in-place — preserves signal_id, no duplicate cards
                    sig.update(raw)
                    sig.d1_tier = d1_tier
                    sig.d1_score = d1_score
                    await state_store.set_d2_signal(coin, sig)
                    revalidated.append(sig)
                else:
                    # Setup broken — remove
                    await state_store.set_d2_signal(coin, None)
                continue

            # Light refresh — just update age/price
            refreshed.append(sig)

        # === PASS 2: Scan for 15M entry (all coins regardless of D1 tier) ===
        new_signals = []
        scan_tasks = []

        for coin in scan_targets:
            # Skip if already have a D2 signal
            if state_store.get_d2_signal(coin):
                continue
            # Skip if recently scanned
            if _was_recently_scanned(coin):
                continue
            # Gate 1: skip stale/invalid/gapped candle data (Snapshot quality)
            if snap.candle_quality(coin, "15M") in ("STALE", "INVALID", "GAPPED"):
                continue

            # Candidate filter: check 15M ATR before heavy pipeline
            if not should_select(coin, "15M"):
                continue

            scan_tasks.append(coin)

        logger.debug(f"[ltf] Batch: {len(scan_tasks)} candidates to scan")

        semaphore = self._scan_semaphore or asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _scan_with_limit(coin):
            async with semaphore:
                try:
                    return await scan_entry(coin)
                except Exception as e:
                    logger.warning(f"[ltf] Error {coin}: {e}")
                    return None

        results = await asyncio.gather(
            *[_scan_with_limit(c) for c in scan_tasks],
        )

        failed_coins = []
        for coin, result in zip(scan_tasks, results):
            if isinstance(result, Exception) or not result:
                failed_coins.append(coin)
                _mark_scanned(coin)
                continue

            # scan_entry returns raw dict — wrap in LTFSignal
            ltf_sig = LTFSignal(coin, result)
            await state_store.set_d2_signal(coin, ltf_sig)
            new_signals.append(ltf_sig)
            _mark_scanned(coin)

        # Phase 11: No Silent Failures — propagate DEGRADED if any scans failed
        if failed_coins:
            await state_store.set_d2_status(
                "DEGRADED",
                reason=f"{len(failed_coins)}_scans_failed: {','.join(failed_coins[:5])}"
            )

        # === PASS 3: Build D2 tiers, update state_store ===
        d2_tiers_this_cycle = {}
        all_d2 = state_store.get_all_d2_signals()
        for coin, sig in all_d2.items():
            if coin not in scan_targets:
                # D1 dropped this coin — clean up stale D2 signal
                await state_store.set_d2_signal(coin, None)
                continue
            if isinstance(sig, LTFSignal):
                d2_tiers_this_cycle[coin] = sig.tier

        await state_store.set_timestamp("last_d2_scan")

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
              f"{len(d2_tiers_this_cycle)} D2 signals, "
              f"{len(scan_tasks)} candidates scanned")

        # Pipeline stage breakdown (one-cycle counts)
        from backend.engines.ltf_pipeline import _log_stage_summary
        _log_stage_summary()

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
        return LTFSignal(coin, sig,
                         d1_tier=sig.get("d1_tier", ""),
                         d1_score=sig.get("d1_score", 0))
    return None
