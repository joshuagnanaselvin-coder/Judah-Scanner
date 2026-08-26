"""Inspector — Background audit & self-healing subsystem.

Runs every 60s and audits 4 subsystems:
  D1  → coverage, scan age, degraded status
  D2  → signal count, stale count, EP drift, no-signal coins
  D3  → decision count, stale decisions, conflict count
  DL  → orphan D1 tiers, orphan D2 signals

Auto-remediates trivial issues (force-rescan, invalidate drifted signals).
Logs actionable findings at WARNING, healthy status at INFO.
"""
import asyncio
import logging
from datetime import datetime, timezone

from backend.state_store import state_store
from backend.market_data import market_data
from backend.config import D2_SIGNAL_TTL_MINUTES

logger = logging.getLogger("judah.inspector")

# ── Thresholds ──────────────────────────────────────────────────────

_INSPECT_INTERVAL_SEC = 60          # How often to run (seconds)
_D1_COVERAGE_ALERT_PCT = 98         # Alert if D1 coverage < this %
_D1_MAX_SCAN_AGE_SEC = 5 * 3600     # Alert if last D1 scan > 5 hours ago
_D2_NO_SIGNAL_REScan_MIN = 30       # Force-rescan coins with no signal > 30 min
_D2_EP_DRIFT_PCT = 2.0              # Invalidate signal if EP drift > this %
_D2_STALE_ALERT_PCT = 30            # Alert if > this % of signals are STALE
_D3_STALE_ALERT = 10                # Alert if > this many stale D3 decisions


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


def _audit_d2(symbols: list) -> dict:
    """Audit D2 scanner health — signal count, staleness, EP drift, no-signal coins."""
    all_d2 = state_store.get_all_d2_signals()
    total_signals = len(all_d2)
    now = datetime.now(timezone.utc)

    stale_count = 0
    drift_count = 0
    no_signal_coins = []
    avg_age_min = 0
    drift_details = []

    age_sum = 0
    for coin, sig in all_d2.items():
        age_min = (now - sig.born_at).total_seconds() / 60 if hasattr(sig, 'born_at') else 0
        age_sum += age_min

        # Check staleness
        if age_min > D2_SIGNAL_TTL_MINUTES * 0.75:  # > 75% of TTL
            stale_count += 1

        # Check EP drift
        if sig.entry > 0:
            candles = market_data.get_candles(coin, "15M")
            if candles:
                last = candles[-1]
                mid_price = (last["high"] + last["low"]) / 2
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

    # Find coins with no D2 signal
    for coin in symbols:
        if coin not in all_d2:
            no_signal_coins.append(coin)

    stale_pct = (stale_count / total_signals * 100) if total_signals > 0 else 0

    status = "OK"
    issues = []
    if no_signal_coins:
        status = "DEGRADED" if len(no_signal_coins) > 50 else "WARNING"
        issues.append(f"{len(no_signal_coins)} coins with no D2 signal")
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
        "drift_details": drift_details[:10],  # cap for API response
        "no_signal_count": len(no_signal_coins),
        "no_signal_coins_sample": no_signal_coins[:20],
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


# ── Auto-Remediation ────────────────────────────────────────────────

async def _remediate(d2_audit: dict, symbols: list) -> list:
    """Fix trivial issues automatically. Returns list of actions taken."""
    actions = []

    # Force-rescan coins with no D2 signal that have been waiting
    no_signal = d2_audit.get("no_signal_coins_sample", [])
    if no_signal:
        from backend.engines.ltf_engine import scan_entry, _mark_scanned
        for coin in no_signal[:50]:  # cap to avoid overload
            try:
                result = await asyncio.wait_for(scan_entry(coin), timeout=15)
                if result:
                    from backend.engines.ltf_scanner import LTFSignal
                    sig = LTFSignal(coin, result)
                    await state_store.set_d2_signal(coin, sig)
                    _mark_scanned(coin)
                    actions.append(f"rescanned {coin} (no signal remediated)")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                logger.debug(f"[inspector] rescanned {coin} failed: {e}")

    # Invalidate signals with EP drift > 2%
    for drift_info in d2_audit.get("drift_details", []):
        coin = drift_info["coin"]
        await state_store.set_d2_signal(coin, None)
        actions.append(f"invalidated {coin} (drift {drift_info['drift_pct']}%)")

    return actions


# ── Main Inspector Loop ─────────────────────────────────────────────

class Inspector:
    """Background auditor for D1/D2/D3/Data Layer."""

    def __init__(self):
        self.running = False
        self.task = None
        self.cycle_id = "INSP-0001"

    async def start(self, symbols: list):
        """Start the inspector loop."""
        self.running = True
        self._symbols = symbols
        self.task = asyncio.create_task(self._run())
        logger.info(f"[inspector] Started — auditing {len(symbols)} coins every {_INSPECT_INTERVAL_SEC}s")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        while self.running:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[inspector] cycle error")
            await asyncio.sleep(_INSPECT_INTERVAL_SEC)

    async def _run_once(self):
        """Run one inspection cycle."""
        symbols = getattr(self, '_symbols', [])
        d1 = _audit_d1(symbols)
        d2 = _audit_d2(symbols)
        d3 = _audit_d3()
        dl = _audit_datalayer(symbols)

        # Auto-remediate D2 issues
        actions = []
        if d2["status"] in ("DEGRADED", "WARNING"):
            actions = await _remediate(d2, symbols)

        # Store results for API access
        overall = "HEALTHY"
        if any(x["status"] == "DEGRADED" for x in [d1, d2, d3, dl]):
            overall = "DEGRADED"
        elif any(x["status"] == "WARNING" for x in [d1, d2, d3, dl]):
            overall = "WARNING"

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "overall": overall,
            "d1": d1,
            "d2": d2,
            "d3": d3,
            "datalayer": dl,
            "actions": actions,
            "cycle_interval_sec": _INSPECT_INTERVAL_SEC,
        }

        # Store in state for API
        await state_store.set_timestamp("last_inspector_cycle")
        state_store._inspector_report = report

        # Log summary
        if overall == "HEALTHY":
            logger.info(f"[inspector] OK — D2:{d2['signal_count']} sigs, "
                        f"D3:{d3['decision_count']} decisions, "
                        f"actions={len(actions)}")
        else:
            all_issues = d1.get("issues", []) + d2.get("issues", []) + d3.get("issues", []) + dl.get("issues", [])
            logger.warning(f"[inspector] {overall} — {', '.join(all_issues[:5])}, "
                           f"actions={len(actions)}")


# Module-level singleton
inspector = Inspector()
