"""Orchestrator — Dimension 1 HTF scanner. 5s safety timer + WS events.

Writes D1 tiers to shared state store after each scan cycle.
Hooks for D2 engine (on_tier_change, on_candle_close).
"""
import asyncio
import logging
import time as _time_module
from datetime import datetime, timezone
from backend.market_data import market_data
from backend.signal_store import signal_store
from backend.engines.engine import scan
from backend.candidate_selector import should_select
from backend.state_store import state_store
from backend.config import SCAN_INTERVAL_SECONDS, TIMEFRAMES_HTF, SIGNAL_TTL_MINUTES
from backend.performance_tracker import performance_tracker
from backend.signal_logger import log_signal

logger = logging.getLogger("judah.scanner")

class Scanner:
    def __init__(self):
        self.symbols: list = []
        self.running: bool = False
        self.scan_task = None
        self._callback = None
        # Hooks for D2 engine
        self.on_tier_change = None
        self.on_candle_close = None
        # Track previous D1 tiers for change detection
        self._prev_tiers: dict[str, str] = {}

    async def start(self, symbols: list):
        self.symbols = symbols
        self.running = True

        print(f"[scanner] Bootstrapping {len(symbols)} coins...")
        count = await market_data.bootstrap(symbols)
        print(f"[scanner] Bootstrapped {count} candle sets")

        market_data.connect_websocket(symbols)
        market_data.on_candle_close = self._on_candle_close

        self.scan_task = asyncio.create_task(self._scan_loop())
        print(f"[scanner] Live - {len(symbols)} coins x {len(TIMEFRAMES_HTF)} TFs")

    async def _scan_loop(self):
        while self.running:
            try:
                await self._run_fallback_scan()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[scanner] Scan error")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    async def _run_fallback_scan(self):
        refreshed = []
        revalidated = []

        for key, sig in list(signal_store.signals.items()):
            candles = market_data.get_candles(sig['symbol'], sig['engine'])
            if candles:
                if signal_store.should_revalidate(sig):
                    logger.info(f"[revalidate] Checking {sig['symbol']} {sig['engine']} "
                                f"at {sig.get('age_minutes', 0)}min checkpoint...")
                    new_sig = scan(sig['symbol'], sig['engine'])
                    if new_sig:
                        new_sig = self._apply_confluence(sig['symbol'], new_sig)
                        new_sig = self._apply_boosts(new_sig, sig['engine'])
                    updated = signal_store.revalidate(sig, new_sig)
                    revalidated.append(updated)
                    if updated.get("freshness_state") == "hot":
                        log_signal(updated, action='revalidated')
                    continue

                updated = signal_store.refresh(sig, candles[-1].close)
                refreshed.append(updated)

        # PASS 2: Scan for new signals (only candidates)
        new_signals = []
        d1_tiers_this_cycle: dict[str, dict] = {}

        for symbol in self.symbols:
            for tf in TIMEFRAMES_HTF:
                if signal_store.was_recently_scanned(symbol, tf, max_age_sec=30):
                    continue

                # Universal candidate selection — uses TF-specific candles
                if not should_select(symbol, tf):
                    continue

                signal = scan(symbol, tf)
                if signal:
                    try:
                        signal = self._apply_confluence(symbol, signal)
                        signal = self._apply_boosts(signal, tf)
                    except Exception as e:
                        logger.warning(f"[confluence] Failed for {symbol} {tf}: {e}")
                    if signal_store.add(signal):
                        new_signals.append(signal)
                        log_signal(signal, action='new')

                signal_store.mark_scanned(symbol, tf)

        # Build D1 tiers per coin (best across all TFs) and write to state store
        coin_tf_map: dict[str, dict] = {}
        for sig in signal_store.signals.values():
            coin = sig['symbol']
            if coin not in coin_tf_map:
                coin_tf_map[coin] = {}
            coin_tf_map[coin][sig['engine']] = {
                "tier": sig.get('tier', 'WATCH'),
                "score": sig.get('composite_score', 0),
            }

        for coin, tfs in coin_tf_map.items():
            best_tf = max(tfs.items(), key=lambda x: x[1]['score'])
            best_tier = best_tf[1]['tier']
            best_score = best_tf[1]['score']

            # Detect tier change and notify D2
            prev_tier = self._prev_tiers.get(coin, "WATCH")
            if prev_tier != best_tier:
                if self.on_tier_change:
                    self.on_tier_change(coin, prev_tier, best_tier)
                self._prev_tiers[coin] = best_tier

            # Write to state store (overwrite each cycle)
            await state_store.set_d1_tier(coin, best_tier, best_score, tfs)
            d1_tiers_this_cycle[coin] = best_tier

        await state_store.set_timestamp("last_d1_scan")

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
              f"{len(revalidated)} revalidated, {len(d1_tiers_this_cycle)} D1 tiers updated")

        if self._callback:
            all_signals = signal_store.get_all()
            await self._callback(new_signals, all_signals, refreshed, revalidated)
            await self._callback([], all_signals, [], [])

    def _on_candle_close(self, symbol: str, tf: str):
        """WS callback — offload blocking scan() to avoid blocking the WS read loop."""
        # Defensive guard: skip duplicates within 2 seconds
        key = f"{symbol}_{tf}"
        last = getattr(self, '_ws_trigger_ts', {}).get(key, 0)
        now = _time_module.time()
        if now - last < 2.0:
            return
        ts_map = getattr(self, '_ws_trigger_ts', {})
        ts_map[key] = now
        self._ws_trigger_ts = ts_map

        # Offload blocking work — WS callback must return immediately
        asyncio.get_running_loop().create_task(
            self._ws_scan_task(symbol, tf)
        )

        signal_store.mark_scanned(symbol, tf)

    async def _ws_scan_task(self, symbol: str, tf: str):
        """Async wrapper — runs scan() without blocking the WS read loop."""
        try:
            signal = scan(symbol, tf)
        except Exception as e:
            logger.warning(f"[ws_scan] {symbol} {tf}: scan raised {e}")
            return

        if not signal:
            return

        try:
            signal = self._apply_confluence(symbol, signal)
            signal = self._apply_boosts(signal, tf)
        except Exception as e:
            logger.warning(f"[ws_scan] {symbol} {tf}: boost/confluence failed {e}")

        if signal_store.add(signal):
            print(f"[{signal['engine']}] {signal['symbol']}: {signal['tier']} "
                  f"score={signal['composite_score']} dir={signal['direction']} "
                  f"RR={signal['rr']:.1f}")
            log_signal(signal, action='new')
            logger.info(f"[OUT] {symbol} {tf}: "
                        f"composite={signal.get('composite_score')} "
                        f"tier={signal.get('tier', '?')} dir={signal.get('direction')}")

            if self._callback:
                all_sigs = signal_store.get_all()
                try:
                    await self._callback([signal], all_sigs, [], [])
                except Exception as e:
                    logger.warning(f"[ws_scan] callback error: {e}")

        # Update D1 tier in state store after WS-triggered scan
        try:
            await self._update_d1_tier_for(symbol)
        except Exception as e:
            logger.warning(f"[ws_scan] tier update error: {e}")

    async def _update_d1_tier_for(self, coin: str):
        """Update D1 tier for a single coin (called after WS candle close)."""
        tfs = {}
        for tf in TIMEFRAMES_HTF:
            sig = signal_store.get(coin, tf)
            if sig:
                tfs[tf] = {
                    "tier": sig.get('tier', 'WATCH'),
                    "score": sig.get('composite_score', 0),
                }

        if not tfs:
            return

        best_tf = max(tfs.items(), key=lambda x: x[1]['score'])
        best_tier = best_tf[1]['tier']
        best_score = best_tf[1]['score']

        prev_tier = self._prev_tiers.get(coin, "WATCH")
        if prev_tier != best_tier:
            if self.on_tier_change:
                self.on_tier_change(coin, prev_tier, best_tier)
            self._prev_tiers[coin] = best_tier

        await state_store.set_d1_tier(coin, best_tier, best_score, tfs)

    def _apply_confluence(self, symbol, signal):
        other_tfs = [tf for tf in TIMEFRAMES_HTF if tf != signal["engine"]]
        agreeing = []
        for otf in other_tfs:
            existing = signal_store.get(symbol, otf)
            if existing and existing["direction"] == signal["direction"]:
                agreeing.append(otf)

        if agreeing:
            signal["confluence"] = agreeing
            signal["confluence_boost"] = signal.get("confluence_boost", 0) + 10
            # Apply confluence boost to composite_score so MTF agreement lifts score above the 60+40 ceiling.
            base = signal.get("composite_score", 0)
            if base < 100:
                signal["composite_score"] = min(base + 10, 100)

        return signal

    def _apply_boosts(self, signal, tf):
        reasons = []
        boost = 0

        if signal.get("fvg") and signal["fvg"]["proximity"] <= 1.0:
            reasons.append("FVG at entry")
            boost += 5

        ob = signal.get("ob")
        if ob and ob.get("touches", 0) == 0:
            reasons.append("Fresh OB")
            boost += 5

        # Flow boost — if flow_analyzer returned conviction data
        flow = signal.get("flow", {})
        if flow:
            if flow.get("vwap_side") == signal.get("direction"):
                reasons.append("VWAP aligned")
                boost += 5
            if flow.get("sweep"):
                reasons.append("Liquidity sweep")
                boost += 3
            if flow.get("killzone"):
                reasons.append("Killzone")

        # Momentum boost — if a strong move preceded this signal
        if signal.get("displacement_pct", 0) > 1.0:
            reasons.append("Displacement")
            boost += 5

        if reasons:
            base = signal.get("composite_score", 0)
            # Cap total boost at +20 to keep max score at 90 per scoring architecture
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
                log_signal(sig, action='expired')
                del signal_store.signals[key]
            elif sig.get("freshness_state") == "EXPIRED":
                expired.append(sig)
                performance_tracker.record(sig)
                log_signal(sig, action='expired')
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
        logger.info(f"[restart] Cleared {cleared_signals} signals + FVG ledger + scan cache")

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
