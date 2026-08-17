"""Signal storage with freshness tracking, FVG ledger, TTL cleanup, and revalidation."""
import logging
from datetime import datetime, timezone
from backend.config import (
    SIGNAL_TTL_MINUTES, MAX_SIGNALS, SCAN_INTERVAL_SECONDS,
)
from collections import defaultdict
from backend.performance_tracker import performance_tracker

logger = logging.getLogger("judah.signal_store")


class SignalStore:
    def __init__(self):
        self.signals: dict = {}
        self.fvg_ledger: dict = {}
        self.scanned_recently: dict = {}

    def add(self, signal: dict) -> bool:
        signal["base_score"] = signal.get("composite_score", 0)
        signal["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        signal["age_ticks"] = 0
        signal["freshness_state"] = "hot"
        signal["freshness_factor"] = 1.0

        key = f"{signal['symbol']}_{signal['engine']}"
        if key in self.signals:
            if signal["base_score"] <= self.signals[key]["base_score"]:
                return False
        self.signals[key] = signal
        return True

    def get(self, symbol, engine):
        return self.signals.get(f"{symbol}_{engine}")

    def get_all(self) -> list:
        now = datetime.now(timezone.utc).timestamp() * 1000
        self._clean_expired(now)
        return sorted(self.signals.values(),
                      key=lambda s: s["composite_score"], reverse=True)[:MAX_SIGNALS]

    def get_all_decisions(self) -> dict:
        """Return all signals keyed by symbol_engine (for ws_hub initial payload)."""
        now = datetime.now(timezone.utc).timestamp() * 1000
        self._clean_expired(now)
        return dict(self.signals)

    def get_stats(self) -> dict:
        """Return summary stats for the header stat chips."""
        all_sigs = self.get_all()
        tiers = {"SNIPER": 0, "OPPORTUNITY": 0, "WATCH": 0, "REJECTED": 0, "INVALIDATED": 0, "EXPIRED": 0}
        for s in all_sigs:
            t = s.get("tier", "REJECTED")
            tiers[t] = tiers.get(t, 0) + 1
        return {
            "total": len(all_sigs),
            "by_tier": tiers,
            "hot": sum(1 for s in all_sigs if s.get("freshness_state") == "hot"),
            "warm": sum(1 for s in all_sigs if s.get("freshness_state") == "warm"),
        }

    def remove(self, symbol, engine):
        self.signals.pop(f"{symbol}_{engine}", None)

    def refresh(self, signal, current_price=None):
        signal['age_ticks'] = signal.get('age_ticks', 0) + 1
        if current_price is not None:
            signal['current_price'] = current_price

        age = signal['age_ticks']
        base = signal.get('base_score', signal.get('composite_score', 0))
        signal['base_score'] = base
        rr = signal.get('rr', 1.5)

        # Decay: 1pt per ~2min, floor at 20
        # 2min=1pt, 5min=2pts, 10min=4pts, 20min=8pts, 30min=12pts
        decay_map = {12: 1, 30: 2, 60: 4, 120: 8, 180: 12}
        decay = 0
        for threshold, pts in sorted(decay_map.items()):
            if age >= threshold:
                decay = pts
        # Live display score (decays for visual feedback only — does NOT affect tier)
        signal['composite_score'] = max(20, base - decay)

        # Tier is locked to the ORIGINAL base score — never downgraded by age decay.
        # A SNIPER stays SNIPER until the setup is revalidated or invalidated.
        signal['tier'] = _recalc_tier(base, rr)

        # Freshness state
        if age < 12:
            signal['freshness_state'] = 'hot'
            signal['freshness_factor'] = 1.0
        elif age < 30:
            signal['freshness_state'] = 'warm'
            signal['freshness_factor'] = 0.85
        elif age < 60:
            signal['freshness_state'] = 'cool'
            signal['freshness_factor'] = 0.70
        elif age < 120:
            signal['freshness_state'] = 'cold'
            signal['freshness_factor'] = 0.55
        else:
            signal['freshness_state'] = 'dead'
            signal['freshness_factor'] = 0.40

        signal['age_minutes'] = (age * SCAN_INTERVAL_SECONDS) // 60

        next_decay = next((t for t in sorted(decay_map.keys()) if t > age), None)
        signal['ticks_to_next_decay'] = next_decay - age if next_decay else 0

        logger.debug(f"[refresh] {signal['id']} age={age} base={base} decay={decay} "
                     f"live={signal['composite_score']} state={signal['freshness_state']}")
        return signal

    def revalidate(self, signal, new_signal: dict) -> dict:
        """Called at 15min and 30min checkpoints.
        If setup still valid (new_signal passed scan), reset age and restore score.
        If setup broken, mark INVALIDATED so it gets removed."""
        if not new_signal:
            signal["freshness_state"] = "INVALIDATED"
            signal["composite_score"] = 0
            signal["invalidation_reason"] = "setup_broken"
            logger.info(f"[revalidate] {signal['id']} INVALIDATED — setup no longer valid")
            return signal

        # Setup still valid — reset to fresh
        # Copy fresh fields from the new scan result
        # Copy fresh fields from the new scan result.
        # Signal builder uses take_profit_1/take_profit_2 (plural) since v2;
        # also support legacy singular take_profit for any older code paths.
        for field in ("composite_score", "base_score", "tier", "rr",
                      "entry", "stop_loss", "direction",
                      "crt_score", "smc_score", "session", "scenario",
                      "session_label", "market_structure", "fvg",
                      "institutional_order_flow", "volume_profile",
                      "liquidity_pools", "confluence_bonuses",
                      "distance_to_entry_pct", "current_price",
                      "take_profit_1", "take_profit_2"):
            if field in new_signal:
                signal[field] = new_signal[field]

        # Backward-compat: set singular take_profit = tp1 if not already present
        if "take_profit" not in signal and "take_profit_1" in signal:
            signal["take_profit"] = signal["take_profit_1"]

        signal["age_ticks"] = 0
        signal["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        signal["freshness_state"] = "hot"
        signal["freshness_factor"] = 1.0
        signal["revalidation_count"] = signal.get("revalidation_count", 0) + 1

        logger.info(f"[revalidate] {signal['id']} RESET — setup still valid, "
                    f"tier={signal['tier']} score={signal['composite_score']} "
                    f"(#{signal['revalidation_count']} reval)")
        return signal

    def should_revalidate(self, signal, now_ms=None) -> bool:
        """Check if signal hit a revalidation checkpoint (15min or 30min)."""
        if now_ms is None:
            now_ms = datetime.now(timezone.utc).timestamp() * 1000
        age_min = (now_ms - signal.get("timestamp", 0)) / 60000
        rev_count = signal.get("revalidation_count", 0)

        # First checkpoint: 15min
        if age_min >= 15 and rev_count == 0:
            return True
        # Second checkpoint: 30min
        if age_min >= 30 and rev_count == 1:
            return True
        return False

    def _expire(self, signal, reason):
        signal["composite_score"] = 0
        signal["freshness_state"] = "EXPIRED"
        signal["freshness_factor"] = 0.0
        signal["invalidation_reason"] = reason
        return signal

    def _clean_expired(self, now_ms):
        ttl = SIGNAL_TTL_MINUTES * 60 * 1000
        expired = [k for k, s in self.signals.items()
                   if (now_ms - s["timestamp"]) > ttl]
        for k in expired:
            self.signals[k]["outcome"] = "TIMEOUT"
            performance_tracker.record(self.signals[k])
            del self.signals[k]

    def mark_scanned(self, symbol, engine):
        key = f"{symbol}_{engine}"
        self.scanned_recently[key] = datetime.now(timezone.utc).timestamp()
        # Periodic purge — remove entries older than 5 minutes (memory safety)
        cutoff = datetime.now(timezone.utc).timestamp() - 300
        stale = [k for k, v in self.scanned_recently.items() if v < cutoff]
        for k in stale:
            del self.scanned_recently[k]

    def was_recently_scanned(self, symbol, engine, max_age_sec=30) -> bool:
        key = f"{symbol}_{engine}"
        last = self.scanned_recently.get(key, 0)
        return (datetime.now(timezone.utc).timestamp() - last) < max_age_sec

    def update_fvg_ledger(self, symbol, engine, candles):
        from backend.vsp_helpers import detect_fvg
        key = f"{symbol}_{engine}"
        fvgs = detect_fvg(candles, 20)

        if key not in self.fvg_ledger:
            self.fvg_ledger[key] = []

        existing = self.fvg_ledger[key]
        for fvg in fvgs:
            found = any(
                ef["type"] == fvg["type"] and
                abs(ef["top"] - fvg["top"]) < 0.01 and
                abs(ef["bottom"] - fvg["bottom"]) < 0.01
                for ef in existing
            )
            if not found:
                existing.append(fvg)
            else:
                for ef in existing:
                    if ef["type"] == fvg["type"] and abs(ef["top"] - fvg["top"]) < 0.01:
                        ef["filled"] = fvg["filled"]

        if len(candles) > 100:
            cutoff = len(candles) - 100
            self.fvg_ledger[key] = [f for f in existing if f.get("candle_index", 0) >= cutoff]

        # Cap FVG ledger per symbol (max 20 entries)
        if len(self.fvg_ledger[key]) > 20:
            self.fvg_ledger[key] = self.fvg_ledger[key][-20:]


def _recalc_tier(score, rr):
    """Tier driven by score alone (locked to base_score, never downgraded by age).
    RR is stored and displayed as a risk indicator but does not gate tier."""
    from backend.config import TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


signal_store = SignalStore()
