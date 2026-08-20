"""Dimension 3 — Decision Layer.

Reads D1 tiers + D2 signals from state_store, classifies signals
into types A/B/C/D/E, packages for frontend, pushes via WebSocket.

No sensitivity modes — fixed thresholds for both D1 and D2.
Signal Types: A (HTF Structure), B (LTF Momentum), C (Full Confluence),
              D (HTF Early Warning), E (Conflict/Trap).

D2 is fully independent — scans all pairs regardless of D1 tier.
Type B signals (D1 REJECTED + strong D2) are valid trading opportunities.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from backend.config import (
    TIER_SNIPER_SCORE,
    TIER_OPPORTUNITY_SCORE,
    TIER_WATCH_SCORE,
    TIER_WEAK_SCORE,
    D2_SIGNAL_TTL_MINUTES,
    TYPE_B_MIN_D2_SCORE,
    TYPE_B_ENTRY_PRECISION_GATE,
    IGNORE_MIN_SCORE,
    D3_D1_SNIPER_THRESHOLD,
    D3_D2_SNIPER_THRESHOLD,
    D3_D1_APPROVED_THRESHOLD,
    D3_D2_MODERATE_THRESHOLD,
    D3_TYPE_D_D1_THRESHOLD,
)
from backend.state_store import state_store
from backend.ws_hub import broadcast, get_initial_payload
from backend.market_evolution import evaluate as me_evaluate, get_dashboard_stats
from backend.market_evolution.history import history_store
from backend.alignment_engine import alignment_engine, AlignmentLevel
from backend.trade_plan_authority import trade_plan_authority
from backend.risk_authority import risk_authority

logger = logging.getLogger("judah.fusion")

# ── Signal Type Definitions ────────────────────────────────────────

SIGNAL_TYPES = {
    "A": {"name": "HTF Structure",   "color": "#eab308", "icon": "🟡", "action": "EXECUTE", "ttl_min": 120},
    "B": {"name": "LTF Momentum",    "color": "#3b82f6", "icon": "🔵", "action": "EXECUTE", "ttl_min": 15},
    "C": {"name": "Full Confluence", "color": "#22c55e", "icon": "🟢", "action": "EXECUTE", "ttl_min": 240},
    "D": {"name": "HTF Early Warn",  "color": "#f97316", "icon": "🟠", "action": "WATCH",   "ttl_min": 60},
    "E": {"name": "Conflict/Trap",   "color": "#ef4444", "icon": "🔴", "action": "ALERT",   "ttl_min": 0},
    "F": {"name": "LTF Weak",        "color": "#a855f7", "icon": "🟣", "action": "WATCH",   "ttl_min": 30},
}

# Position size multipliers by signal type
TYPE_POSITION_MULT = {"A": 0.75, "B": 0.35, "C": 1.0, "D": 0.0, "E": 0.0, "F": 0.0}

# Stop width multipliers by signal type
TYPE_STOP_MULT = {"A": 1.5, "B": 1.0, "C": 1.5, "D": 1.5, "E": 1.5, "F": 2.0}

# Decay rates per signal type (per 5-min interval)
DECAY_TYPE_A = 0.94
DECAY_TYPE_C = 0.98


def classify_tier(score: float) -> str:
    """Classify a score into SNIPER / OPPORTUNITY / WATCH / REJECTED.

    Uses thresholds from config.py:
      SNIPER      >= TIER_SNIPER_SCORE (55)
      OPPORTUNITY >= TIER_OPPORTUNITY_SCORE (38)
      WATCH       >= TIER_WATCH_SCORE (22)
      REJECTED    <  TIER_WATCH_SCORE
    """
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


def calculate_ev(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Calculate Expected Value per trade: EV = (Win_Rate × Avg_Win) - (Loss_Rate × Avg_Loss)."""
    loss_rate = 1.0 - win_rate
    return (win_rate * avg_win) - (loss_rate * avg_loss)


def classify_signal_type(d1_tier: str, d1_score: float, d2_tier: str, d2_score: float,
                          d1_direction: str, d2_direction: str,
                          nascent_move: bool = False, entry_precision: float = 0.0) -> str:
    """Decision Layer: classify signal into Type A/B/C/D/E or None.

    REJECTED D1 and D2 tiers are not blocked — they reach D3 and can produce
    Type B (D2-only LTF momentum plays) when the D2 signal is strong enough.

    Uses config thresholds (D3_D1_SNIPER_THRESHOLD etc.) for consistency
    with the scoring system.

    Classification order (first match wins):
    1. Type C: D1 SNIPER (>= D3_D1_SNIPER_THRESHOLD) AND D2 SNIPER (>= D3_D2_SNIPER_THRESHOLD) AND directions align
    2. Type A: D1 approved (SNIPER/OPPORTUNITY) AND D2 >= D3_D2_MODERATE_THRESHOLD AND directions align
    3. Type B: D1 NOT approved AND D2 >= TYPE_B_MIN_D2_SCORE AND nascent_move AND EP >= TYPE_B_ENTRY_PRECISION_GATE
    4. Type E: D1 approved AND D2 strong BUT opposing directions
    5. Type D: D1 >= D3_TYPE_D_D1_THRESHOLD AND D2 not aligned
    6. Type F: catch-all — any D2 signal that didn't match above (manual watch)
    """
    from backend.config import (
        D3_D1_SNIPER_THRESHOLD, D3_D2_SNIPER_THRESHOLD,
        D3_D1_APPROVED_THRESHOLD, D3_D2_MODERATE_THRESHOLD,
        D3_TYPE_D_D1_THRESHOLD, TYPE_B_MIN_D2_SCORE,
        TYPE_B_ENTRY_PRECISION_GATE,
    )
    d1_approved = d1_tier in ("SNIPER", "OPPORTUNITY")
    d1_sniper = d1_score >= D3_D1_SNIPER_THRESHOLD
    d2_sniper = d2_score >= D3_D2_SNIPER_THRESHOLD
    d1_opp_or_above = d1_score >= D3_D1_APPROVED_THRESHOLD
    d2_min_b = d2_score >= TYPE_B_MIN_D2_SCORE
    directions_align = d1_direction == d2_direction and d1_direction != ""
    ep_gate = entry_precision >= TYPE_B_ENTRY_PRECISION_GATE

    # Type C: both SNIPER on both sides (highest conviction)
    if d1_sniper and d2_sniper and directions_align:
        return "C"

    # Type A: D1 approved + D2 moderate confirmation
    if d1_approved and d2_score >= D3_D2_MODERATE_THRESHOLD and directions_align:
        return "A"

    # Type B: D1 not approved, D2 LTF momentum play
    # nascent_move gate: required for weak D2 (WATCH), waived for strong D2 (OPP+)
    # Rationale: a D2 score at OPPORTUNITY+ already implies genuine momentum;
    # nascent_move adds confidence but should not block high-quality D2 signals.
    type_b_nascent_ok = nascent_move or d2_score >= D3_D1_APPROVED_THRESHOLD
    if not d1_approved and d2_min_b and type_b_nascent_ok and ep_gate:
        return "B"

    # Type E: both valid but opposing directions (check before Type D — more specific)
    if d1_approved and d2_tier in ("SNIPER", "OPPORTUNITY") and not directions_align:
        return "E"

    # Type D: D1 has signal data and D2 not aligned (HTF warning with LTF mismatch)
    d2_not_rejected = d2_tier != "REJECTED"
    if d1_opp_or_above and not directions_align and d2_not_rejected:
        return "D"

    # No signal type matched — fallback to Type F (LTF Weak)
    # All D2 coins now produce a card so user can see every coin for manual planning.
    return "F"


# ── DB Persistence Helpers ──────────────────────────────────────────

def _persist_decision(coin: str, sig_type: str | None, package: dict) -> None:
    """Write D3 decision to SQLite. Fire-and-forget — never blocks caller."""
    try:
        from backend import db
        alignment = package.get("alignment", {})
        risk_dec = package.get("risk_decision", {})
        trade_plan = package.get("trade_plan", {})
        row = {
            "coin": coin,
            "ts": datetime.now(timezone.utc).isoformat(),
            "signal_type": sig_type or "?",
            "action": package.get("action"),
            "position_mult": package.get("position_mult"),
            "stop_mult": package.get("stop_mult"),
            "ev_pct": package.get("expected_value_pct"),
            "confidence": alignment.get("alignment_score"),
            "d1_tier": package.get("d1_tier"),
            "d1_score": package.get("d1_score"),
            "d2_tier": package.get("d2_tier"),
            "d2_score": package.get("d2_score"),
        }
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(db.insert_decision(row))
        else:
            loop.run_until_complete(db.insert_decision(row))
    except Exception:
        logger.exception("[fusion] DB persist failed for %s", coin)


async def ensure_bayes_loaded() -> None:
    """Lazy-load Bayesian calibration from DB. Safe to call multiple times."""
    try:
        from backend.market_evolution.confidence import _load_bayes_from_db
        await _load_bayes_from_db()
    except Exception:
        logger.exception("[fusion] ensure_bayes_loaded failed")


# ── Fusion Engine ───────────────────────────────────────────────────

class FusionEngine:
    """Dimension 3 orchestrator.

    Watches D1 and D2 state_store timestamps for changes.
    When either dimension updates, fuses all affected coins
    and pushes to frontend.
    """

    def __init__(self):
        self.running: bool = False
        self.scan_task = None
        self._last_d1_scan: float = 0.0
        self._last_d2_scan: float = 0.0

    async def start(self):
        """Start D3 fusion loop."""
        # Phase 22: Rehydrate Bayesian calibration from SQLite on startup
        try:
            await ensure_bayes_loaded()
            logger.info("[fusion] Bayesian calibration rehydrated from DB")
        except Exception:
            logger.exception("[fusion] Bayes rehydration failed")

        self.running = True
        self.scan_task = asyncio.create_task(self._scan_loop())
        logger.info("[fusion] D3 Fusion Engine started (Signal Types A/B/C/D/E)")

    async def stop(self):
        self.running = False
        if self.scan_task:
            self.scan_task.cancel()

    async def _scan_loop(self):
        """Watch for D1/D2 changes and trigger fusion."""
        while self.running:
            try:
                await self._check_and_fuse()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[fusion] Scan error")
            await asyncio.sleep(2)

    async def _archive_expired(self):
        """Remove D3 decisions whose D2 signal has expired.

        D2 removes signals at 15-min TTL (ltf_engine PASS 1). D3 must catch
        the orphaned decisions and clean them up so they don't pile up.
        """
        archived = []
        signal_ids_to_remove = []
        d2_coins = set(state_store.get_all_d2_signals().keys())

        for coin, decision in list(state_store.d3_decisions.items()):
            if coin not in d2_coins:
                signal_id = decision.get("signal_id", "")
                signal_ids_to_remove.append(signal_id)
                archived.append(coin)

        # Remove from active D3 decisions AFTER collecting signal_ids
        for coin in archived:
            state_store.d3_decisions.pop(coin, None)

        if archived:
            logger.info(f"[fusion] Archived {len(archived)} expired decisions: "
                        f"{', '.join(archived[:5])}{'...' if len(archived) > 5 else ''}")
            # Notify frontend of removals
            await broadcast({
                "type": "REMOVE_SIGNALS",
                "signal_ids": signal_ids_to_remove,
                "moved_to_history": True,
            })

    async def _check_and_fuse(self):
        """Check if D1 or D2 has new data, fuse all D2 signals (independent of D1)."""
        last_d1 = state_store.last_d1_scan
        last_d2 = state_store.last_d2_scan

        # Always fuse if D2 changed. Only skip if BOTH are stale (system idle).
        d1_changed = last_d1 != self._last_d1_scan
        d2_changed = last_d2 != self._last_d2_scan
        if not d1_changed and not d2_changed:
            return

        self._last_d1_scan = last_d1
        self._last_d2_scan = last_d2

        # Archive any D3 decisions that lost their D2 signal
        await self._archive_expired()

        # D2 scans ALL 529 pairs independently — fuse all D2 signals
        d2_all = state_store.get_all_d2_signals()
        d2_coins = set(d2_all.keys())

        logger.info(f"[fusion] D2={len(d2_all)} signals to process")
        results = []
        type_e_alerts = []
        for coin in d2_coins:
            d1 = state_store.get_d1_tier(coin)
            d2 = d2_all[coin]
            pkg = await self._fuse_coin(coin, type_e_alerts)
            if pkg:
                results.append(pkg)

        if results:
            logger.info(f"[fusion] Fused {len(results)} from {len(d2_coins)} D2 signals")

        if type_e_alerts:
            logger.warning(f"[fusion] ⚠️  {len(type_e_alerts)} Type E conflict alerts this cycle:")
            for alert in type_e_alerts:
                logger.warning(f"  → {alert['coin']}: D1={alert['d1_dir']} vs D2={alert['d2_dir']} "
                               f"| D1={alert['d1_tier']}({alert['d1_score']:.0f}) "
                               f"D2={alert['d2_tier']}({alert['d2_score']:.0f})")

        # Update D3 fusion timestamp
        await state_store.set_timestamp("last_d3_fusion")

    async def _fuse_coin(self, coin: str, type_e_alerts: list | None = None):
        """Fuse D1 + D2 for one coin. Returns package dict or None.

        D2 is independent — if D1 data is missing, defaults to REJECTED.
        Type B signals (D1 not approved) still proceed.

        Args:
            coin: Trading pair symbol.
            type_e_alerts: Optional list to append Type E conflict alerts to.
        """
        d1 = state_store.get_d1_tier(coin)
        d2 = state_store.get_d2_signal(coin)

        if not d2:
            return None

        # Default D1 to REJECTED if no data (D2 is independent)
        if not d1:
            d1 = {"tier": "REJECTED", "score": 0, "direction": "", "timeframes": {}}

        d1_tier = d1.get("tier", "WATCH")
        d1_score = d1.get("score", 0)
        # Always show real D1 score even for REJECTED tier.
        # The tier "score" is 0 for REJECTED, but real scores (10-30+) live in
        # the per-TF breakdown. Pick the best TF score as the display score.
        d1_tf_scores = [v.get("score", 0) for v in d1.get("timeframes", {}).values()]
        real_d1_score = max(d1_tf_scores) if d1_tf_scores else d1_score
        d1_score = real_d1_score if real_d1_score > 0 else d1_score
        d2_score = float(getattr(d2, 'score', 0))
        d2_tier_name = classify_tier(d2_score)

        # ── Signal Type Classification ─────────────────────────────────
        d1_direction = d1.get("direction", "")
        d2_direction = getattr(d2, 'direction', '')
        nascent_move = getattr(d2, 'nascent_move', False)
        entry_precision = getattr(d2, 'entry_precision', 0.0)

        sig_type = classify_signal_type(
            d1_tier, d1_score, d2_tier_name, d2_score,
            d1_direction, d2_direction, nascent_move, entry_precision
        )

        # No valid signal type (e.g. both REJECTED, or weak D2 without nascent move)
        # — skip this coin, don't pollute D3 output
        if sig_type is None:
            logger.debug(f"[fusion] {coin}: no signal type (d1={d1_tier}/{d1_score:.0f} "
                        f"d2={d2_tier_name}/{d2_score:.0f}) — skipping")
            return None

        # Type E conflict alert (sent to frontend + logged)
        if sig_type == "E" and type_e_alerts is not None:
            alert = {
                "coin": coin,
                "d1_tier": d1_tier,
                "d1_score": d1_score,
                "d1_dir": d1_direction,
                "d2_tier": d2_tier_name,
                "d2_score": d2_score,
                "d2_dir": d2_direction,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            type_e_alerts.append(alert)
            # Push alert to connected clients via WebSocket
            await broadcast({
                "type": "TYPE_E_ALERT",
                "data": alert,
            })

        type_info = SIGNAL_TYPES.get(sig_type, SIGNAL_TYPES.get("D", {}))
        action = type_info.get("action", "WATCH")
        position_mult = TYPE_POSITION_MULT.get(sig_type, 0.0)
        stop_mult = TYPE_STOP_MULT.get(sig_type, 1.5)
        ttl_min = type_info.get("ttl_min", 60)

        # ── Structured Scoring Decision Log ────────────────────────────
        logger.info(
            f"[scoring] coin={coin} sig_type={sig_type or '—'} action={action} "
            f"d1_tier={d1_tier} d1_score={d1_score:.0f} d2_tier={d2_tier_name} "
            f"d2_score={d2_score:.0f} dir_d1={d1_direction or '?'} "
            f"dir_d2={d2_direction or '?'} dirs_align={d1_direction == d2_direction and bool(d1_direction)} "
            f"nascent={nascent_move} ep={entry_precision:.0f} pos_mult={position_mult} "
            f"stop_mult={stop_mult} ttl={ttl_min}min"
        )

        # ── Expected Value Calculation ─────────────────────────────────
        # Per-signal EV using formula: EV = (WinRate × AvgWin) - (LossRate × AvgLoss)
        # Use CONTINUOUS mapping from D2 score (not bucketed) so each signal
        # gets a unique win rate reflecting its actual quality.
        raw_signal = getattr(d2, 'raw_signal', {}) or {}
        rr = float(getattr(d2, 'rr', 1.0) or 1.0)
        # Continuous WR: score 0 = 30%, score 50 = 60%, score 80+ = 78%
        # Anchored at institutional-grade baseline.
        estimated_win_rate = 0.30 + (d2_score / 100.0) * 0.50
        estimated_win_rate = min(0.85, max(0.20, estimated_win_rate))

        # === IMPROVEMENT #5: Session Regime weighting ===
        # Apply session conviction multiplier to win rate
        from backend.session_regime import session_regime
        sig_type_for_session = sig_type or "D"
        regime_mult = session_regime.get_conviction_multiplier(sig_type_for_session, coin)
        regime_info = session_regime.get_session_info(coin)

        # Adjust win rate by regime multiplier (capped between 0.20 and 0.85)
        estimated_win_rate = min(0.85, max(0.20, estimated_win_rate * regime_mult))

        # Avg win/loss derived from RR
        # Assume 1% risk per trade
        risk_amount = 0.01
        avg_win = risk_amount * rr
        avg_loss = risk_amount
        expected_value = calculate_ev(estimated_win_rate, avg_win, avg_loss)
        expected_value_pct = expected_value * 100  # Convert to %

        # ── Package D1 TF breakdown ───────────────────────────────────
        tf_breakdown = {}
        for tf, data in d1.get("timeframes", {}).items():
            tf_breakdown[tf] = {
                "tier": data.get("tier", "WATCH"),
                "score": data.get("score", 0),
            }

        # ── D1 HTF Structure (from best signal in signal_store) ──────
        from backend.config import TIMEFRAMES_HTF
        from backend.signal_store import signal_store as sig_store

        d1_best = None
        d1_best_score = -1
        for htf in TIMEFRAMES_HTF:
            d1_sig = sig_store.get(coin, htf)
            if d1_sig and d1_sig.get("composite_score", 0) > d1_best_score:
                d1_best = d1_sig
                d1_best_score = d1_sig.get("composite_score", 0)

        # Fallback: if signal_store is empty (cold start), use state_store tier
        if not d1_best:
            # Cold start — no signal_store data yet. Use state_store tier info.
            d1_snap = state_store.get_d1_tier(coin)
            if d1_snap and d1_snap.get("tier") not in ("WATCH", "REJECTED"):
                d1_structure = {
                    "direction": d1_snap.get("direction", ""),
                    "tier": d1_snap.get("tier", "WATCH"),
                    "score": d1_snap.get("score", 0),
                    "premium_discount": "EQUILIBRIUM",
                    "ob_zone": "UNKNOWN", "ob_type": "",
                    "ob_low": 0, "ob_high": 0, "ob_strength": 0,
                    "msb_type": "", "msb_level": 0, "msb_direction": "",
                    "fvg_type": "", "fvg_size_atr": 0, "fvg_filled_pct": 100,
                    "liq_swept": False, "liq_level": 0, "liq_direction": "",
                    "poc": 0, "va_high": 0, "va_low": 0,
                    "session": "", "session_label": "",
                }
            else:
                d1_structure = {}
        else:
            d1_ob = d1_best.get("ob", {})
            d1_liq = d1_best.get("liquidity", {})
            d1_msb = d1_best.get("msb", {})
            d1_fvg = d1_best.get("fvg", {})
            d1_vp = d1_best.get("volume_profile", {})

            d1_structure = {
                "direction": d1_best.get("direction", ""),
                "tier": d1_best.get("tier", "WATCH"),
                "score": d1_best.get("composite_score", 0),
                # OB
                "ob_type": d1_ob.get("type", "") if d1_ob else "",
                "ob_zone": d1_ob.get("zone", "UNKNOWN") if d1_ob else "UNKNOWN",
                "ob_low": d1_ob.get("low", 0) if d1_ob else 0,
                "ob_high": d1_ob.get("high", 0) if d1_ob else 0,
                "ob_strength": d1_ob.get("strength", 0) if d1_ob else 0,
                # MSB
                "msb_type": d1_msb.get("type", "") if d1_msb else "",
                "msb_level": d1_msb.get("level", 0) if d1_msb else 0,
                "msb_direction": d1_msb.get("direction", "") if d1_msb else "",
                # FVG
                "fvg_type": d1_fvg.get("type", "") if d1_fvg else "",
                "fvg_size_atr": d1_fvg.get("size_atr", 0) if d1_fvg else 0,
                "fvg_filled_pct": d1_fvg.get("filled_pct", 100) if d1_fvg else 100,
                # Liquidity
                "liq_swept": d1_liq.get("swept", False) if d1_liq else False,
                "liq_level": d1_liq.get("level", 0) if d1_liq else 0,
                "liq_direction": d1_liq.get("direction", "") if d1_liq else "",
                # Volume profile
                "poc": d1_vp.get("poc_price", 0) if d1_vp else 0,
                "va_high": d1_vp.get("va_high", 0) if d1_vp else 0,
                "va_low": d1_vp.get("va_low", 0) if d1_vp else 0,
                # CRT
                "premium_discount": d1_best.get("premium_discount", "EQUILIBRIUM"),
                "session": d1_best.get("session", ""),
                "session_label": d1_best.get("session_label", d1_best.get("session", "")),
            }

        # ── D2 15M Structure (from raw_signal) ────────────────────────
        raw = getattr(d2, 'raw_signal', {}) or {}
        d2_structure = {
            # Scenario
            "scenario": raw.get("scenario", ""),
            "entry_type": raw.get("entry_type", ""),
            "sl_method": raw.get("sl_method", ""),
            # OB
            "ob_type": raw.get("ob", {}).get("type", "") if raw.get("ob") else "",
            "ob_zone": raw.get("ob", {}).get("zone", "UNKNOWN") if raw.get("ob") else "UNKNOWN",
            "ob_low": raw.get("ob", {}).get("low", 0) if raw.get("ob") else 0,
            "ob_high": raw.get("ob", {}).get("high", 0) if raw.get("ob") else 0,
            "ob_strength": raw.get("ob", {}).get("strength", 0) if raw.get("ob") else 0,
            # MSB
            "msb_type": raw.get("msb", {}).get("type", "") if raw.get("msb") else "",
            "msb_level": raw.get("msb", {}).get("level", 0) if raw.get("msb") else 0,
            "msb_direction": raw.get("msb", {}).get("direction", "") if raw.get("msb") else "",
            # FVG
            "fvg_type": raw.get("fvg", {}).get("type", "") if raw.get("fvg") else "",
            "fvg_size_atr": raw.get("fvg", {}).get("size_atr", 0) if raw.get("fvg") else 0,
            "fvg_filled_pct": raw.get("fvg", {}).get("filled_pct", 100) if raw.get("fvg") else 100,
            # Liquidity
            "liq_swept": raw.get("liquidity", {}).get("swept", False) if raw.get("liquidity") else False,
            "liq_level": raw.get("liquidity", {}).get("level", 0) if raw.get("liquidity") else 0,
            "liq_direction": raw.get("liquidity", {}).get("direction", "") if raw.get("liquidity") else "",
            # Volume profile
            "poc": raw.get("volume_profile", {}).get("poc_price", 0) if raw.get("volume_profile") else 0,
            "va_high": raw.get("volume_profile", {}).get("va_high", 0) if raw.get("volume_profile") else 0,
            "va_low": raw.get("volume_profile", {}).get("va_low", 0) if raw.get("volume_profile") else 0,
            # Session
            "session": raw.get("session", ""),
            "session_label": raw.get("session_label", ""),
            # CRT
            "premium_discount": raw.get("premium_discount", "EQUILIBRIUM"),
            "price_position_pct": raw.get("price_position_pct", 50),
            # Displacement
            "displacement_ratio": raw.get("displacement", {}).get("ratio", 0) if raw.get("displacement") else 0,
        }

        # ── Alignment (D1 HTF vs D2 LTF) — explicit level ────────────
        alignment_result = alignment_engine.evaluate(
            d1_structure=d1_structure,
            d2_structure=d2_structure,
            d1_tier=d1_tier,
            d2_tier=d2_tier_name,
            d1_direction=d1_direction,
            d2_direction=d2_direction,
            d1_quality="VALID",
            d2_quality="VALID",
        )
        alignment = alignment_result.to_dict()
        alignment["alignment_score"] = int(alignment_result.score * 20)  # back-compat (0–20)
        alignment_level = alignment_result.level.value

        # ── Phase 12: Signal Provenance — collect D1/D2 evidence IDs ─────
        snap_id = state_store.last_snapshot_id or ""
        from backend.decision_snapshot import _CODE_VERSION, _CONFIG_HASH
        try:
            evidence = __import__("backend.evidence_store", fromlist=["evidence_store"]).evidence_store
            all_evidence = evidence.get_for_snapshot_sync(snap_id) if snap_id else {}
            d1_evidence_ids = [
                r.evidence_id
                for by_cat in all_evidence.get(coin, {}).values()
                for r in by_cat
                if r.timeframe in ("1H", "4H", "1D")
            ]
            d2_evidence_ids = [
                r.evidence_id
                for by_cat in all_evidence.get(coin, {}).values()
                for r in by_cat
                if r.timeframe == "15M"
            ]
        except Exception:
            d1_evidence_ids = []
            d2_evidence_ids = []
        alignment_id = f"aln-{snap_id[:8]}-{coin[:6]}" if snap_id else ""

        # ── SSL/BSL levels ────────────────────────────────────────────
        liq_pools = raw.get("liquidity_pools", {}) or {}
        d2_structure["ssl"] = _extract_ssl(liq_pools, getattr(d2, 'direction', 'BULLISH'))
        d2_structure["bsl"] = _extract_bsl(liq_pools, getattr(d2, 'direction', 'BULLISH'))

        # ── SL override: use D1 structural levels if tighter ─────────────
        # D2 SL comes from 15M structure + 0.3x ATR buffer.
        # If D1 has a closer OB/MSB level in the right direction,
        # that HTF level makes a better SL anchor.
        d2_sl = getattr(d2, 'sl', 0)
        d2_entry = getattr(d2, 'entry', 0)
        d2_dir = getattr(d2, 'direction', 'BULLISH')
        if d2_sl and d2_entry and d1_structure:
            d1_sl = _d1_structural_sl(d1_structure, d2_entry, d2_dir, d2_sl)
            if d1_sl:
                d2.sl = d1_sl
                # Recalculate TPs from new SL distance
                risk = abs(d2_entry - d2_sl)
                new_risk = abs(d2_entry - d1_sl)
                if new_risk > 0 and risk > 0:
                    scale = new_risk / risk
                    d2.tp1 = d2_entry + (d2.tp1 - d2_entry) * scale if d2_dir == 'BULLISH' else d2_entry - (d2_entry - d2.tp1) * scale
                    d2.tp2 = d2_entry + (d2.tp2 - d2_entry) * scale if d2_dir == 'BULLISH' else d2_entry - (d2_entry - d2.tp2) * scale
                    d2.rr1 = round(abs(d2.tp1 - d2_entry) / new_risk, 2)
                    d2.rr2 = round(abs(d2.tp2 - d2_entry) / new_risk, 2)

        # ── Build package ─────────────────────────────────────────────
        package = {
            "snapshot_id": snap_id,
            "signal_id": getattr(d2, 'signal_id', ''),
            # Phase 12: Signal Provenance chain
            "code_version": _CODE_VERSION,
            "config_hash": _CONFIG_HASH,
            "d1_evidence_ids": d1_evidence_ids,
            "d2_evidence_ids": d2_evidence_ids,
            "alignment_id": alignment_id,
            "trade_plan_id": "",    # populated after trade_plan_authority.propose()
            "risk_decision_id": "", # populated after risk_authority.review()
            "coin": coin,
            "timeframe": "15M",
            "direction": getattr(d2, 'direction', 'BULLISH'),
            # Signal Type (new Decision Layer)
            "signal_type": sig_type or "—",
            "signal_type_name": type_info.get("name", "—"),
            "signal_type_icon": type_info.get("icon", ""),
            "signal_type_color": type_info.get("color", "#6b7280"),
            "action": action,
            # Position sizing
            "position_mult": position_mult,
            "stop_mult": stop_mult,
            "ttl_min": ttl_min,
            # Scores
            "d2_score": round(d2_score, 1),
            "d2_tier": d2_tier_name,
            "d1_tier": d1_tier,
            "d1_score": round(d1_score, 1),
            # Structure
            "d1_timeframes": tf_breakdown,
            "d1_structure": d1_structure,
            "d2_structure": d2_structure,
            # Alignment
            "alignment": alignment,
            # Trade data
            "entry": getattr(d2, 'entry', 0),
            "sl": getattr(d2, 'sl', 0),
            "tp1": getattr(d2, 'tp1', 0),
            "tp2": getattr(d2, 'tp2', 0),
            "rr1": round(getattr(d2, 'rr1', 0), 2),
            "rr2": round(getattr(d2, 'rr2', 0), 2),
            # Expected Value
            "expected_value": round(expected_value, 4),
            "expected_value_pct": round(expected_value_pct, 2),
            "estimated_win_rate": round(estimated_win_rate * 100, 1),
            # Session regime info (Improvement #5)
            "session_regime": regime_info,
            "regime_mult": regime_mult,
            # Metadata
            "freshness": getattr(d2, 'freshness', 'HOT'),
            "score_history": list(getattr(d2, 'score_history', []))[-10:],
            "born_at": getattr(d2, 'born_at', datetime.now(timezone.utc)).isoformat(),
            "last_scan": getattr(d2, 'last_scan', datetime.now(timezone.utc)).isoformat(),
            # Nascent move flag
            "nascent_move": nascent_move,
            "entry_precision": entry_precision,
            # D2 sub-scores (for frontend sorting and breakdown)
            "momentum_score": round(float(getattr(d2, 'momentum_score', 0)), 1),
            "flow_score": round(float(getattr(d2, 'flow_score', 0)), 1),
            "htf_bonus": round(float(getattr(d2, 'htf_bonus', 0)), 1),
        }

        # ── Market Evolution Engine (16-state matrix) ─────────────────
        me_state = me_evaluate(
            coin,
            d1_tier, d1_score,
            d2_tier_name, d2_score,
            direction=package["direction"],
            alignment_score=alignment_result.score,
            signal_type=sig_type or "",
        )
        package["marketEvolution"] = me_state.to_dict()

        # ── TradePlan Authority (single source of truth for plan) ──────
        d2_dir = getattr(d2, 'direction', 'BULLISH')
        d2_entry = getattr(d2, 'entry', 0)
        d2_sl = getattr(d2, 'sl', 0)
        d2_atr = float(getattr(d2, 'atr', 0.0)) or (abs(d2_entry - d2_sl) * 2) if d2_entry and d2_sl else 0
        d2_ob_low = d2_structure.get("ob_low", 0)
        d2_ob_high = d2_structure.get("ob_high", 0)
        d1_ob_low = d1_structure.get("ob_low", 0)
        d1_ob_high = d1_structure.get("ob_high", 0)

        confidence_score = round(alignment_result.score, 3)
        if sig_type == "C":
            confidence_score = min(1.0, confidence_score + 0.15)
        elif sig_type == "A":
            confidence_score = min(1.0, confidence_score + 0.10)
        elif sig_type == "E":
            confidence_score = max(0.0, confidence_score - 0.20)

        plan = trade_plan_authority.propose(
            symbol=coin,
            direction=d2_dir,
            entry=d2_entry,
            atr=d2_atr if d2_atr > 0 else 0.0001,
            d1_zone=d1_structure.get("premium_discount", "EQUILIBRIUM"),
            d2_zone=d2_structure.get("premium_discount", "EQUILIBRIUM"),
            ob_low=d2_ob_low or d1_ob_low,
            ob_high=d2_ob_high or d1_ob_high,
            signal_type=sig_type or "D",
            confidence_score=confidence_score,
            alignment_level=alignment_level,
            signal_id=getattr(d2, 'signal_id', ''),
            d1_sl=d1_ob_low if d2_dir == "BULLISH" else d1_ob_high,
            d2_sl=d2_sl,
        )

        # ── Risk Authority (independent risk approval) ────────────────
        risk_decision = risk_authority.review(plan, correlation_group=coin[:3])

        # Phase 12: Wire plan/risk decision IDs back into provenance chain
        plan_id = getattr(plan, 'plan_id', '') or f"plan-{snap_id[:8]}-{coin[:6]}"
        risk_id = getattr(risk_decision, 'decision_id', '') or f"risk-{snap_id[:8]}-{coin[:6]}"

        package["trade_plan"] = plan.to_dict()
        package["trade_plan_id"] = plan_id
        package["risk_decision_id"] = risk_id
        package["risk_decision"] = {
            "verdict": risk_decision.verdict.value,
            "approved_size": risk_decision.approved_size,
            "portfolio_heat": round(risk_decision.portfolio_heat, 4),
            "rationale": risk_decision.rationale,
        }
        package["alignment_level"] = alignment_level
        package["tradeable"] = alignment_result.is_tradeable() and risk_decision.verdict.value == "APPROVED"

        await state_store.set_d3_decision(coin, package)
        await broadcast({"type": "signal", "data": package})

        # Phase 22: persist decision to SQLite
        _persist_decision(coin, sig_type, package)

        logger.debug(f"[fusion] {coin}: Type {sig_type or '—'} "
                      f"D1={d1_tier}({d1_score:.0f}) D2={d2_score:.0f} "
                      f"dir={package['direction']} EV={expected_value_pct:.2f}%")
        return package


def _extract_ssl(liq_pools: dict, direction: str) -> dict:
    """Extract Swing Low Level (SSL) — key support below price for bullish setups."""
    if not liq_pools or not liq_pools.get("pools"):
        return {"level": 0, "touches": 0, "swept": False}

    pools = sorted(liq_pools["pools"], key=lambda p: p.get("level", 0))
    for pool in pools:
        if pool.get("level", 0) > 0:
            return {
                "level": pool.get("level", 0),
                "touches": pool.get("touches", 0),
                "swept": pool.get("swept", False),
            }
    return {"level": 0, "touches": 0, "swept": False}


def _extract_bsl(liq_pools: dict, direction: str) -> dict:
    """Extract Buy/Sell Level — key resistance above price for bearish setups."""
    if not liq_pools or not liq_pools.get("pools"):
        return {"level": 0, "touches": 0, "swept": False}

    pools = sorted(liq_pools["pools"], key=lambda p: p.get("level", 0), reverse=True)
    for pool in pools:
        if pool.get("level", 0) > 0:
            return {
                "level": pool.get("level", 0),
                "touches": pool.get("touches", 0),
                "swept": pool.get("swept", False),
            }
    return {"level": 0, "touches": 0, "swept": False}


def _d1_structural_sl(d1: dict, entry: float, direction: str, current_sl: float) -> float | None:
    """Check D1 HTF structural levels for a tighter SL than D2's.

    For BULLISH: look for OB low, MSB swing low, or FVG bottom below entry.
    For BEARISH: look for OB high, MSB swing high, or FVG top above entry.
    Returns the improved SL or None if D2 SL is already tighter.
    """
    if not d1 or not entry:
        return None

    if direction == "BULLISH":
        # Must be below entry
        candidates = []
        ob_low = d1.get("ob_low", 0)
        if ob_low and ob_low < entry:
            candidates.append(ob_low)
        msb_level = d1.get("msb_level", 0)
        if msb_level and msb_level < entry:
            candidates.append(msb_level)
        # FVG bottom (size_atr is approximate; use entry - fvg_size_atr * entry)
        fvg_size = d1.get("fvg_size_atr", 0)
        if fvg_size and fvg_size > 0:
            fvg_bot = entry * (1 - fvg_size / 100)
            if fvg_bot < entry:
                candidates.append(fvg_bot)
        if not candidates:
            return None
        best = max(c for c in candidates if c < entry)  # closest to entry
        current_dist = entry - current_sl
        new_dist = entry - best
        # Only use D1 SL if it's tighter (smaller distance) and reasonable
        if 0 < new_dist < current_dist and new_dist < entry * 0.04:
            return best

    else:  # BEARISH
        candidates = []
        ob_high = d1.get("ob_high", 0)
        if ob_high and ob_high > entry:
            candidates.append(ob_high)
        msb_level = d1.get("msb_level", 0)
        if msb_level and msb_level > entry:
            candidates.append(msb_level)
        fvg_size = d1.get("fvg_size_atr", 0)
        if fvg_size and fvg_size > 0:
            fvg_top = entry * (1 + fvg_size / 100)
            if fvg_top > entry:
                candidates.append(fvg_top)
        if not candidates:
            return None
        best = min(c for c in candidates if c > entry)  # closest to entry
        current_dist = current_sl - entry
        new_dist = best - entry
        if 0 < new_dist < current_dist and new_dist < entry * 0.04:
            return best

    return None


# Module-level singleton
fusion_engine = FusionEngine()
