"""Data Layer — quality enforcement between scanners and D3 fusion.

CRITICAL: Data quality is the #1 priority. All 500 coins flow through,
but data_layer enforces freshness, validates signal integrity, and
cleans stale/orphaned data before D3 sees it.

Ownership:
  D1 writes → state_store.d1_tiers
  D2 writes → state_store.d2_signals
  D3 reads  → data_layer.get_fusion_payload()

No cross-reading between D1 and D2.
Freshness is the main character of each layer.
"""
import asyncio
import logging
from datetime import datetime, timezone

from backend.state_store import state_store
from backend.config import SIGNAL_TTL_MINUTES, D2_SIGNAL_TTL_MINUTES

logger = logging.getLogger("judah.data_layer")


class DataLayer:
    """Quality enforcement layer between scanners and D3 fusion.

    Responsibilities:
    1. TTL-based expiry (D1: 4H, D2: 15M)
    2. Freshness scoring (HOT/WARM/COOL/STALE)
    3. Signal integrity (no broken signals pass through)
    4. Orphan cleanup (D2 signals for removed coins, D3 for expired D2)
    5. D1/D2 change tracking (for D3 trigger efficiency)
    """

    def __init__(self):
        self._last_d1_ts: float = 0.0
        self._last_d2_ts: float = 0.0

    async def get_fusion_payload(self) -> dict:
        """Return clean D1 + D2 data for D3 fusion.

        Applies ALL quality checks:
        - TTL expiry
        - Freshness scoring
        - Signal integrity (no zero entry/SL/TP, impossible scores)
        - Orphan detection
        """
        d1_tiers = self._get_valid_d1_tiers()
        d2_signals = self._get_valid_d2_signals()

        # Track changes for D3 triggering
        current_d1_ts = state_store.last_d1_scan
        current_d2_ts = state_store.last_d2_scan

        return {
            "d1_tiers": d1_tiers,
            "d2_signals": d2_signals,
            "d1_changed": current_d1_ts != self._last_d1_ts,
            "d2_changed": current_d2_ts != self._last_d2_ts,
            "d1_coin_count": len(d1_tiers),
            "d2_coin_count": len(d2_signals),
        }

    def mark_consumed(self):
        """Call after D3 processes the payload to reset change detection."""
        self._last_d1_ts = state_store.last_d1_scan
        self._last_d2_ts = state_store.last_d2_scan

    def _get_valid_d1_tiers(self) -> dict:
        """Return D1 tiers that pass ALL quality checks."""
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - (SIGNAL_TTL_MINUTES * 60)
        tiers = {}
        stale_count = 0
        broken_count = 0
        for coin, entry in state_store.d1_tiers.items():
            updated_at = entry.get("updated_at", 0)

            # TTL check
            if updated_at < cutoff:
                stale_count += 1
                logger.debug(f"[data_layer] Stale D1: {coin} "
                             f"(age={(now - updated_at) / 60:.0f}min)")
                continue

            # Signal integrity check
            if self._is_broken_signal(entry, coin):
                broken_count += 1
                continue

            # Freshness scoring
            age_min = (now - updated_at) / 60
            entry["_freshness"] = self._freshness_label(age_min, SIGNAL_TTL_MINUTES)

            tiers[coin] = entry

        if stale_count or broken_count:
            logger.debug(f"[data_layer] D1 filtered: {stale_count} stale, "
                         f"{broken_count} broken, {len(tiers)} valid")
        return tiers

    def _get_valid_d2_signals(self) -> dict:
        """Return D2 signals that pass ALL quality checks.

        D2 TTL: 15M (matches candle duration). This ensures only
        the latest 15M signals are present — stale D2 signals are dropped.
        """
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - (D2_SIGNAL_TTL_MINUTES * 60)
        signals = {}
        stale_count = 0
        broken_count = 0

        for coin, sig in state_store.d2_signals.items():
            born_at = getattr(sig, 'born_at', None)
            if born_at is None:
                continue

            born_ts = born_at.timestamp() if hasattr(born_at, 'timestamp') else float(born_at)

            # TTL check — D2 signals expire after 15M
            if born_ts < cutoff:
                stale_count += 1
                logger.debug(f"[data_layer] Stale D2: {coin} "
                             f"(age={(now - born_ts) / 60:.0f}min)")
                continue

            # Signal integrity check
            if self._is_broken_signal(sig.to_dict() if hasattr(sig, 'to_dict') else sig, coin):
                broken_count += 1
                continue

            # Freshness scoring
            age_min = (now - born_ts) / 60
            sig_data = sig.to_dict() if hasattr(sig, 'to_dict') else sig
            sig_data["_freshness"] = self._freshness_label(age_min, D2_SIGNAL_TTL_MINUTES)
            signals[coin] = sig

        if stale_count or broken_count:
            logger.debug(f"[data_layer] D2 filtered: {stale_count} stale, "
                         f"{broken_count} broken, {len(signals)} valid")
        return signals

    def _is_broken_signal(self, signal: dict, coin: str) -> bool:
        """Detect broken signals that should never reach D3 or frontend.

        Handles two signal formats:
        - D2 signals: have entry/stop_loss/take_profit/composite_score
        - D1 tiers: have tier/score/direction/timeframes (no entry prices)

        Broken signals:
        - Zero entry price (no valid entry) — D2 only
        - Zero SL with zero TP (no risk management) — D2 only
        - Score > 100 or score < 0 (impossible score) — both D1 and D2
        - Missing required fields — both D1 and D2
        """
        # Detect signal type by keys present
        is_d2_signal = "entry" in signal or "composite_score" in signal

        # D1 tiers don't have entry prices — check tier/score only
        if not is_d2_signal:
            tier = signal.get("tier", "")
            score = signal.get("score", 0)
            # D1 tier must exist and score must be reasonable (0-100)
            if not tier:
                logger.debug(f"[data_layer] Broken D1 tier {coin}: missing tier")
                return True
            if score < 0 or score > 100:
                logger.debug(f"[data_layer] Broken D1 tier {coin}: impossible score={score}")
                return True
            return False

        # D2 signal — full integrity check
        entry = signal.get("entry", 0)
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit_1", signal.get("take_profit", 0))
        score = signal.get("composite_score", 0)

        # Zero entry — can't trade
        if not entry or entry <= 0:
            logger.debug(f"[data_layer] Broken signal {coin}: zero/negative entry={entry}")
            return True

        # No SL and no TP — no risk management
        if sl == 0 and tp == 0:
            logger.debug(f"[data_layer] Broken signal {coin}: zero SL and zero TP")
            return True

        # Impossible score
        if score < 0 or score > 100:
            logger.debug(f"[data_layer] Broken signal {coin}: impossible score={score}")
            return True

        return False

    def _freshness_label(self, age_min: float, ttl_min: float) -> str:
        """Assign freshness label based on age relative to TTL.

        HOT     : < 25% of TTL age → very recent, high confidence
        WARM    : 25-50% of TTL age → recent, normal confidence
        COOL    : 50-75% of TTL age → aging, lower confidence
        STALE   : > 75% of TTL age → near expiry, should be refreshed
        """
        ratio = age_min / ttl_min
        if ratio < 0.25:
            return "HOT"
        elif ratio < 0.50:
            return "WARM"
        elif ratio < 0.75:
            return "COOL"
        else:
            return "STALE"

    async def cleanup_stale(self):
        """Remove stale/orphaned data from all layers.

        - Expired D1 tiers (past 4H TTL)
        - Expired D2 signals (past 15M TTL)
        - D2 signals for coins no longer in symbol list
        - D3 decisions for coins with no matching D2 signal
        """
        now = datetime.now(timezone.utc).timestamp()
        d1_cutoff = now - (SIGNAL_TTL_MINUTES * 60)
        d2_cutoff = now - (D2_SIGNAL_TTL_MINUTES * 60)

        removed_d1 = 0
        removed_d2 = 0
        removed_d3 = 0
        valid_d2_coins = set()

        # Clean D1 tiers
        for coin in list(state_store.d1_tiers.keys()):
            entry = state_store.d1_tiers[coin]
            updated_at = entry.get("updated_at", 0)
            if updated_at < d1_cutoff:
                del state_store.d1_tiers[coin]
                removed_d1 += 1

        # Clean D2 signals
        for coin, sig in list(state_store.d2_signals.items()):
            born_at = getattr(sig, 'born_at', None)
            if born_at is None:
                continue
            born_ts = born_at.timestamp() if hasattr(born_at, 'timestamp') else float(born_at)
            if born_ts < d2_cutoff:
                del state_store.d2_signals[coin]
                removed_d2 += 1
            else:
                valid_d2_coins.add(coin)

        # Clean orphaned D3 decisions (no matching D2 signal)
        for coin in list(state_store.d3_decisions.keys()):
            if coin not in valid_d2_coins:
                del state_store.d3_decisions[coin]
                removed_d3 += 1

        if removed_d1 or removed_d2 or removed_d3:
            logger.info(f"[data_layer] Cleanup: removed {removed_d1} D1, "
                        f"{removed_d2} D2, {removed_d3} D3 stale entries")

    async def get_stats(self) -> dict:
        """Return current data layer stats for health checks."""
        now = datetime.now(timezone.utc).timestamp()
        d1_cutoff = now - (SIGNAL_TTL_MINUTES * 60)
        d2_cutoff = now - (D2_SIGNAL_TTL_MINUTES * 60)

        d1_valid = 0
        d1_stale = 0
        for e in state_store.d1_tiers.values():
            updated_at = e.get("updated_at", 0)
            if updated_at >= d1_cutoff:
                d1_valid += 1
            else:
                d1_stale += 1

        d2_valid = 0
        d2_stale = 0
        for sig in state_store.d2_signals.values():
            born_at = getattr(sig, 'born_at', None)
            if born_at is not None:
                born_ts = born_at.timestamp() if hasattr(born_at, 'timestamp') else float(born_at)
                if born_ts >= d2_cutoff:
                    d2_valid += 1
                else:
                    d2_stale += 1

        freshness_counts = {"HOT": 0, "WARM": 0, "COOL": 0, "STALE": 0}
        for e in state_store.d1_tiers.values():
            f = e.get("_freshness")
            if f:
                freshness_counts[f] = freshness_counts.get(f, 0) + 1

        return {
            "d1_total": len(state_store.d1_tiers),
            "d1_valid": d1_valid,
            "d1_stale": d1_stale,
            "d1_freshness": freshness_counts,
            "d2_total": len(state_store.d2_signals),
            "d2_valid": d2_valid,
            "d2_stale": d2_stale,
            "d3_total": len(state_store.d3_decisions),
            "last_d1_scan": state_store.last_d1_scan,
            "last_d2_scan": state_store.last_d2_scan,
        }


# Singleton
data_layer = DataLayer()
