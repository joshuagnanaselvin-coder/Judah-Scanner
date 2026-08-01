"""Dimension 2 — LTF Scanner Orchestrator (15M timeframe).

Exact copy of D1's scanner.py pattern, adapted for 15M:

  PASS 1: Revalidate + refresh existing D2 signals
  PASS 2: Candidate filter → concurrent scan for new signals
  PASS 3: Write D2 tiers to state_store

Key differences from D1:
  - 15M timeframe only (not 1H/4H/1D)
  - Uses LTFSignal objects (not plain dicts)
  - Stores to state_store.d2_signals (not signal_store)
  - Writes state_store timestamp — D3 watches for changes
  - NO D1 influence on score — completely independent 4-layer scoring

Input: all D1 coins (SNIPER, OPPORTUNITY, WATCH — REJECTED are skipped).
Dimensions work independently during the process.
"""
import asyncio
import logging
import time as _time_module
from datetime import datetime, timezone
from typing import Optional
from backend.market_data import market_data
from backend.engines.ltf_scanner import scan_ltf, LTFSignal
from backend.candidate_selector import should_select
from backend.state_store import state_store
from backend.config import (
    D2_SCAN_INTERVAL_SECONDS, D2_SIGNAL_TTL_MINUTES,
    SCAN_CONCURRENCY,
)

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
        PASS 2: Candidate filter → concurrent scan for new signals
        PASS 3: Write D2 tiers to state_store
        """
        refreshed = []
        revalidated = []

        # === PASS 1: Revalidate + refresh existing D2 signals ===
        existing = state_store.get_all_d2_signals()
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
                raw = scan_ltf(coin)
                if raw:
                    await state_store.set_d2_signal(coin, raw)
                    revalidated.append(raw)
                else:
                    # Setup broken — remove
                    await state_store.set_d2_signal(coin, None)
                continue

            # Light refresh — just update age/price
            refreshed.append(sig)

        # === PASS 2: Batch scan for new signals ===
        new_signals = []
        scan_tasks = []

        for coin in self.symbols:
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
                    return scan_ltf(coin)
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
