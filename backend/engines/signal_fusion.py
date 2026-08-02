"""Dimension 3 — Fusion Engine.

Reads D1 tiers + D2 signals from state_store, assigns buckets,
packages for frontend, pushes via WebSocket.

D3 is completely decoupled from D1 and D2:
  - No imports from scanner.py or ltf_engine.py
  - Own async scan loop — watches state_store timestamps for changes
  - Fires fusion when D1 or D2 data is updated
  - Pushes to frontend via ws_hub (itself decoupled from main.py)

Buckets:
  READY       — D1 SNIPER      + D2 SNIPER      | Execute with normal risk
  EARLY       — D1 OPPORTUNITY + D2 SNIPER      | Execute with reduced risk
  TRAP        — D1 WATCH       + D2 SNIPER      | High caution / Tight SL
  BUILDING    — D1 SNIPER      + D2 OPPORTUNITY | Wait patiently
  DEVELOPING  — D1 OPPORTUNITY + D2 OPPORTUNITY | Observe only
  IGNORE      — D1 WATCH       + D2 OPPORTUNITY | Do not trade
  FILTERED    — D2 score below threshold           | Not sent to frontend
"""
import asyncio
import logging
from datetime import datetime, timezone
from backend.config import (
    TIER_SNIPER_SCORE,
    TIER_OPPORTUNITY_SCORE,
    TIER_WATCH_SCORE,
    D2_SIGNAL_TTL_MINUTES,
    D2_SENSITIVITY_MODE,
    D2_MIN_SCORE_STRICT,
    D2_MIN_SCORE_BALANCED,
    D2_MIN_SCORE_EXPLORATION,
    D2_MIN_SCORE_DEBUG,
)
from backend.state_store import state_store
from backend.ws_hub import broadcast, get_initial_payload

logger = logging.getLogger("judah.fusion")


def resolve_d2_threshold() -> int:
    """Return the D2 minimum score for the current sensitivity mode.

    Reads directly from the config module so runtime mutations
    (via POST /api/d2-mode) take effect immediately.
    """
    import backend.config as cfg
    return {
        "STRICT": cfg.D2_MIN_SCORE_STRICT,
        "BALANCED": cfg.D2_MIN_SCORE_BALANCED,
        "EXPLORATION": cfg.D2_MIN_SCORE_EXPLORATION,
        "DEBUG": cfg.D2_MIN_SCORE_DEBUG,
    }.get(cfg.D2_SENSITIVITY_MODE, cfg.D2_MIN_SCORE_STRICT)


def d2_tier(score: float, threshold: int = None) -> str:
    """Classify a D2 score into SNIPER/OPPORTUNITY using the SAME fixed
    thresholds as D1. Sensitivity mode is for frontend display only."""
    if score >= TIER_SNIPER_SCORE:
        return "SNIPER"
    if score >= TIER_OPPORTUNITY_SCORE:
        return "OPPORTUNITY"
    if score >= TIER_WATCH_SCORE:
        return "WATCH"
    return "REJECTED"


# ── Bucket logic (pure function, no side effects) ──────────────────

def bucket(d1_tier: str, d2_score: float) -> str:
    """6-bucket grid: D1 tier × D2 tier → bucket.

    Both D1 and D2 use the same fixed tier thresholds.
    """
    d2 = d2_tier(d2_score)

    if d2 == "REJECTED":
        return "FILTERED"

    grid = {
        ("SNIPER",      "SNIPER"):      "READY",
        ("OPPORTUNITY", "SNIPER"):      "EARLY",
        ("WATCH",       "SNIPER"):      "TRAP",
        ("SNIPER",      "OPPORTUNITY"): "BUILDING",
        ("OPPORTUNITY", "OPPORTUNITY"): "DEVELOPING",
        ("WATCH",       "OPPORTUNITY"): "IGNORE",
    }
    return grid.get((d1_tier, d2), "FILTERED")


def bucket_label(b: str) -> str:
    return {
        "READY": "Ready", "EARLY": "Early", "TRAP": "Trap",
        "BUILDING": "Building", "DEVELOPING": "Developing",
        "IGNORE": "Ignore", "FILTERED": "Filtered",
    }.get(b, b)


def bucket_color(b: str) -> str:
    return {
        "READY": "#22c55e", "EARLY": "#eab308", "TRAP": "#ef4444",
        "BUILDING": "#3b82f6", "DEVELOPING": "#9ca3af",
        "IGNORE": "#6b7280", "FILTERED": "#374151",
    }.get(b, "#6b7280")


# ── Fusion Engine ──────────────────────────────────────────────────

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
        logger.info("[fusion] D3 Fusion Engine started")

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
            await asyncio.sleep(2)  # Check every 2 seconds

    async def _check_and_fuse(self):
        """Check if D1 or D2 has new data, fuse if so."""
        last_d1 = state_store.last_d1_scan
        last_d2 = state_store.last_d2_scan

        if last_d1 == self._last_d1_scan and last_d2 == self._last_d2_scan:
            return  # No changes

        self._last_d1_scan = last_d1
        self._last_d2_scan = last_d2

        # Fuse all active coins
        active = state_store.get_active_coins()
        d2_all = state_store.get_all_d2_signals()
        d2_coins = set(d2_all.keys())
        active_set = set(active)
        overlap = d2_coins & active_set
        only_d2 = d2_coins - active_set

        logger.info(f"[fusion] Active={len(active)} D2={len(d2_all)} "
                    f"overlap={len(overlap)} D2-only={len(only_d2)} "
                    f"threshold={resolve_d2_threshold()}")

        results = []
        skip_no_d1 = 0
        skip_no_d2 = 0
        skip_filtered = 0
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
            else:
                skip_filtered += 1

        if results:
            logger.info(f"[fusion] Fused {len(results)} signals from {len(active)} active coins")
        elif active:
            logger.info(f"[fusion] 0 fused — {skip_no_d1} no-D1, {skip_no_d2} no-D2, {skip_filtered} filtered")

    async def _fuse_coin(self, coin: str):
        """Fuse D1 + D2 for one coin. Returns package dict or None."""
        d1 = state_store.get_d1_tier(coin)
        d2 = state_store.get_d2_signal(coin)

        if not d1 or not d2:
            return None

        d1_tier = d1.get("tier", "WATCH")
        d1_score = d1.get("score", 0)
        d2_score = float(getattr(d2, 'score', 0))
        d2_display_tier = d2_tier(d2_score)

        # Bucket assignment
        b = bucket(d1_tier, d2_score)
        if b == "FILTERED":
            return None

        # Package D1 TF breakdown
        tf_breakdown = {}
        for tf, data in d1.get("timeframes", {}).items():
            tf_breakdown[tf] = {
                "tier": data.get("tier", "WATCH"),
                "score": data.get("score", 0),
            }

        # Build package (pure packaging — no analysis)
        package = {
            "signal_id": getattr(d2, 'signal_id', ''),
            "coin": coin,
            "timeframe": "15M",
            "direction": getattr(d2, 'direction', 'BULLISH'),
            "bucket": b,
            "bucket_label": bucket_label(b),
            "bucket_color": bucket_color(b),
            "d2_score": round(d2_score, 1),
            "d2_tier": d2_display_tier,
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

        # Write to state store
        await state_store.set_d3_fusion(coin, package)

        # Push to frontend
        await broadcast({"type": "signal", "data": package})

        logger.debug(f"[fusion] {coin}: bucket={b} D1={d1_tier}({d1_score:.0f}) "
                     f"D2={d2_score:.0f} dir={package['direction']}")
        return package


# Module-level singleton (matches D1/D2 pattern)
fusion_engine = FusionEngine()
