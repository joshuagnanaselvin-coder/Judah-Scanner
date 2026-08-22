"""Dimension 1 — 4H Scanner Orchestrator.

4H-only deep scan: scans all 500 coins on 4H timeframe.
Triggered by candle close (every 4 hours).
Pipeline: Flow → CRT → SMC → Signal Builder → Score → Tier

D1 is fully independent — no communication with D2.
"""
import asyncio
import logging
import time as _time_module
from datetime import datetime, timezone

from backend.market_data import market_data
from backend.signal_store import signal_store
from backend.engines.engine import scan
from backend.state_store import state_store
from backend.config import (
    SCAN_INTERVAL_SECONDS, TIMEFRAMES_HTF, SIGNAL_TTL_MINUTES,
    SCAN_CONCURRENCY,
)
from backend.performance_tracker import performance_tracker
from backend.decision_snapshot import SnapshotBuilder

logger = logging.getLogger("judah.scanner")


class Scanner:
    """Phase 21: Observability — each scanner instance carries a stable cycle ID."""
    _scanner_count = 0
    _ids: dict[int, str] = {}

    def __init__(self):
        Scanner._scanner_count += 1
        key = id(self)
        if key not in Scanner._ids:
            Scanner._ids[key] = f"D1-{Scanner._scanner_count:04d}"
        self.cycle_id: str = Scanner._ids[key]

        self.symbols: list = []
        self.running: bool = False
        self.scan_task = None
        self._callback = None
        self.on_tier_change = None
        self._prev_tiers: dict[str, str] = {}
        self._scan_semaphore: asyncio.Semaphore | None = None
        self._d3_notify = None  # Wired in main.py after fusion_engine starts

    async def start(self, symbols: list):
        self.symbols = symbols
        self.running = True
        self._scan_semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        # Connect WS for live candle data (D2 also uses it, but D1 scan loop
        # is timer-driven — WS keeps our candle cache fresh)
        market_data.connect_websocket(symbols)

        # Start the scan loop
        self.scan_task = asyncio.create_task(self._scan_loop())
        logger.info(f"[scanner] [{self.cycle_id}] Live - {len(symbols)} coins x 4H "
                    f"(candle-close driven, ~4h cycle)")

        # Bootstrap historical candle data in background
        asyncio.create_task(self._background_bootstrap(symbols))

    async def _background_bootstrap(self, symbols: list):
        """Download historical candle data without blocking the scanner."""
        try:
            logger.info(f"[scanner] [{self.cycle_id}] Background bootstrap starting "
                  f"({len(symbols)} pairs x 4H)...")
            count = await market_data.bootstrap(symbols)
            logger.info(f"[scanner] [{self.cycle_id}] Background bootstrap: {count} candle sets")

            dropped = self._drop_incomplete_candles()
            logger.info(f"[scanner] [{self.cycle_id}] Dropped {dropped} incomplete 4H candles")

            # Run initial full scan now that we have historical data
            logger.info(f"[scanner] [{self.cycle_id}] Initial full scan (post-bootstrap)...")
            await self._run_batch_scan()
            logger.info(f"[scanner] [{self.cycle_id}] Initial scan complete")
        except Exception as e:
            logger.error(f"[scanner] [{self.cycle_id}] Background bootstrap failed: {e}")

    async def _scan_loop(self):
        """D1 4H scan loop — candle-close driven.

        Waits for the next 4H candle to close, then runs a full deep scan
        of all 500 coins on the 4H timeframe. No WS event queue needed —
        we sleep until the next candle close and scan everything then.
        """
        # Wait for bootstrap to complete before first scan
        await self._wait_for_candles()

        while self.running:
            try:
                # Calculate and sleep until next 4H candle close
                sleep_sec = self._seconds_until_next_close("4H")
                logger.info(f"[scan] [{self.cycle_id}] Sleeping {sleep_sec / 3600:.1f}h "
                            f"until next 4H candle close")
                await asyncio.sleep(sleep_sec)

                if not self.running:
                    break

                # Run full deep scan of ALL coins on 4H
                logger.info(f"[scan] [{self.cycle_id}] 4H candle closed — "
                            f"starting full scan of {len(self.symbols)} coins")
                t0 = _time_module.time()
                await self._run_batch_scan()
                elapsed = _time_module.time() - t0
                logger.info(f"[scan] [{self.cycle_id}] 4H cycle complete in {elapsed:.1f}s")

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"[scan] [{self.cycle_id}] Scan error")
                await state_store.set_d1_status("DEGRADED", reason="scan_cycle_failed")
                await asyncio.sleep(60)  # back off on error

    async def _wait_for_candles(self):
        """Wait until we have 4H candle data for at least some coins."""
        max_wait = 120  # seconds
        waited = 0
        while waited < max_wait:
            has_candles = any(
                len(market_data.get_candles(s, "4H") or []) >= 25
                for s in self.symbols[:10]  # check first 10
            )
            if has_candles:
                logger.info(f"[scan] [{self.cycle_id}] 4H candles available — "
                            f"starting initial scan")
                return
            await asyncio.sleep(5)
            waited += 5
        logger.warning(f"[scan] [{self.cycle_id}] Timeout waiting for 4H candles — "
                       f"proceeding anyway")

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

    async def _scan_batch(self, scan_tasks: list, full_cycle: bool = True, snap=None):
        """Run a batch of scans on 4H for all coins.

        Every coin is scanned. Pipeline internal gates (ATR/flow/structure)
        determine if there's a valid signal. All coins get a D1 tier entry.
        """
        if not scan_tasks:
            return

        from backend.config import SCAN_CONCURRENCY
        _scan_sem = self._scan_semaphore or asyncio.Semaphore(SCAN_CONCURRENCY)

        async def _scan_with_limit(symbol, tf):
            async with _scan_sem:
                try:
                    return await scan(symbol, tf)
                except Exception as e:
                    logger.warning(f"[scan] {symbol} {tf}: scan raised {e}")
                    return None

        results = await asyncio.gather(
            *[_scan_with_limit(sym, tf) for sym, tf in scan_tasks],
        )

        new_signals = []
        refreshed = []

        for (symbol, tf), result in zip(scan_tasks, results):
            if isinstance(result, Exception) or not result:
                # Scan failed — store as REJECTED so the coin still appears in D3
                signal = {
                    "symbol": symbol, "engine": tf, "tier": "REJECTED",
                    "composite_score": 0.0, "base_score": 0.0,
                    "direction": "NEUTRAL", "entry": 0, "stop_loss": 0,
                    "take_profit": 0, "rr": 0.0, "confidence": "LOW",
                    "freshness_state": "REJECTED",
                }
                if signal_store.add(signal):
                    new_signals.append(signal)
                continue

            try:
                signal = self._apply_confluence(symbol, result)
                signal = self._apply_boosts(signal, tf)
            except Exception as e:
                logger.warning(f"[confluence] Failed for {symbol} {tf}: {e}")
                signal = result
                signal["tier"] = "REJECTED"
                signal["engine"] = tf
                if signal_store.add(signal):
                    new_signals.append(signal)
                continue

            snap_id = state_store.last_snapshot_id
            from backend.decision_snapshot import _CODE_VERSION, _CONFIG_HASH
            signal["snapshot_id"] = snap_id
            signal["code_version"] = _CODE_VERSION
            signal["config_hash"] = _CONFIG_HASH
            signal["d1_evidence_ids"] = []
            signal["alignment_id"] = ""
            signal["trade_plan_id"] = ""
            signal["risk_decision_id"] = ""
            signal["created_at"] = datetime.now(timezone.utc).isoformat()

            if signal_store.add(signal):
                new_signals.append(signal)

        if full_cycle:
            # === Build D1 tiers for ALL coins (parallel publish) ===
            d1_tiers_this_cycle = {}

            # Collect scan results in one pass
            all_coin_tfs: dict[str, dict] = {}
            signal_tfs: set[str] = set()
            for sig in signal_store.signals.values():
                if sig.get("freshness_state") in ("INVALIDATED", "EXPIRED"):
                    continue
                coin = sig['symbol']
                tf = sig['engine']
                if coin not in all_coin_tfs:
                    all_coin_tfs[coin] = {}
                all_coin_tfs[coin][tf] = {
                    "tier": sig.get('tier', 'WATCH'),
                    "score": sig.get('base_score', sig.get('composite_score', 0)),
                    "direction": sig.get('direction', ''),
                }
                signal_tfs.add(f"{coin}_{tf}")

            # Determine which coins were scanned this cycle
            coins_scanned_this_cycle = {sym for sym, _ in scan_tasks}

            # Build tier writes — collect all, then publish in parallel
            tier_writes = []
            for coin in self.symbols:
                best_tier = "REJECTED"
                best_score = 0
                best_direction = ""
                display_tfs = {}

                if coin in all_coin_tfs:
                    tfs = all_coin_tfs[coin]
                    if tfs:
                        best_tf = max(tfs.items(), key=lambda x: x[1]['score'])
                        best_tier = best_tf[1]['tier']
                        best_score = best_tf[1]['score']
                        best_direction = best_tf[1].get('direction', '')
                        real_tfs = {tf: data for tf, data in tfs.items()
                                    if f"{coin}_{tf}" in signal_tfs}
                        display_tfs = real_tfs

                # If coin wasn't scanned, keep existing tier (don't overwrite with REJECTED)
                if coin not in coins_scanned_this_cycle:
                    existing = state_store.get_d1_tier(coin)
                    if existing and existing.get("tier") != "REJECTED":
                        continue

                tier_writes.append((coin, best_tier, best_score, display_tfs, best_direction))

            # Parallel tier publish — asyncio.gather eliminates 2.5s sequential I/O wait
            await asyncio.gather(*[
                self._publish_tier(
                    coin, tier, score, tfs, direction,
                    prev_tiers=self._prev_tiers,
                    on_tier_change=self.on_tier_change,
                )
                for coin, tier, score, tfs, direction in tier_writes
            ])

            # Set timestamp AFTER all tiers are published
            await state_store.set_timestamp("last_d1_scan")

            # Notify D3 fusion engine — D1 data is ready (event-driven trigger).
            # Wired in main.py after startup to avoid circular imports.
            if self._d3_notify and full_cycle:
                try:
                    self._d3_notify()
                except Exception:
                    pass

            for coin, tier, score, _, _ in tier_writes:
                d1_tiers_this_cycle[coin] = tier

        # Console output
        if new_signals:
            for s in new_signals:
                logger.info(f"[{s['engine']}] {s['symbol']}: {s['tier']} "
                      f"score={s['composite_score']} dir={s['direction']} "
                      f"RR={s['rr']:.1f} session={s['session']}")
        logger.info(f"[scan] {len(new_signals)} new signals, "
                    f"{len(scan_tasks)} coins scanned on 4H"
                    + (f", {len(d1_tiers_this_cycle)} D1 tiers" if full_cycle else ""))

    async def _run_batch_scan(self):
        """Full batch scan cycle — scan ALL 500 coins on 4H, no pre-filters.

        Every coin gets scanned. The pipeline's internal ATR/range/flow gates
        determine if there's a valid signal. All coins get a D1 tier entry
        (SNIPER/OPPORTUNITY/WATCH/WEAK/REJECTED).
        """
        logger.info(f"[scan] [{self.cycle_id}] Full cycle: scanning {len(self.symbols)} coins on 4H")

        # Scan ALL coins — no candidate filter, no pre-selection
        scan_tasks = [(symbol, "4H") for symbol in self.symbols]

        # Build snapshot for data quality
        snap = SnapshotBuilder(market_data).build(self.symbols, TIMEFRAMES_HTF)
        await state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)

        await self._scan_batch(scan_tasks, full_cycle=True, snap=snap)

    def _apply_zscore_normalization(self, all_signals: list) -> list:
        """Normalize composite scores across the live signal universe.

        Blends raw score (70%) with percentile rank (30%) so that
        a score of 60 in a quiet market means the same as 60 in a volatile
        market — both are top ~30% of available setups.

        Uses base_score (pre-decay) for fair comparison.
        """
        if len(all_signals) < 3:
            return all_signals  # too few to normalize

        scores = [s.get("base_score", s.get("composite_score", 0)) for s in all_signals]
        n = len(scores)
        mean = sum(scores) / n
        variance = sum((s - mean) ** 2 for s in scores) / n
        std = variance ** 0.5
        if std < 1e-6:
            return all_signals  # all same score, no normalization needed

        for sig in all_signals:
            raw = sig.get("base_score", sig.get("composite_score", 0))
            z = (raw - mean) / std
            # Map z to 0-100 percentile rank using cumulative normal approximation
            import math
            percentile = 0.5 * (1 + math.erf(z / 1.41421356)) * 100
            # Blend: 70% raw score, 30% percentile rank
            blended = round(0.7 * raw + 0.3 * percentile, 1)
            sig["composite_score"] = min(blended, 100)
            sig["z_score"] = round(z, 2)
            sig["percentile"] = round(percentile, 1)

        return all_signals

    @staticmethod
    async def _publish_tier(coin: str, tier: str, score: float, display_tfs: dict, direction: str, prev_tiers: dict | None = None, on_tier_change=None):
        """Write a single D1 tier to state_store.

        Extracted so it can be called concurrently via asyncio.gather
        for parallel tier publishing.
        """
        # Track tier changes for event callbacks
        if prev_tiers is not None and on_tier_change:
            prev = prev_tiers.get(coin, "WATCH")
            if prev != tier:
                on_tier_change(coin, prev, tier)
                prev_tiers[coin] = tier

        await state_store.set_d1_tier(coin, tier, score, display_tfs, direction)

    def _on_candle_close(self, symbol: str, tf: str):
        """WS callback — enqueue HTF candle close events for batched scanning.

        D1 scanning is event-driven on HTF candle close (1H/4H/1D).
        15M events are ignored — D2 handles them separately.
        """
        if tf not in TIMEFRAMES_HTF:
            return  # LTF candles handled by ltf_engine

        # Defensive guard: skip duplicates within 2 seconds
        key = f"{symbol}_{tf}"
        last = getattr(self, '_ws_trigger_ts', {}).get(key, 0)
        now = _time_module.time()
        if now - last < 2.0:
            return
        ts_map = getattr(self, '_ws_trigger_ts', {})
        ts_map[key] = now
        self._ws_trigger_ts = ts_map

        # Enqueue for the scan loop to process
        if self._scan_events:
            try:
                self._scan_events.put_nowait((symbol, tf))
            except asyncio.QueueFull:
                pass  # queue full — will be caught by fallback cycle


    def _drop_incomplete_candles(self) -> int:
        """Drop the last (forming) candle from each HTF pair after bootstrap.

        Binance returns the currently-forming candle as the last entry in klines.
        For D1 HTF analysis we must use only closed candles — WS will deliver
        the next closed candle when it actually closes.

        Returns count of candles dropped.
        """
        dropped = 0
        for tf in TIMEFRAMES_HTF:
            for symbol in self.symbols:
                key = f"{symbol}_{tf}"
                candles = market_data.candles.get(key)
                if candles and len(candles) >= 2:
                    # Pop the last entry — it's the incomplete candle
                    candles.pop()
                    dropped += 1
        return dropped

    async def _update_d1_tier_for(self, coin: str):
        """Update D1 tier for a single coin after WS-triggered scan.

        CRITICAL: only use TFs that have a signal in signal_store right now.
        Do NOT fabricate REJECTED+0 for missing TFs — that would corrupt
        the best_tf selection and overwrite a real score with 0.
        """
        tfs = {}
        for tf in TIMEFRAMES_HTF:
            sig = signal_store.get(coin, tf)
            if sig:
                tfs[tf] = {
                    "tier": sig.get('tier', 'WATCH'),
                    "score": sig.get('base_score', sig.get('composite_score', 0)),
                    "direction": sig.get('direction', ''),
                }

        if not tfs:
            return

        best_tf = max(tfs.items(), key=lambda x: x[1]['score'])
        best_tier = best_tf[1]['tier']
        best_score = best_tf[1]['score']
        best_direction = best_tf[1].get('direction', '')

        prev_tier = self._prev_tiers.get(coin, "WATCH")
        if prev_tier != best_tier:
            if self.on_tier_change:
                self.on_tier_change(coin, prev_tier, best_tier)
            self._prev_tiers[coin] = best_tier

        # Pass the tfs dict (only populated TFs) — state_store will preserve
        # existing per-TF entries for TFs not in this dict.
        await state_store.set_d1_tier(coin, best_tier, best_score, tfs, best_direction)

    def _apply_confluence(self, symbol, signal):
        """D1 confluence — 4H only, no MTF confluence (single TF).

        Previously counted agreement across 1H/4H/1D. Now only 4H exists,
        so there's no cross-TF confluence to compute. Kept as no-op for
        backward compatibility — the pipeline scoring already captures
        the setup quality.
        """
        return signal

    def _apply_boosts(self, signal, tf):
        """Apply post-pipeline micro-boosts.

        Flow and momentum are already scored in the engine pipeline
        (layers 1 and 4). We only add marginal refinements here that
        the main pipeline doesn't capture: proximity to FVG and
        confluence from other timeframes (already handled separately
        by _apply_confluence).
        """
        reasons = []
        boost = 0

        # FVG proximity at entry — CRT doesn't score this directly
        if signal.get("fvg") and signal["fvg"].get("proximity", 999) <= 1.0:
            reasons.append("FVG at entry")
            boost += 5

        # Fresh OB — SMC doesn't differentiate 0-touch OB
        ob = signal.get("ob")
        if ob and ob.get("touches", 0) == 0:
            reasons.append("Fresh OB")
            boost += 5

        if reasons:
            base = signal.get("composite_score", 0)
            signal["composite_score"] = min(base + boost, 100)
            signal["boost_reasons"] = reasons
            signal["boost_total"] = boost

        return signal

    def _clean_expired(self) -> list:
        now = datetime.now(timezone.utc).timestamp() * 1000
        ttl = SIGNAL_TTL_MINUTES * 60 * 1000
        expired = []
        for key, sig in list(signal_store.signals.items()):
            if (now - sig["timestamp"]) > ttl:
                sig["outcome"] = "TIMEOUT"
                expired.append(sig)
                performance_tracker.record(sig)
                del signal_store.signals[key]
            elif sig.get("freshness_state") == "EXPIRED":
                expired.append(sig)
                performance_tracker.record(sig)
                del signal_store.signals[key]
        return expired

    def on_new_signals(self, callback):
        self._callback = callback

    def stop(self):
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()

    async def restart(self) -> dict:
        """Full restart: clear signals + FVG ledger, re-bootstrap candles, reconnect WS.
        Keeps the same symbol list. Safe to call multiple times."""
        logger.info(f"[restart] Stopping scan loop for {len(self.symbols)} pairs...")

        # 1. Stop the scan loop
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()
            try:
                await self.scan_task
            except (asyncio.CancelledError, Exception):
                pass

        # 2. Wipe in-memory state
        cleared_signals = len(signal_store.signals)
        signal_store.signals.clear()
        signal_store.fvg_ledger.clear()
        signal_store.scanned_recently.clear()

        # Phase 20: Restart/Recovery — also clear StateStore tiers + evidence
        from backend.state_store import state_store
        from backend.evidence_store import evidence_store
        await state_store.clear()
        # Evidence is regenerated each cycle — wipe to prevent stale cross-snapshot evidence
        ev_stats = evidence_store.get_stats()
        if ev_stats["total_records"] > 0:
            logger.info(f"[restart] Evidence store had {ev_stats['total_records']} records — clearing")
            evidence_store._records.clear()
            evidence_store._snapshot_timestamps.clear()

        logger.info(f"[restart] Cleared {cleared_signals} signals + FVG ledger + scan cache + state_store + evidence")

        # 3. Cancel WS connections and close session
        ws_tasks = getattr(market_data, "_ws_tasks", [])
        for t in ws_tasks:
            t.cancel()
        for t in ws_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        market_data._ws_tasks = []
        market_data.ws_connected = False

        # Close aiohttp session so bootstrap() can create a fresh one
        if market_data.session and not market_data.session.closed:
            await market_data.session.close()
            market_data.session = None

        # Wipe candle cache so bootstrap fetches fresh
        market_data.candles.clear()

        # Phase 22: Rehydrate Bayesian calibration from DB (survives restarts)
        try:
            from backend.market_evolution.confidence import _load_bayes_from_db
            await _load_bayes_from_db()
            logger.info("[restart] Bayesian calibration rehydrated from DB")
        except Exception:
            logger.exception("[restart] Bayes rehydration failed")

        # 4. Re-bootstrap (fresh REST pull for every symbol x TF)
        logger.info(f"[restart] Re-bootstrapping {len(self.symbols)} pairs x {len(TIMEFRAMES_HTF)} TFs...")
        count = await market_data.bootstrap(self.symbols)
        logger.info(f"[restart] Bootstrapped {count} candle sets")

        # 5. Reconnect WebSocket
        market_data.connect_websocket(self.symbols)
        market_data.on_candle_close = self._on_candle_close

        # 6. Restart the scan loop
        self.running = True
        self.scan_task = asyncio.create_task(self._scan_loop())

        # Kick off an immediate D1 full scan so last_d1_scan gets set
        scan_task = asyncio.create_task(self._run_batch_scan())
        scan_task.add_done_callback(
            lambda t: logger.error(f"[restart] D1 full scan failed: {t.exception()}")
            if t.exception() else None
        )

        logger.info(f"[restart] Restart complete — {count} candle sets, "
                    f"{len(self.symbols)} pairs live")

        # 7. Notify any connected WebSocket clients
        if self._callback:
            try:
                await self._callback([], [], [], [])
            except Exception as e:
                logger.warning(f"[restart] WS notify failed: {e}")

        return {
            "symbols": len(self.symbols),
            "candle_sets": count,
            "signals_cleared": cleared_signals,
        }

scanner = Scanner()
