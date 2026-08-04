"""Dimension 3 — Fusion Engine.

Reads D1 tiers + D2 scores from state_store, assigns buckets
via a 3x3 grid (SNIPER/OPPORTUNITY/WATCH × SNIPER/OPPORTUNITY/WATCH),
packages for frontend, pushes via WebSocket.

No sensitivity modes — fixed thresholds for both D1 and D2.
All 9 combos produce a bucket; REJECTED on either dimension → IGNORE.

3×3 Bucket Matrix:
  D1 \ D2      | SNIPER        | OPPORTUNITY    | WATCH
  -------------|---------------|----------------|--------
  SNIPER       | 🟢 READY      | 🔵 BUILDING    | ⏳ WAIT
  OPPORTUNITY  | 🟡 EARLY      | 🟠 DEVELOPING  | 👀 MONITOR
  WATCH        | 🔴 TRAP       | ⚫ IGNORE      | ⚫ IGNORE
"""
import asyncio
import logging
from datetime import datetime, timezone
from backend.config import (
    TIER_SNIPER_SCORE,
    TIER_OPPORTUNITY_SCORE,
    TIER_WATCH_SCORE,
    D2_SIGNAL_TTL_MINUTES,
)
from backend.state_store import state_store
from backend.ws_hub import broadcast, get_initial_payload
from backend.market_evolution import evaluate as me_evaluate, get_dashboard_stats
from backend.market_evolution.history import history_store

logger = logging.getLogger("judah.fusion")

# ── Bucket definitions ──────────────────────────────────────────────

BUCKETS = [
    "READY", "BUILDING", "WAIT",
    "EARLY", "DEVELOPING", "MONITOR",
    "TRAP", "IGNORE", "IGNORE",
]

BUCKET_LABELS = {
    "READY": "Ready", "BUILDING": "Building", "WAIT": "Wait",
    "EARLY": "Early", "DEVELOPING": "Developing", "MONITOR": "Monitor",
    "TRAP": "Trap", "IGNORE": "Ignore",
}

BUCKET_COLORS = {
    "READY":    "#22c55e",  # green
    "EARLY":    "#eab308",  # amber
    "BUILDING": "#3b82f6",  # blue
    "DEVELOPING":"#f97316", # orange
    "WAIT":     "#fbbf24",  # yellow
    "MONITOR":  "#a855f7",  # purple
    "TRAP":     "#ef4444",  # red
    "IGNORE":   "#6b7280",  # dark gray
}

BUCKET_ICONS = {
    "READY": "🟢", "BUILDING": "🔵", "WAIT": "⏳",
    "EARLY": "🟡", "DEVELOPING": "🟠", "MONITOR": "👀",
    "TRAP": "🔴", "IGNORE": "⚫",
}

def classify_tier(score: float) -> str:
    """Classify a score into SNIPER / OPPORTUNITY / WATCH / REJECTED."""
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


def bucket(d1_score: float, d2_score: float) -> str:
    """3×3 bucket grid from D1 and D2 scores.

    REJECTED on either dimension → IGNORE.
    """
    d1 = classify_tier(d1_score)
    d2 = classify_tier(d2_score)

    # REJECTED → always IGNORE
    if d1 == "REJECTED" or d2 == "REJECTED":
        return "IGNORE"

    grid = {
        ("SNIPER",      "SNIPER"):      "READY",
        ("OPPORTUNITY", "SNIPER"):      "EARLY",
        ("WATCH",       "SNIPER"):      "TRAP",
        ("SNIPER",      "OPPORTUNITY"): "BUILDING",
        ("OPPORTUNITY", "OPPORTUNITY"): "DEVELOPING",
        ("WATCH",       "OPPORTUNITY"): "IGNORE",
        ("SNIPER",      "WATCH"):       "WAIT",
        ("OPPORTUNITY", "WATCH"):       "MONITOR",
        ("WATCH",       "WATCH"):       "IGNORE",
    }
    return grid.get((d1, d2), "IGNORE")


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
        logger.info("[fusion] D3 Fusion Engine started (9-bucket grid, no sensitivity mode)")

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

    async def _check_and_fuse(self):
        """Check if D1 or D2 has new data, fuse if so."""
        last_d1 = state_store.last_d1_scan
        last_d2 = state_store.last_d2_scan

        if last_d1 == self._last_d1_scan and last_d2 == self._last_d2_scan:
            return

        self._last_d1_scan = last_d1
        self._last_d2_scan = last_d2

        active = state_store.get_active_coins()
        d2_all = state_store.get_all_d2_signals()
        d2_coins = set(d2_all.keys())
        active_set = set(active)
        overlap = d2_coins & active_set

        logger.info(f"[fusion] Active={len(active)} D2={len(d2_all)} overlap={len(overlap)}")

        results = []
        skip_no_d1 = 0
        skip_no_d2 = 0
        for coin in active:
            d1 = state_store.get_d1_tier(coin)
            d2 = state_store.get_d2_signal(coin)
            if not d1:
                skip_no_d1 += 1
                continue
            if not d2:
                skip_no_d2 += 1
                continue
            pkg = await self._fuse_coin(coin)
            if pkg:
                results.append(pkg)

        if results:
            logger.info(f"[fusion] Fused {len(results)} from {len(active)} active coins")
        elif active:
            logger.info(f"[fusion] 0 fused — {skip_no_d1} no-D1, {skip_no_d2} no-D2")

    async def _fuse_coin(self, coin: str):
        """Fuse D1 + D2 for one coin. Returns package dict or None."""
        d1 = state_store.get_d1_tier(coin)
        d2 = state_store.get_d2_signal(coin)

        if not d1 or not d2:
            return None

        d1_tier = d1.get("tier", "WATCH")
        d1_score = d1.get("score", 0)
        d2_score = float(getattr(d2, 'score', 0))
        d2_tier_name = classify_tier(d2_score)

        # Bucket assignment
        b = bucket(d1_score, d2_score)

        # Package D1 TF breakdown
        tf_breakdown = {}
        for tf, data in d1.get("timeframes", {}).items():
            tf_breakdown[tf] = {
                "tier": data.get("tier", "WATCH"),
                "score": data.get("score", 0),
            }

        # ── D1 HTF Structure (from signal_store) ─────────────────────────
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
            }

        # ── D2 15M Structure (from raw_signal) ──────────────────────────
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

        # ── SSL/BSL levels ───────────────────────────────────────────────
        # SSL = swing low support (bullish SL anchor)
        # BSL = swing high resistance (bearish SL anchor)
        liq_pools = raw.get("liquidity_pools", {}) or {}
        d2_structure["ssl"] = _extract_ssl(liq_pools, getattr(d2, 'direction', 'BULLISH'))
        d2_structure["bsl"] = _extract_bsl(liq_pools, getattr(d2, 'direction', 'BULLISH'))

        # ── Build package ───────────────────────────────────────────────
        package = {
            "signal_id": getattr(d2, 'signal_id', ''),
            "coin": coin,
            "timeframe": "15M",
            "direction": getattr(d2, 'direction', 'BULLISH'),
            "bucket": b,
            "bucket_label": BUCKET_LABELS.get(b, b),
            "bucket_icon": BUCKET_ICONS.get(b, ""),
            "bucket_color": BUCKET_COLORS.get(b, "#6b7280"),
            "d2_score": round(d2_score, 1),
            "d2_tier": d2_tier_name,
            "d1_tier": d1_tier,
            "d1_score": round(d1_score, 1),
            "d1_timeframes": tf_breakdown,
            "d1_structure": d1_structure,
            "d2_structure": d2_structure,
            "entry": getattr(d2, 'entry', 0),
            "sl": getattr(d2, 'sl', 0),
            "tp1": getattr(d2, 'tp1', 0),
            "tp2": getattr(d2, 'tp2', 0),
            "rr1": round(getattr(d2, 'rr1', 0), 2),
            "rr2": round(getattr(d2, 'rr2', 0), 2),
            "freshness": getattr(d2, 'freshness', 'HOT'),
            "score_history": list(getattr(d2, 'score_history', []))[-10:],
            "born_at": getattr(d2, 'born_at', datetime.now(timezone.utc)).isoformat(),
            "last_scan": getattr(d2, 'last_scan', datetime.now(timezone.utc)).isoformat(),
        }

        # ── Market Evolution Engine (16-state matrix) ─────────────────
        # The new primary object. Replaces D3 buckets as the
        # single source of truth the frontend consumes.
        me_state = me_evaluate(
            coin,
            d1_tier, d1_score,
            d2_tier_name, d2_score,
            direction=package["direction"],
        )
        package["marketEvolution"] = me_state.to_dict()

        await state_store.set_d3_fusion(coin, package)
        await broadcast({"type": "signal", "data": package})

        logger.debug(f"[fusion] {coin}: {b} D1={d1_tier}({d1_score:.0f}) D2={d2_score:.0f} dir={package['direction']}")
        return package


def _extract_ssl(liq_pools: dict, direction: str) -> dict:
    """Extract Swing Low Level (SSL) — key support below price for bullish setups."""
    if not liq_pools or not liq_pools.get("pools"):
        return {"level": 0, "touches": 0, "swept": False}

    pools = sorted(liq_pools["pools"], key=lambda p: p.get("level", 0))
    # For bullish: lowest swing low with most touches
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
    # For bearish: highest swing high with most touches
    for pool in pools:
        if pool.get("level", 0) > 0:
            return {
                "level": pool.get("level", 0),
                "touches": pool.get("touches", 0),
                "swept": pool.get("swept", False),
            }
    return {"level": 0, "touches": 0, "swept": False}


# Module-level singleton
fusion_engine = FusionEngine()
