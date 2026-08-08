"""Correlation Filter — prevents overexposure to highly-correlated coins.

Groups coins into correlation buckets (Majors, Alts, Solana Ecosystem, AI/Narrative).
When a bucket already has an active position or a high-confidence signal, reduces
the position size of additional signals from that bucket.

Rules:
  1. Same bucket coin with SNIPER score > 85 → reduce to 0.35x (not 0.25x; SNIPER still trades)
  2. Same bucket coin with any signal → reduce to 0.5x
  3. Opposing direction in same bucket → block (signals cancel)
  4. Majors bucket: lower threshold (always apply correlation)
  5. Use BTC correlation as proxy for "same direction" detection
"""
import logging
from typing import Optional
from collections import defaultdict

logger = logging.getLogger("judah.correlation")

# Correlation buckets (coins that tend to move together)
_CORRELATION_BUCKETS = {
    "MAJORS": ["BTCUSDT", "ETHUSDT"],
    "SOL_ECOSYSTEM": ["SOLUSDT", "JUPUSDT", "RAYUSDT", "BONKUSDT", "JTOUSDT"],
    "AI_NARRATIVE": ["FETUSDT", "AGIXUSDT", "RENDERUSDT", "TAOUSDT", "WLDUSDT"],
    "DEFI": ["UNIUSDT", "AAVEUSDT", "LINKUSDT", "MKRUSDT", "SNXUSDT"],
    "LAYER1": ["AVAXUSDT", "DOTUSDT", "MATICUSDT", "ATOMUSDT", "NEARUSDT"],
    "MEME": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "WIFUSDT"],
}

# Reverse map: symbol → bucket
_SYMBOL_TO_BUCKET = {}
for bucket, symbols in _CORRELATION_BUCKETS.items():
    for sym in symbols:
        _SYMBOL_TO_BUCKET[sym.upper()] = bucket


def get_correlation_bucket(symbol: str) -> Optional[str]:
    """Get the correlation bucket for a symbol."""
    return _SYMBOL_TO_BUCKET.get(symbol.upper())


def apply_correlation_filter(symbol: str, signal: dict, active_positions: list,
                              active_signals: list) -> dict:
    """Apply correlation filter to a signal.

    Args:
        symbol: Trading pair (e.g. "SOLUSDT")
        signal: Signal dict with "composite_score", "direction", "tier"
        active_positions: List of currently open position dicts
        active_signals: List of active signal dicts (not yet filled)

    Returns:
        Modified signal dict with "position_multiplier" added (1.0 = no reduction)
    """
    bucket = get_correlation_bucket(symbol)
    if not bucket:
        # No correlation data → no filter
        signal["position_multiplier"] = 1.0
        signal["correlation_blocked"] = False
        signal["correlation_bucket"] = None
        return signal

    signal["correlation_bucket"] = bucket
    bucket_signals = _get_bucket_signals(bucket, active_positions, active_signals, symbol)

    # Rule 3: Opposing direction in same bucket → block
    opposing = [s for s in bucket_signals
                if s.get("direction") and s.get("direction") != signal.get("direction")]
    if opposing:
        logger.info(f"[correlation] BLOCK {symbol}: opposing direction in {bucket} "
                     f"({[s.get('symbol', '?') for s in opposing]})")
        signal["position_multiplier"] = 0.0
        signal["correlation_blocked"] = True
        signal["correlation_reason"] = f"Opposing direction in {bucket}"
        return signal

    # Rule 1: Same bucket with SNIPER > 85 → reduce
    sniper_signals = [s for s in bucket_signals
                      if s.get("tier") == "SNIPER" and s.get("composite_score", 0) > 85]
    if sniper_signals:
        signal["position_multiplier"] = 0.35
        signal["correlation_blocked"] = False
        signal["correlation_reason"] = f"SNIPER in {bucket}, reduced to 35%"
        return signal

    # Rule 2: Any signal in same bucket → reduce
    if bucket_signals:
        signal["position_multiplier"] = 0.5
        signal["correlation_blocked"] = False
        signal["correlation_reason"] = f"Signal in {bucket}, reduced to 50%"
        return signal

    # No correlation conflict
    signal["position_multiplier"] = 1.0
    signal["correlation_blocked"] = False
    signal["correlation_reason"] = None
    return signal


def get_bucket_summary(active_positions: list, active_signals: list) -> dict:
    """Get a summary of correlation bucket exposure for dashboard display."""
    bucket_exposure = defaultdict(list)
    for pos in active_positions:
        bucket = get_correlation_bucket(pos.get("symbol", ""))
        if bucket:
            bucket_exposure[bucket].append({
                "symbol": pos.get("symbol"),
                "direction": pos.get("direction"),
                "type": "position",
            })
    for sig in active_signals:
        bucket = get_correlation_bucket(sig.get("symbol", ""))
        if bucket:
            bucket_exposure[bucket].append({
                "symbol": sig.get("symbol"),
                "direction": sig.get("direction"),
                "type": "signal",
            })

    return dict(bucket_exposure)


# ── Private Helpers ─────────────────────────────────────────────────────

def _get_bucket_signals(bucket: str, active_positions: list, active_signals: list,
                         exclude_symbol: str) -> list:
    """Get all signals/positions in a bucket, excluding the current symbol."""
    results = []
    for pos in active_positions:
        sym = pos.get("symbol", "").upper()
        if sym != exclude_symbol.upper() and get_correlation_bucket(sym) == bucket:
            results.append(pos)
    for sig in active_signals:
        sym = sig.get("symbol", "").upper()
        if sym != exclude_symbol.upper() and get_correlation_bucket(sym) == bucket:
            results.append(sig)
    return results
