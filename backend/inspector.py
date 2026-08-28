"""Inspector — Background audit & self-healing subsystem.

Event-driven (NOT timer-driven). Called by the engine after each scan
cycle completes:

  D1 finishes a 4H cycle   → scanner calls inspector.after_d1_cycle(scanned_coins)
  D2 finishes a 15M cycle  → ltf_engine calls inspector.after_d2_cycle(scanned_coins)

Role: gap-filler. D1/D2 are time-driven and may miss coins (network,
timeout, error). Inspector catches missed coins and pushes them back
through the pipeline once.

NO continuous 60s timer. NO continuous rescanning. NO drift invalidation
(PASS 1 handles that).

Audit reports for /api/inspector are still produced on each event.
"""
import asyncio
import logging
from datetime import datetime, timezone

from backend.state_store import state_store
from backend.market_data import market_data
from backend.config import D2_SIGNAL_TTL_MINUTES

logger = logging.getLogger("judah.inspector")

# ── Thresholds ──────────────────────────────────────────────────────

_D1_COVERAGE_ALERT_PCT = 98         # Alert if D1 coverage < this %
_D1_MAX_SCAN_AGE_SEC = 5 * 3600     # Alert if last D1 scan > 5 hours ago
_D2_EP_DRIFT_PCT = 2.0              # Reference — drift invalidation is PASS 1's job
_D2_STALE_ALERT_PCT = 30            # Alert if > this % of signals are STALE
_D3_STALE_ALERT = 10                # Alert if > this many stale D3 decisions

# Cap how many missed coins to rescan per cycle (prevents overload)
_MAX_RESCAN_PER_CYCLE = 30


# ── Subsystem Audit Functions ───────────────────────────────────────

def _audit_d1(symbols: list) -> dict:
    """Audit D1 scanner health."""
    total = len(symbols)
    tiers = state_store.d1_tiers
    covered = sum(1 for s in symbols if s in tiers)
    coverage_pct = (covered / total * 100) if total > 0 else 0

    d1_status = state_store.get_d1_status()
    last_scan_ts = state_store.last_d1_scan
    scan_age_sec = 0
    if last_scan_ts:
        scan_age_sec = datetime.now(timezone.utc).timestamp() - last_scan_ts

    status = "OK"
    issues = []

    if coverage_pct < _D1_COVERAGE_ALERT_PCT:
        status = "DEGRADED"
        issues.append(f"coverage {coverage_pct:.0f}% ({covered}/{total})")
    if scan_age_sec > _D1_MAX_SCAN_AGE_SEC:
        status = "STALE"
        issues.append(f"last scan {scan_age_sec / 3600:.1f}h ago")

    return {
        "coverage_pct": round(coverage_pct, 1),
        "coins_covered": covered,
        "coins_total": total,
        "scan_age_sec": round(scan_age_sec),
        "engine_status": d1_status.get("status", "unknown"),
        "status": status,
        "issues": issues,
    }


def _audit_d2(symbols: list, scanned_coins: set) -> dict:
    """Audit D2 scanner health.

    scanned_coins: set of coins D2 just finished processing in this cycle.
    The "missed" list is computed against this set — not against the full
    universe — so we only flag coins the engine tried and failed/skipped.
    """
    all_d2 = state_store.get_all_d2_signals()
    total_signals = len(all_d2)
    now = datetime.now(timezone.utc)

    stale_count = 0
    drift_count = 0
    missed_coins = []
    avg_age_min = 0
    drift_details = []

    age_sum = 0
    for coin, sig in all_d2.items():
        age_min = (now - sig.born_at).total_seconds() / 60 if hasattr(sig, 'born_at') else 0
        age_sum += age_min

        # Check staleness
        if age_min > D2_SIGNAL_TTL_MINUTES * 0.75:  # > 75% of TTL
            stale_count += 1

        # Check EP drift (READ-ONLY here — PASS 1 owns invalidation)
        if sig.entry > 0:
            candles = market_data.get_candles(coin, "15M")
            if candles:
                last = candles[-1]
                mid_price = (last.high + last.low) / 2
                drift = abs(mid_price - sig.entry) / sig.entry * 100
                if drift > _D2_EP_DRIFT_PCT:
                    drift_count += 1
                    drift_details.append({
                        "coin": coin,
                        "entry": round(sig.entry, 6),
                        "market": round(mid_price, 6),
                        "drift_pct": round(drift, 1),
                    })

    avg_age_min = (age_sum / total_signals) if total_signals > 0 else 0

    # Missed coins = symbols D2 tried this cycle but produced no signal object
    # (failed scan, timeout, exception, or pipeline rejected without writing).
    # Universe coins not in scanned_coins are NOT considered missed — D2 may
    # not have reached them yet (will be caught on next call).
    for coin in scanned_coins:
        if coin not in all_d2:
            missed_coins.append(coin)

    stale_pct = (stale_count / total_signals * 100) if total_signals > 0 else 0

    status = "OK"
    issues = []
    if missed_coins:
        status = "WARNING" if len(missed_coins) > 10 else "OK"
        issues.append(f"{len(missed_coins)} coins missed by D2 in last cycle")
    if stale_pct > _D2_STALE_ALERT_PCT:
        status = "WARNING"
        issues.append(f"{stale_pct:.0f}% signals stale ({stale_count}/{total_signals})")
    if drift_count > 0:
        status = "WARNING"
        issues.append(f"{drift_count} signals with EP drift > {_D2_EP_DRIFT_PCT}%")

    d2_status = state_store.get_d2_status()
    if d2_status.get("status") == "DEGRADED":
        status = "DEGRADED"

    return {
        "signal_count": total_signals,
        "avg_age_min": round(avg_age_min, 1),
        "stale_count": stale_count,
        "stale_pct": round(stale_pct, 1),
        "drift_count": drift_count,
        "drift_details": drift_details[:10],
        "missed_count": len(missed_coins),
        "missed_coins_sample": missed_coins[:20],
        "engine_status": d2_status.get("status", "unknown"),
        "status": status,
        "issues": issues,
    }


def _audit_d3() -> dict:
    """Audit D3 fusion health."""
    all_d2 = state_store.get_all_d2_signals()
    all_decisions = state_store.get_all_decisions()

    total_decisions = len(all_decisions)
    stale_decisions = 0
    conflict_count = 0

    for coin, decision in all_decisions.items():
        # Stale: D3 decision exists but D2 signal is gone
        if coin not in all_d2:
            stale_decisions += 1
        # Conflict: Type E
        if decision.get("signal_type") == "E":
            conflict_count += 1

    status = "OK"
    issues = []
    if stale_decisions > _D3_STALE_ALERT:
        status = "WARNING"
        issues.append(f"{stale_decisions} stale D3 decisions (D2 signal gone)")
    if conflict_count > 0:
        issues.append(f"{conflict_count} Type E conflict signals")

    return {
        "decision_count": total_decisions,
        "stale_decisions": stale_decisions,
        "conflict_count": conflict_count,
        "status": status,
        "issues": issues,
    }


def _audit_datalayer(symbols: list) -> dict:
    """Audit data layer — orphan entries, cleanup needs."""
    d1_tiers = state_store.d1_tiers
    d2_signals = state_store.get_all_d2_signals()

    # Orphan D1 tiers (coin not in active symbol list)
    orphan_d1 = [c for c in d1_tiers if c not in symbols]
    # Orphan D2 signals (coin not in active symbol list)
    orphan_d2 = [c for c in d2_signals if c not in symbols]

    now = datetime.now(timezone.utc)
    stale_d1 = 0
    for coin, tier in d1_tiers.items():
        updated_at = tier.get("updated_at", "")
        if updated_at:
            try:
                age_sec = (now - datetime.fromisoformat(updated_at)).total_seconds()
                if age_sec > 4 * 3600:  # > 4h
                    stale_d1 += 1
            except (ValueError, TypeError):
                pass

    status = "OK"
    issues = []
    if orphan_d1:
        issues.append(f"{len(orphan_d1)} orphan D1 tiers")
    if orphan_d2:
        issues.append(f"{len(orphan_d2)} orphan D2 signals")
    if stale_d1 > 0:
        issues.append(f"{stale_d1} D1 tiers older than 4h")

    return {
        "d1_tier_count": len(d1_tiers),
        "d2_signal_count": len(d2_signals),
        "orphan_d1_count": len(orphan_d1),
        "orphan_d2_count": len(orphan_d2),
        "stale_d1_count": stale_d1,
        "status": status,
        "issues": issues,
    }


# ── Rescan Helpers ──────────────────────────────────────────────────

async def _rescan_d2_missed(missed_coins: list) -> list:
    """Push missed D2 coins through the LTF pipeline.

    Uses in-place .update() when an existing signal is present (preserves
    signal_id and born_at — no card flicker, no fresh HOT badge).
    """
    actions = []
    if not missed_coins:
        return actions

    from backend.engines.ltf_scanner import LTFSignal
    from backend.engines.ltf_engine import scan_entry

    for coin in missed_coins[:_MAX_RESCAN_PER_CYCLE]:
        try:
            result = await asyncio.wait_for(scan_entry(coin), timeout=15)
            if not result:
                continue
            # Update in-place — preserves signal_id, born_at, score_history.
            existing = state_store.get_d2_signal(coin)
            if existing and isinstance(existing, LTFSignal):
                existing.update(result)
                await state_store.set_d2_signal(coin, existing)
                actions.append(f"rescanned {coin} (in-place)")
            else:
                sig = LTFSignal(coin, result)
                await state_store.set_d2_signal(coin, sig)
                actions.append(f"scanned {coin} (new)")
        except asyncio.TimeoutError:
            logger.debug(f"[inspector] rescan {coin} timed out")
        except Exception as e:
            logger.debug(f"[inspector] rescan {coin} failed: {e}")

    return actions


async def _rescan_d1_missed(missed_coins: list) -> list:
    """Push missed D1 coins back through the D1 scanner.

    D1 is 4H-timeframe, run by backend.scanner.scanner. The actual scan
    method depends on the scanner API; for now, log + flag for visibility.
    """
    actions = []
    if not missed_coins:
        return actions

    # D1's pipeline is heavy (multi-TF). Rescan only when coverage gap is
    # significant (>5 missed coins). For <5, log only.
    if len(missed_coins) < 5:
        actions.append(f"d1 missed={len(missed_coins)} (logged, not rescan)")
        return actions

    # Placeholder — D1 re-trigger is wired separately. Log the gap.
    actions.append(f"d1 missed={len(missed_coins)} (coverage gap logged)")
    return actions


# ── Event-Driven Inspector ──────────────────────────────────────────

class Inspector:
    """Event-driven background auditor.

    NOT timer-based. The caller (D1 scanner after a full cycle, D2 engine
    after a full cycle) invokes after_d1_cycle() / after_d2_cycle() and
    passes the list of coins it actually processed. Inspector finds the
    ones without a signal/tier and rescans them.
    """

    def __init__(self):
        self._symbols = []
        self._d3_notify = None  # wire after D3 starts

    def set_symbols(self, symbols: list):
        self._symbols = symbols

    def set_d3_notify(self, fn):
        """Allow Inspector-triggered rescans to push a D3 re-fuse."""
        self._d3_notify = fn

    # ── Event handlers (called by D1 / D2 after each cycle) ──────────

    async def after_d2_cycle(self, scanned_coins: set, cycle_id: str = ""):
        """Called by D2 engine after each 15M scan cycle completes."""
        try:
            await self._run_once_d2(scanned_coins, cycle_id)
        except Exception:
            logger.exception("[inspector] after_d2_cycle error")

    async def after_d1_cycle(self, scanned_coins: set, cycle_id: str = ""):
        """Called by D1 scanner after each 4H scan cycle completes."""
        try:
            await self._run_once_d1(scanned_coins, cycle_id)
        except Exception:
            logger.exception("[inspector] after_d1_cycle error")

    # ── Internal audit + remediate ────────────────────────────────────

    async def _run_once_d2(self, scanned_coins: set, cycle_id: str):
        symbols = self._symbols
        scanned_set = set(scanned_coins) if scanned_coins else set()
        d1 = _audit_d1(symbols)
        d2 = _audit_d2(symbols, scanned_set)
        d3 = _audit_d3()
        dl = _audit_datalayer(symbols)

        # Rescan missed D2 coins (in-place updates)
        missed = d2.get("missed_coins_sample", []) if d2["missed_count"] > 0 else []
        actions = await _rescan_d2_missed(missed)

        # If we pushed new signals, notify D3 to re-fuse
        if actions and self._d3_notify:
            try:
                self._d3_notify()
            except Exception:
                pass

        self._store_report(d1, d2, d3, dl, actions, cycle_id, "D2")

    async def _run_once_d1(self, scanned_coins: set, cycle_id: str):
        symbols = self._symbols
        scanned_set = set(scanned_coins) if scanned_coins else set()

        # D1 missed = coins in scan list that don't have a D1 tier
        missed = []
        d1_tiers = state_store.d1_tiers
        for coin in scanned_set:
            if coin not in d1_tiers:
                missed.append(coin)

        d1 = _audit_d1(symbols)
        d3 = _audit_d3()
        dl = _audit_datalayer(symbols)

        actions = await _rescan_d1_missed(missed)

        # D2 audit uses empty scanned set here — D2 will run its own event
        d2 = _audit_d2(symbols, set())

        self._store_report(d1, d2, d3, dl, actions, cycle_id, "D1")

    # ── Report storage ───────────────────────────────────────────────

    def _store_report(self, d1, d2, d3, dl, actions, cycle_id, source):
        overall = "HEALTHY"
        if any(x["status"] == "DEGRADED" for x in [d1, d2, d3, dl]):
            overall = "DEGRADED"
        elif any(x["status"] == "WARNING" for x in [d1, d2, d3, dl]):
            overall = "WARNING"

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall": overall,
            "source": source,
            "cycle_id": cycle_id,
            "d1": d1,
            "d2": d2,
            "d3": d3,
            "datalayer": dl,
            "actions": actions,
        }
        state_store._inspector_report = report

        # Log summary
        if overall == "HEALTHY":
            logger.info(f"[inspector] OK [{source}/{cycle_id}] D2:{d2['signal_count']} sigs, "
                        f"D3:{d3['decision_count']} decisions, actions={len(actions)}")
        else:
            all_issues = d1.get("issues", []) + d2.get("issues", []) + d3.get("issues", []) + dl.get("issues", [])
            logger.info(f"[inspector] {overall} [{source}/{cycle_id}] "
                        f"{', '.join(all_issues[:3])}, actions={len(actions)}")


# Module-level singleton
inspector = Inspector()