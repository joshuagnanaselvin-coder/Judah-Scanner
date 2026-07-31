"""Dimension 2 — LTF Engine orchestrator.

Hybrid trigger: 5s safety timer + WS events (new 15M candle OR D1 tier change).
Gates on D1: skips coins where ALL D1 TFs = WATCH.
Reads D1 tiers from state_store, writes LTF signals to state_store.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.config import (
    D2_TIMEFRAME, D2_SCAN_INTERVAL_SECONDS, D2_SKIP_ALL_WATCH,
    TIMEFRAMES_HTF,
)
from backend.engines.ltf_scanner import scan_ltf
from backend.state_store import state_store
from backend.market_data import market_data

logger = logging.getLogger("judah.ltf_engine")


class LTFEngine:
    def __init__(self):
        self.running: bool = False
        self.scan_task = None
        self._pending_coins: set = set()  # Coins needing D2 scan (WS trigger)
        self._last_d1_update: dict = {}   # Track last D1 updated_at per coin for change detection

    async def start(self):
        """Start D2 engine."""
        self.running = True
        self.scan_task = asyncio.create_task(self._scan_loop())
        logger.info("[ltf_engine] Started — 15M entry scanner (hybrid trigger)")

    async def stop(self):
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()

    def on_candle_close(self, symbol: str, timeframe: str):
        """Called by market_data when a new candle closes on WebSocket."""
        if timeframe.upper() == D2_TIMEFRAME:
            self._pending_coins.add(symbol)
            logger.debug(f"[ltf_engine] WS trigger: {symbol} {timeframe} candle closed")

    def on_d1_tier_change(self, coin: str, old_tier: str, new_tier: str):
        """Called by D1 scanner when a coin's tier changes.

        Triggers D2 re-scan if coin was WATCH and is now OPPORTUNITY or better.
        """
        if old_tier == "WATCH" and new_tier in ("OPPORTUNITY", "SNIPER"):
            self._pending_coins.add(coin)
            logger.info(f"[ltf_engine] D1 tier change trigger: {coin} {old_tier} → {new_tier}")

        # Track last D1 update time for change detection in _run_cycle
        self._last_d1_update[coin] = datetime.now(timezone.utc).timestamp()

    async def _scan_loop(self):
        """Hybrid scan loop: 5s timer + WS event drain."""
        while self.running:
            try:
                await self._drain_pending()
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[ltf_engine] Scan error")
            await asyncio.sleep(D2_SCAN_INTERVAL_SECONDS)

    async def _drain_pending(self):
        """Process WS-triggered coins immediately (before timer cycle)."""
        if not self._pending_coins:
            return

        coins = list(self._pending_coins)
        self._pending_coins.clear()

        logger.debug(f"[ltf_engine] Draining {len(coins)} WS-triggered coins")
        for coin in coins:
            await self._scan_coin(coin)

    async def _run_cycle(self):
        """Full cycle: scan all active coins (5s safety net)."""
        # Get active coins from state store (D1 non-WATCH)
        active_coins = state_store.get_active_coins()

        # Check for newly activated coins (D1 updated since last check)
        newly_active = []
        for coin in active_coins:
            d1 = state_store.get_d1_tier(coin)
            if d1:
                last_update = self._last_d1_update.get(coin, 0)
                if d1["updated_at"] > last_update:
                    newly_active.append(coin)
                    self._last_d1_update[coin] = d1["updated_at"]

        # Scan newly active coins immediately
        for coin in newly_active:
            await self._scan_coin(coin)

        # Refresh existing D2 signals
        d2_signals = state_store.get_all_d2_signals()
        for coin, signal in d2_signals.items():
            if coin not in active_coins:
                # Coin dropped out of D1 — remove D2 signal
                logger.debug(f"[ltf_engine] Removing {coin}: no longer active in D1")
                continue
            if signal.is_expired():
                logger.info(f"[ltf_engine] EXPIRED {coin}: TTL exceeded")
                continue
            # Light refresh (re-scan for score update)
            await self._scan_coin(coin)

    async def _scan_coin(self, coin: str) -> bool:
        """Scan a single coin. Returns True if a SNIPER signal was found/updated."""
        # Gate: skip if all D1 TFs are WATCH
        if D2_SKIP_ALL_WATCH and state_store.is_all_watch(coin):
            logger.debug(f"[ltf_engine] SKIP {coin}: all D1 TFs = WATCH")
            return False

        # Check 15M candle availability
        candles = market_data.get_candles(coin, "15M")
        if not candles or len(candles) < 50:
            logger.debug(f"[ltf_engine] SKIP {coin}: insufficient 15M data")
            return False

        # Run LTF scan
        signal = scan_ltf(coin)
        if not signal:
            # Remove stale D2 signal if scan fails
            existing = state_store.get_d2_signal(coin)
            if existing:
                logger.debug(f"[ltf_engine] Removing {coin}: scan returned None")
            return False

        # Link D1 context
        d1_tier = state_store.get_d1_tier_str(coin)
        signal.d1_tier = d1_tier

        # Write to state store
        await state_store.set_d2_signal(coin, signal)

        # Trigger D3 fusion
        from backend.engines.signal_fusion import fuse
        await fuse(coin)

        return True
