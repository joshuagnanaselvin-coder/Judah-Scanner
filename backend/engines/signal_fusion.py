"""Dimension 3 — Signal Fusion Engine.

Reads D1 tier + D2 signal from state store, assigns buckets, packages for frontend.
Zero technical analysis. Pure packaging + bucketing logic.

Triggered after D1 or D2 updates. Pushes to frontend via WebSocket.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from backend.config import (
    TIER_SNIPER_SCORE, TIER_OPPORTUNITY_SCORE, TIER_WATCH_SCORE,
)
from backend.state_store import state_store
from backend.ws_hub import broadcast

logger = logging.getLogger("judah.fusion")


def bucket(d1_tier: str, d2_score: float) -> str:
    """Assign bucket based on D1 tier + D2 score.

    | D1 Tier  | D2 Score | Bucket  |
    | SNIPER   | >= 70    | READY   |
    | OPPORTUNITY | >= 70    | EARLY   |
    | WATCH    | >= 70    | TRAP    |
    | All WATCH| any      | FILTERED|
    """
    if d2_score < 70:
        return "FILTERED"

    if d1_tier == "SNIPER":
        return "READY"
    if d1_tier == "OPPORTUNITY":
        return "EARLY"
    if d1_tier == "WATCH":
        return "TRAP"
    if d1_tier == "REJECTED":
        return "FILTERED"

    # No D1 data = conservative
    return "FILTERED"


def bucket_label(b: str) -> str:
    return {
        "READY": "Ready",
        "EARLY": "Early",
        "TRAP": "Trap",
        "FILTERED": "Filtered",
    }.get(b, b)


def bucket_color(b: str) -> str:
    return {
        "READY": "#22c55e",   # green
        "EARLY": "#f59e0b",   # amber
        "TRAP": "#ef4444",    # red
        "FILTERED": "#6b7280", # gray
    }.get(b, "#6b7280")


async def fuse(coin: str) -> Optional[dict]:
    """Fuse D1 + D2 for one coin. Returns packaged dict for frontend or None if filtered."""
    d1 = state_store.get_d1_tier(coin)
    d2 = state_store.get_d2_signal(coin)

    if not d1 or not d2:
        return None

    d1_tier = d1.get("tier", "WATCH")
    d1_score = d1.get("score", 0)
    d2_score = d2.score if hasattr(d2, 'score') else 0

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

    # Build package (no analysis — just packaging D1 + D2 data)
    package = {
        "signal_id": d2.signal_id,
        "coin": coin,
        "timeframe": "15M",
        "direction": d2.direction,
        "bucket": b,
        "bucket_label": bucket_label(b),
        "bucket_color": bucket_color(b),
        "d2_score": round(d2_score, 1),
        "d2_tier": d2.tier,
        "d1_tier": d1_tier,
        "d1_score": round(d1_score, 1),
        "d1_timeframes": tf_breakdown,
        "entry": d2.entry,
        "sl": d2.sl,
        "tp1": d2.tp1,
        "tp2": d2.tp2,
        "rr1": round(d2.rr1, 2),
        "rr2": round(d2.rr2, 2),
        "freshness": d2.freshness,
        "score_history": list(d2.score_history)[-10:],  # Last 10 for sparkline
        "born_at": d2.born_at.isoformat(),
        "last_scan": d2.last_scan.isoformat(),
    }

    # Write to state store
    await state_store.set_d3_fusion(coin, package)

    # Push to frontend
    await broadcast({"type": "signal", "data": package})

    logger.debug(f"[fusion] {coin}: bucket={b} D1={d1_tier}({d1_score:.0f}) "
                 f"D2={d2_score:.0f} dir={d2.direction}")

    return package


async def fuse_all():
    """Fuse all active coins. Called after D1 full scan completes."""
    active = state_store.get_active_coins()
    results = []
    for coin in active:
        pkg = await fuse(coin)
        if pkg:
            results.append(pkg)
    logger.info(f"[fusion] Fused {len(results)} signals from {len(active)} active coins")
    return results
