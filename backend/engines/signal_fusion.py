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
)
from backend.state_store import state_store
from backend.ws_hub import broadcast, get_initial_payload
from backend.market_evolution import evaluate as me_evaluate, get_dashboard_stats
from backend.market_evolution.history import history_store
from backend.signal_history import signal_history

logger = logging.getLogger("judah.fusion")

# ── Signal Type Definitions ────────────────────────────────────────

SIGNAL_TYPES = {
    "A": {"name": "HTF Structure",   "color": "#eab308", "icon": "🟡", "action": "EXECUTE", "ttl_min": 120},
    "B": {"name": "LTF Momentum",    "color": "#3b82f6", "icon": "🔵", "action": "EXECUTE", "ttl_min": 15},
    "C": {"name": "Full Confluence", "color": "#22c55e", "icon": "🟢", "action": "EXECUTE", "ttl_min": 240},
    "D": {"name": "HTF Early Warn",  "color": "#f97316", "icon": "🟠", "action": "WATCH",   "ttl_min": 60},
    "E": {"name": "Conflict/Trap",   "color": "#ef4444", "icon": "🔴", "action": "ALERT",   "ttl_min": 0},
}

# Position size multipliers by signal type
TYPE_POSITION_MULT = {"A": 0.75, "B": 0.35, "C": 1.0, "D": 0.0, "E": 0.0}

# Stop width multipliers by signal type
TYPE_STOP_MULT = {"A": 1.5, "B": 1.0, "C": 1.5, "D": 1.5, "E": 1.5}

# Decay rates per signal type (per 5-min interval)
DECAY_TYPE_A = 0.94
DECAY_TYPE_C = 0.98


def classify_tier(score: float) -> str:
    """Classify a score into SNIPER / OPPORTUNITY / WATCH / WEAK / REJECTED."""
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    if score >= TIER_WEAK_SCORE:
        return "WEAK"
    return "REJECTED"


def calculate_ev(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Calculate Expected Value per trade: EV = (Win_Rate × Avg_Win) - (Loss_Rate × Avg_Loss)."""
    loss_rate = 1.0 - win_rate
    return (win_rate * avg_win) - (loss_rate * avg_loss)


def classify_signal_type(d1_tier: str, d1_score: float, d2_tier: str, d2_score: float,
                          d1_direction: str, d2_direction: str,
                          nascent_move: bool = False, entry_precision: float = 0.0) -> str | None:
    """Decision Layer: classify signal into Type A/B/C/D/E or None.

    Classification order (first match wins):
    1. Type C: D1 SNIPER (>=85) AND D2 SNIPER (>=85) AND directions align
    2. Type A: D1 >= 70 AND D2 >= 50 AND directions align
    3. Type B: D1 NOT approved AND D2 >= 72 AND nascent_move AND Entry Precision >= 18
    4. Type D: D1 >= 70 AND D2 not aligned
    5. Type E: Both valid but opposing directions
    6. None: everything else
    """
    d1_approved = d1_tier in ("SNIPER", "OPPORTUNITY")
    d1_sniper = d1_score >= 85
    d2_sniper = d2_score >= 85
    d1_opp_or_above = d1_score >= 70
    d2_min_b = d2_score >= TYPE_B_MIN_D2_SCORE
    directions_align = d1_direction == d2_direction and d1_direction != ""
    ep_gate = entry_precision >= TYPE_B_ENTRY_PRECISION_GATE

    # Type C: both SNIPER on both sides (highest conviction)
    if d1_sniper and d2_sniper and directions_align:
        return "C"

    # Type A: D1 approved + D2 moderate confirmation
    if d1_approved and d2_score >= 50 and directions_align:
        return "A"

    # Type B: D1 not approved, D2 LTF momentum play
    if not d1_approved and d2_min_b and nascent_move and ep_gate:
        return "B"

    # Type E: both valid but opposing directions (check before Type D — more specific)
    if d1_approved and d2_tier in ("SNIPER", "OPPORTUNITY") and not directions_align:
        return "E"

    # Type D: D1 approved but D2 not aligned (general case)
    if d1_opp_or_above and not directions_align and d2_tier != "REJECTED":
        return "D"

    # No signal
    return None


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
        """Move D3 decisions whose D2 signal has expired into history.

        D2 removes signals at 15-min TTL (ltf_engine PASS 1). D3 must catch
        the orphaned decisions before they become stale in the live feed.

        Expired decisions go to signal_history (2h retention) so the
        frontend can render them in a "Recent History" section.
        """
        archived = []
        signal_ids_to_remove = []
        d2_coins = set(state_store.get_all_d2_signals().keys())

        for coin, decision in list(state_store.d3_decisions.items()):
            if coin not in d2_coins:
                signal_id = decision.get("signal_id", "")
                reason = "ttl_expired"
                # Check if D1 also dropped this coin
                d1 = state_store.get_d1_tier(coin)
                if not d1:
                    reason = "d1_dropped"
                signal_history.add(decision, expiry_reason=reason)
                signal_ids_to_remove.append(signal_id)
                archived.append(coin)

        # Remove from active D3 decisions AFTER collecting signal_ids
        async with state_store._lock:
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

        if last_d1 == self._last_d1_scan and last_d2 == self._last_d2_scan:
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
        raw_signal = getattr(d2, 'raw_signal', {}) or {}
        rr = getattr(d2, 'rr', 1.0)
        # Estimate win rate from score: higher score → higher win rate
        # SNIPER(85+) = 75%, OPPORTUNITY(65+) = 60%, WATCH(40+) = 45%
        if d2_score >= 85:
            estimated_win_rate = 0.75
        elif d2_score >= 65:
            estimated_win_rate = 0.60
        elif d2_score >= 40:
            estimated_win_rate = 0.45
        else:
            estimated_win_rate = 0.35

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

        # ── D1 HTF Structure (from signal_store) ─────────────────────
        from backend.config import TIMEFRAMES_HTF
        from backend.signal_store import signal_store as sig_store

        d1_best = None
        d1_best_score = -1
        for htf in TIMEFRAMES_HTF:
            d1_sig = sig_store.get(coin, htf)
            if d1_sig and d1_sig.get("composite_score", 0) > d1_best_score:
                d1_best = d1_sig
                d1_best_score = d1_sig.get("composite_score", 0)

        # D1 structural summary
        d1_structure = {}
        if d1_best:
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

        # ── Alignment (D1 HTF vs D2 LTF) ──────────────────────────────
        alignment = _compute_alignment(d1_structure, d2_structure, d1, d2)

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
            "signal_id": getattr(d2, 'signal_id', ''),
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
            # Metadata
            "freshness": getattr(d2, 'freshness', 'HOT'),
            "score_history": list(getattr(d2, 'score_history', []))[-10:],
            "born_at": getattr(d2, 'born_at', datetime.now(timezone.utc)).isoformat(),
            "last_scan": getattr(d2, 'last_scan', datetime.now(timezone.utc)).isoformat(),
            # Nascent move flag
            "nascent_move": nascent_move,
            "entry_precision": entry_precision,
        }

        # ── Market Evolution Engine (16-state matrix) ─────────────────
        me_state = me_evaluate(
            coin,
            d1_tier, d1_score,
            d2_tier_name, d2_score,
            direction=package["direction"],
            alignment_score=alignment.get("alignment_score", 0),
            signal_type=sig_type or "",
        )
        package["marketEvolution"] = me_state.to_dict()

        await state_store.set_d3_decision(coin, package)
        await broadcast({"type": "signal", "data": package})

        logger.debug(f"[fusion] {coin}: Type {sig_type or '—'} "
                      f"D1={d1_tier}({d1_score:.0f}) D2={d2_score:.0f} "
                      f"dir={package['direction']} EV={expected_value_pct:.2f}%")
        return package


def _compute_alignment(d1s: dict, d2s: dict, d1: dict, d2: Any) -> dict:
    """Compute HTF/LTF alignment between D1 and D2 structures.

    Returns alignment dict with score (0-20) and 4 boolean components.
    """
    components = {
        "direction_agreement": False,
        "htf_ob_alignment": False,
        "htf_zone_alignment": False,
        "htf_liquidity_proximity": False,
    }
    score = 0

    # 1. Direction agreement (0-5 pts)
    d1_dir = (d1.get("direction") or "").upper()
    d2_dir = (getattr(d2, 'direction', '') or "").upper()
    if d1_dir and d2_dir and d1_dir == d2_dir:
        components["direction_agreement"] = True
        score += 5

    # 2. HTF OB alignment — D2 entry near D1 OB zone (0-5 pts)
    d1_ob_zone = (d1s.get("ob_zone") or "").upper()
    d2_ob_zone = (d2s.get("ob_zone") or "").upper()
    if d1_ob_zone and d2_ob_zone and d1_ob_zone == d2_ob_zone:
        components["htf_ob_alignment"] = True
        score += 5

    # 3. HTF zone alignment — both in same premium/discount zone (0-5 pts)
    d1_pd = (d1s.get("premium_discount") or "").upper()
    d2_pd = (d2s.get("premium_discount") or "").upper()
    if d1_pd and d2_pd and d1_pd == d2_pd and d1_pd != "UNKNOWN":
        components["htf_zone_alignment"] = True
        score += 5

    # 4. HTF liquidity proximity — D2 near D1 swept liquidity level (0-5 pts)
    d1_liq_swept = d1s.get("liq_swept", False)
    d2_liq_level = d2s.get("liq_level", 0)
    if d1_liq_swept and d2_liq_level > 0:
        components["htf_liquidity_proximity"] = True
        score += 5

    return {
        "alignment_score": min(score, 20),
        "components": components,
    }


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
