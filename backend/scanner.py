"""Orchestrator — Dimension 1 HTF scanner. 15s batch cycle + WS events.

Batch scanning: candidate filter first, then concurrent CRT+SMC pipeline
with bounded concurrency (20 parallel scans). Only active coins enter the
heavy pipeline — ~80% of pairs are filtered out by the adaptive ATR gate.

WS events provide immediate re-scan on candle close for the specific coin.
"""
import asyncio
import logging
import time as _time_module
from datetime import datetime, timezone

# Phase 21: Observability — cycle ID for D1
_D1_cycle_count = 0
_D1_cycle_ids: dict[int, str] = {}


def _get_d1_cycle_id() -> str:
    global _D1_cycle_count
    try:
        task = asyncio.current_task()
        key = id(task) if task else 0
        if key not in _D1_cycle_ids:
            _D1_cycle_count += 1
            _D1_cycle_ids[key] = f"D1-{_D1_cycle_count:04d}"
        return _D1_cycle_ids[key]
    except RuntimeError:
        return "D1-????"


from backend.market_data import market_data
from backend.signal_store import signal_store
from backend.engines.engine import scan
from backend.candidate_selector import should_select, get_candidates
from backend.state_store import state_store
from backend.config import (
    SCAN_INTERVAL_SECONDS, TIMEFRAMES_HTF, SIGNAL_TTL_MINUTES,
    SCAN_CONCURRENCY,
    TIMEFRAMES_LTF,
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
        self.on_candle_close = None
        self._prev_tiers: dict[str, str] = {}
        self._scan_semaphore: asyncio.Semaphore | None = None

        # Event-driven scan queue: HTF candle close events push (symbol, tf) here
        self._scan_events: asyncio.Queue = None
        # Fallback timer: full revalidate+tier cycle every N seconds
        self._fallback_cycle_seconds = 300  # 5 min safety net

    async def start(self, symbols: list):
        self.symbols = symbols
        self.running = True
        self._scan_semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
        self._scan_events = asyncio.Queue()

        # Connect WS immediately so we start receiving live candle data
        market_data.connect_websocket(symbols)
        market_data.on_candle_close = self._on_candle_close

        # Start the scan loop immediately — it will run WS-triggered scans
        # as soon as candle data is available (from WS or bootstrap).
        self.scan_task = asyncio.create_task(self._scan_loop())
        print(f"[scanner] [{self.cycle_id}] Live - {len(symbols)} coins x {len(TIMEFRAMES_HTF)} TFs")
        print(f"[scanner] [{self.cycle_id}] D1 scanning: event-driven on HTF candle close "
              f"({', '.join(TIMEFRAMES_HTF)}) + {self._fallback_cycle_seconds}s fallback")

        # Bootstrap historical candle data in background (does NOT block scanning).
        # 2116 REST requests for 529 pairs × 4 TFs — takes ~100s.
        # Scan loop handles scans as data arrives; bootstrap fills in the rest.
        asyncio.create_task(self._background_bootstrap(symbols))

    async def _background_bootstrap(self, symbols: list):
        """Download historical candle data without blocking the scanner.

        Runs after the scanner is already live. Scan loop will process
        whatever data is available; this fills in the rest.
        """
        try:
            print(f"[scanner] [{self.cycle_id}] Background bootstrap starting "
                  f"({len(symbols)} pairs x {len(TIMEFRAMES_HTF)} HTF + {len(TIMEFRAMES_LTF)} LTF)...")
            count = await market_data.bootstrap(symbols)
            print(f"[scanner] [{self.cycle_id}] Background bootstrap: {count} candle sets")

            dropped = self._drop_incomplete_candles()
            print(f"[scanner] [{self.cycle_id}] Dropped {dropped} incomplete HTF candles")

            # Run initial full scan now that we have historical data
            print(f"[scanner] [{self.cycle_id}] Initial full scan (post-bootstrap)...")
            await self._run_batch_scan()
            print(f"[scanner] [{self.cycle_id}] Initial scan complete")
        except Exception as e:
            logger.error(f"[scanner] [{self.cycle_id}] Background bootstrap failed: {e}")

    async def _scan_loop(self):
        """Event-driven scan loop.

        Two triggers:
          1. WS candle close → enqueues (symbol, tf) → batched scan
          2. Fallback timer → full revalidate+tier cycle every N seconds
        """
        last_fallback = _time_module.time()
        # Per-symbol+tf cooldown so we don't scan the same coin multiple times
        # while its WS events arrive rapidly.
        _scan_cooldown: dict[str, float] = {}
        COOLDOWN_SEC = 30  # skip re-scans within 30s of last scan

        while self.running:
            try:
                # Wait for WS event (timeout = 2s so we can drain queue + check fallback)
                try:
                    event = await asyncio.wait_for(
                        self._scan_events.get(), timeout=2.0
                    )
                except asyncio.TimeoutError:
                    event = None

                # Batch: drain all pending events (handles burst of candle closes)
                events = [event] if event else []
                while not self._scan_events.empty():
                    try:
                        events.append(self._scan_events.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                # De-dup + filter HTF + cooldown
                seen: set[str] = set()
                scan_tasks = []
                now = _time_module.time()
                for symbol, tf in events:
                    key = f"{symbol}_{tf}"
                    if key in seen:
                        continue
                    # Only scan HTF timeframes here (1H/4H/1D)
                    if tf not in TIMEFRAMES_HTF:
                        continue
                    if now - _scan_cooldown.get(key, 0) < COOLDOWN_SEC:
                        continue
                    seen.add(key)
                    scan_tasks.append((symbol, tf))
                    _scan_cooldown[key] = now

                if scan_tasks:
                    logger.info(f"[scan] [{self.cycle_id}] WS-triggered: "
                                f"{len(scan_tasks)} HTF candle close events")
                    await self._scan_batch(scan_tasks, full_cycle=False)
                    # Update D1 tiers for coins that were just scanned
                    for symbol, tf in scan_tasks:
                        await self._update_d1_tier_for(symbol)

                # Fallback: full cycle (revalidate + new scan + tier build)
                if now - last_fallback >= self._fallback_cycle_seconds:
                    last_fallback = now
                    logger.info(f"[scan] [{self.cycle_id}] Fallback full cycle")
                    await self._run_batch_scan()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(f"[scan] [{self.cycle_id}] Scan error")
                state_store.set_d1_status("DEGRADED", reason="scan_cycle_failed")

    async def _scan_batch(self, scan_tasks: list, full_cycle: bool = True):
        """Run a batch of scans (from WS events).

        Args:
            scan_tasks: list of (symbol, tf) to scan
            full_cycle: if True, also revalidate existing signals and build tiers
        """
        refreshed = []
        revalidated = []

        if full_cycle:
            # === PASS 1: Revalidate + refresh existing signals ===
            snap = SnapshotBuilder(market_data).build(self.symbols, TIMEFRAMES_HTF)
            state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)

            for key, sig in list(signal_store.signals.items()):
                quality = snap.candle_quality(sig['symbol'], sig['engine'])
                if quality in ("STALE", "INVALID", "GAPPED"):
                    continue

                candles = snap.get_candles(sig['symbol'], sig['engine'])
                if not candles:
                    candles = market_data.get_candles(sig['symbol'], sig['engine'])
                if candles:
                    if signal_store.should_revalidate(sig):
                        new_sig = await scan(sig['symbol'], sig['engine'])
                        if new_sig:
                            new_sig = self._apply_confluence(sig['symbol'], new_sig)
                            new_sig = self._apply_boosts(new_sig, sig['engine'])
                        updated = signal_store.revalidate(sig, new_sig)
                        # If revalidation invalidated the signal, remove it from the
                        # store so PASS 3 below doesn't pick up its score=0 and
                        # overwrite the coin's D1 tier with REJECTED.
                        if updated.get("freshness_state") == "INVALIDATED":
                            signal_store.remove(sig['symbol'], sig['engine'])
                        revalidated.append(updated)
                        continue

                    updated = signal_store.refresh(sig, candles[-1].close)
                    refreshed.append(updated)

        # === Scan the new batch ===
        new_signals = []
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

        for (symbol, tf), result in zip(scan_tasks, results):
            if isinstance(result, Exception) or not result:
                signal_store.mark_scanned(symbol, tf)
                continue

            try:
                signal = self._apply_confluence(symbol, result)
                signal = self._apply_boosts(signal, tf)
            except Exception as e:
                logger.warning(f"[confluence] Failed for {symbol} {tf}: {e}")
                signal_store.mark_scanned(symbol, tf)
                continue

            tier = signal.get("tier", "")
            if tier in ("WEAK", "REJECTED"):
                signal_store.mark_scanned(symbol, tf)
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

        if not full_cycle:
            # WS-triggered scans: update D1 tiers per coin so D3 always has
            # fresh data without waiting for the next full cycle.
            for (symbol, tf) in scan_tasks:
                await self._update_d1_tier_for(symbol)

        if full_cycle:
            # === PASS 3: Build D1 tiers per coin ===
            # Build D1 tiers from scan results + ensure ALL coins have an entry.
            # D3 iterates over ALL D2 coins (529 pairs); a missing D1 tier entry
            # shows as d1_score=0 in the frontend. Non-candidates get REJECTED.
            d1_tiers_this_cycle = {}
            all_coin_tfs: dict[str, dict] = {}

            # Collect scan results — use base_score (actual setup quality)
            # instead of composite_score (time-decayed display score).
            # composite_score decays via refresh() and can hit 0 on invalidation,
            # but base_score always reflects the last confirmed setup quality.
            for sig in signal_store.signals.values():
                # Skip invalidated/expired signals — they have score=0 and would
                # pollute D1 tiers with REJECTED+0 entries.
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

            # Fill REJECTED for coins that were scanned but produced no signal,
            # AND for all non-candidate coins so D3 always has a D1 tier entry.
            for symbol in self.symbols:
                if symbol not in all_coin_tfs:
                    all_coin_tfs[symbol] = {}
                for tf in TIMEFRAMES_HTF:
                    if tf not in all_coin_tfs[symbol]:
                        all_coin_tfs[symbol][tf] = {
                            "tier": "REJECTED",
                            "score": 0,
                            "direction": "",
                        }

            # Build per-coin best tier.
            #
            # CRITICAL INVARIANT: only store per-TF entries for TFs that have
            # an actual signal in signal_store. The filler REJECTED+0 entries
            # below are for state_store tier tracking (D3 needs a tier for
            # every coin), but they MUST NOT pollute the timeframes dict
            # because the frontend TF chips render from that dict and would
            # show phantom REJECTED+0 scores for TFs that were never scanned.
            coins_scanned_this_cycle = set()
            for sym, tf in scan_tasks:
                coins_scanned_this_cycle.add(sym)

            # Track which specific (coin, tf) pairs produced a signal.
            # Only these get included in the per-coin timeframes dict.
            signal_tfs: set[str] = set()
            for sig in signal_store.signals.values():
                if sig.get("freshness_state") in ("INVALIDATED", "EXPIRED"):
                    continue
                signal_tfs.add(f"{sig['symbol']}_{sig['engine']}")

            for coin, tfs in all_coin_tfs.items():
                best_tf = max(tfs.items(), key=lambda x: x[1]['score'])
                best_tier = best_tf[1]['tier']
                best_score = best_tf[1]['score']
                best_direction = best_tf[1].get('direction', '')

                # Only overwrite tier if this coin was scanned this cycle.
                # For unscanned coins, keep existing tier from state_store.
                if coin not in coins_scanned_this_cycle:
                    existing = state_store.get_d1_tier(coin)
                    if existing and existing.get("tier") != "REJECTED":
                        continue

                prev_tier = self._prev_tiers.get(coin, "WATCH")
                if prev_tier != best_tier:
                    if self.on_tier_change:
                        self.on_tier_change(coin, prev_tier, best_tier)
                    self._prev_tiers[coin] = best_tier

                # Pass only TFs with real signals to state_store — this keeps
                # the frontend TF chips clean (no phantom REJECTED+0 entries).
                real_tfs = {tf: data for tf, data in tfs.items()
                            if f"{coin}_{tf}" in signal_tfs}
                # If no TF has a signal, pass empty dict (frontend shows no TF chips).
                display_tfs = real_tfs if real_tfs else {}

                await state_store.set_d1_tier(coin, best_tier, best_score, display_tfs, best_direction)
                d1_tiers_this_cycle[coin] = best_tier

            await state_store.set_timestamp("last_d1_scan")

        # Console output
        if new_signals:
            for s in new_signals:
                print(f"[{s['engine']}] {s['symbol']}: {s['tier']} "
                      f"score={s['composite_score']} dir={s['direction']} "
                      f"RR={s['rr']:.1f} session={s['session']}")
        if revalidated:
            for s in revalidated:
                state = s.get('freshness_state', '?')
                score = s.get('composite_score', 0)
                print(f"[reval] {s['symbol']} {s['engine']}: {state} score={score}")
        print(f"[scan] {len(new_signals)} new, {len(refreshed)} refreshed, "
              f"{len(revalidated)} revalidated, "
              f"{len(scan_tasks)} HTF candle events scanned"
              + (f", {len(d1_tiers_this_cycle)} D1 tiers" if full_cycle else ""))

        # Push to frontend
        if self._callback:
            all_signals = signal_store.get_all()
            self._apply_zscore_normalization(all_signals)
            try:
                await self._callback(new_signals, all_signals, refreshed, revalidated)
            except Exception as e:
                logger.warning(f"[scan] callback error: {e}")

    async def _run_batch_scan(self):
        """Full batch scan cycle — revalidate, scan candidates, build tiers for ALL coins.

        Called on startup and as a fallback every _fallback_cycle_seconds.
        Scans ATR candidates (efficient) but writes REJECTED tier entries for
        all non-candidate coins so D1 tiers exist for every coin D3 may reference.
        """
        snap = SnapshotBuilder(market_data).build(self.symbols, TIMEFRAMES_HTF)
        state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)
        logger.info(f"[scan] [{self.cycle_id}] Full cycle: snapshot {snap.snapshot_id[:8]}")

        # Build candidate list (coins that pass the ATR/movement filter)
        scan_tasks = []
        scanned_pairs: set[str] = set()   # (coin, tf) pairs actually scanned
        for tf in TIMEFRAMES_HTF:
            candidates = get_candidates(self.symbols, tf)
            for symbol in candidates:
                quality = snap.candle_quality(symbol, tf)
                if quality in ("STALE", "INVALID", "GAPPED"):
                    continue
                scan_tasks.append((symbol, tf))
                scanned_pairs.add(f"{symbol}_{tf}")

        logger.info(f"[scan] [{self.cycle_id}] Full cycle: {len(scan_tasks)} pairs")
        await self._scan_batch(scan_tasks, full_cycle=True)

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
        """Institutional MTF Confluence Engine.

        Instead of a flat +10 boost for ANY agreement, now counts ALL agreeing TFs
        and applies graduated boost. Direction conflict across TFs reduces score.

        Hedge fund logic: the more independent timeframes that confirm the same
        direction, the higher the conviction. A signal where 1H/4H/1D all agree
        BULLISH is significantly more reliable than just 1H + 4H agreeing.
        """
        other_tfs = [tf for tf in TIMEFRAMES_HTF if tf != signal["engine"]]
        agreeing = []
        opposing = []
        for otf in other_tfs:
            existing = signal_store.get(symbol, otf)
            if not existing:
                continue
            if existing["direction"] == signal["direction"]:
                agreeing.append(otf)
            elif existing["direction"] and existing["direction"] != signal["direction"]:
                opposing.append(otf)

        if agreeing:
            signal["confluence"] = agreeing
            # Graduated boost: 1 agreeing TF = +5, 2 = +10, 3 = +15
            boost = min(len(agreeing) * 5, 15)
            signal["confluence_boost"] = signal.get("confluence_boost", 0) + boost
            base = signal.get("composite_score", 0)
            signal["composite_score"] = min(base + boost, 100)
            signal["mtf_confluence"] = {
                "agreeing": agreeing,
                "opposing": opposing,
                "boost": boost,
                "level": f"{1 + len(agreeing)}/3 TF agreement",
            }

        # Conflict penalty: if another HTF strongly opposes, reduce score
        if opposing and not agreeing:
            penalty = min(len(opposing) * 3, 6)
            current = signal.get("composite_score", 0)
            signal["composite_score"] = max(current - penalty, 0)
            signal["mtf_conflict"] = {
                "opposing": opposing,
                "penalty": penalty,
            }

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
        await state_store.clear(preserve_snapshot_id=False)
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
        print(f"[restart] Re-bootstrapping {len(self.symbols)} pairs x {len(TIMEFRAMES_HTF)} TFs...")
        count = await market_data.bootstrap(self.symbols)
        print(f"[restart] Bootstrapped {count} candle sets")

        # 5. Reconnect WebSocket
        market_data.connect_websocket(self.symbols)
        market_data.on_candle_close = self._on_candle_close

        # 6. Restart the scan loop
        self.running = True
        self.scan_task = asyncio.create_task(self._scan_loop())

        # Kick off an immediate D1 full scan so last_d1_scan gets set
        asyncio.create_task(self._run_batch_scan())

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
