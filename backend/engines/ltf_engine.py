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
  - Scoped to D1-approved coins only

Input: all D1 coins (SNIPER, OPPORTUNITY, WATCH — REJECTED are skipped).
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
from backend.engines.ltf_scanner import LTFSignal
from backend.market_data import market_data
from backend.state_store import state_store
from backend.engines.ltf_scanner import scan_entry
from backend.candidate_selector import should_select

logger = logging.getLogger("judah.ltf_engine")


class LTFEngine:
    def __init__(self):
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

        print(f"[ltf] Starting D2 engine — {len(symbols)} coins on 15M")
        self.scan_task = asyncio.create_task(self._scan_loop())
        print(f"[ltf] Live — {len(symbols)} coins x 15M (SNIPER/OPPORTUNITY/WATCH)")

    async def _scan_loop(self):
        """Main scan cycle: timer + WS event drain."""
        while self.running:
            try:
                t0 = _time_module.time()
                await self._run_batch_scan()
                elapsed = _time_module.time() - t0
                logger.info(f"[ltf] Cycle complete in {elapsed:.1f}s")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[ltf] Scan error")
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

        d1_approved = []
        for coin in scan_targets:
            d1 = state_store.get_d1_tier(coin)
            if d1 and d1.get("tier") in ("SNIPER", "OPPORTUNITY", "WATCH"):
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
                raw = scan_entry(coin, d1_tier=d1_tier, d1_score=d1_score)
                if raw:
                    await state_store.set_d2_signal(coin, raw)
                    revalidated.append(raw)
                else:
                    # Setup broken — remove
                    await state_store.set_d2_signal(coin, None)
                continue

            # Light refresh — just update age/price
            refreshed.append(sig)

        # === PASS 2: Scan ALL symbols for 15M entry (D2 is independent of D1) ===
        new_signals = []
        scan_tasks = []

        for coin in scan_targets:
            # Skip if already have a D2 signal
            if state_store.get_d2_signal(coin):
                continue
            # Skip if recently scanned
            if _was_recently_scanned(coin):
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
                    return scan_entry(coin)
                except Exception as e:
                    logger.warning(f"[ltf] Error {coin}: {e}")
                    return None

        results = await asyncio.gather(
            *[_scan_with_limit(c) for c in scan_tasks],
            return_exceptions=True
        )

        for coin, result in zip(scan_tasks, results):
            if isinstance(result, Exception) or not result:
                _mark_scanned(coin)
                continue

            await state_store.set_d2_signal(coin, result)
            new_signals.append(result)
            _mark_scanned(coin)

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

    def stop(self):
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()


# ── Helpers ─────────────────────────────────────────────────────────

_REVALIDATE_AGE_MIN = 8  # Revalidate after 8 minutes (half of 15M TTL)
_scanned_recently: dict = {}


def _age_minutes(sig: LTFSignal) -> float:
    return (datetime.now(timezone.utc) - sig.born_at).total_seconds() / 60


def _should_revalidate(sig: LTFSignal) -> bool:
    return _age_minutes(sig) >= _REVALIDATE_AGE_MIN


def _mark_scanned(coin: str):
    _scanned_recently[coin] = datetime.now(timezone.utc).timestamp()


def _was_recently_scanned(coin: str, max_age_sec: int = D2_SCAN_INTERVAL_SECONDS) -> bool:
    last = _scanned_recently.get(coin, 0)
    return (datetime.now(timezone.utc).timestamp() - last) < max_age_sec


# Module-level singleton
ltf_engine = LTFEngine()
