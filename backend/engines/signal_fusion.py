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

        # Build package
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

        await state_store.set_d3_fusion(coin, package)
        await broadcast({"type": "signal", "data": package})

        logger.debug(f"[fusion] {coin}: {b} D1={d1_tier}({d1_score:.0f}) D2={d2_score:.0f} dir={package['direction']}")
        return package


# Module-level singleton
fusion_engine = FusionEngine()
