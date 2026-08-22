"""DecisionSnapshot — immutable market snapshot consumed by D1, D2, and D3.

Every scanner decision reads from exactly one DecisionSnapshot.
No downstream component re-reads live candle state for the same decision.

Design: lightweight dataclass with validated candles per symbol/timeframe.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.config import (
    TIMEFRAMES_HTF,
    TIMEFRAMES_LTF,
    HOST,
    PORT,
)

logger = logging.getLogger("judah.snapshot")


def _config_hash() -> str:
    """Hash the current config module for provenance tracking."""
    cfg_path = os.path.join(os.path.dirname(__file__), "config.py")
    try:
        with open(cfg_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return "unknown"


def _code_version() -> str:
    """Git short SHA for reproducibility."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.dirname(__file__)),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# Stable identifiers computed once at import time
_CONFIG_HASH = _config_hash()
_CODE_VERSION = _code_version()


@dataclass(frozen=True)
class DecisionSnapshot:
    """Immutable market snapshot for a single decision cycle.

    D1 and D2 each read from the same snapshot.  No mutable shared state.
    """

    snapshot_id: str
    snapshot_timestamp: float  # epoch seconds
    processing_timestamp: float
    symbol: str
    market_data_version: str
    configuration_hash: str
    code_version: str

    candles: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    # key: "SYMBOL:TF" -> tuple of Candle objects (immutable view)

    data_quality: dict[str, str] = field(default_factory=dict)
    # key: "SYMBOL:TF" -> quality state ("VALID" | "STALE" | "MISSING" | ...)

    liquidity_state: dict[str, Any] = field(default_factory=dict)
    # per-symbol liquidity metadata

    btc_candles: tuple[Any, ...] = field(default_factory=tuple)
    d1_tiers: dict[str, dict] = field(default_factory=dict)

    def get_candles(self, symbol: str, timeframe: str) -> tuple[Any, ...]:
        """Return candles for (symbol, timeframe). Empty tuple if missing."""
        return self.candles.get(f"{symbol}:{timeframe}", ())

    def candle_quality(self, symbol: str, timeframe: str) -> str:
        """Return data quality for a symbol/timeframe pair."""
        return self.data_quality.get(f"{symbol}:{timeframe}", "MISSING")

    def is_valid_for(self, symbol: str, timeframe: str) -> bool:
        """Quick check whether a symbol/timeframe has valid data."""
        return (
            self.candle_quality(symbol, timeframe) == "VALID"
            and len(self.get_candles(symbol, timeframe)) > 0
        )

    def frame_timestamp(self, symbol: str, timeframe: str) -> float:
        """Return the timestamp of the latest candle for a symbol/timeframe, or 0."""
        candles = self.get_candles(symbol, timeframe)
        if candles:
            return getattr(candles[-1], "time", 0)
        return 0.0

    def age_seconds(self, symbol: str, timeframe: str, now: float | None = None) -> float:
        """Age of the latest candle in seconds."""
        if now is None:
            now = datetime.now(timezone.utc).timestamp()
        return now - self.frame_timestamp(symbol, timeframe)


class SnapshotBuilder:
    """Builds DecisionSnapshots from the current market_data state.

    Usage:
        builder = SnapshotBuilder(market_data)
        snap = builder.build(symbols, timeframes)
    """

    def __init__(self, market_data_client):
        self._md = market_data_client

    def build(
        self,
        symbols: list[str],
        htf_timeframes: list[str] | None = None,
        ltf_timeframes: list[str] | None = None,
    ) -> DecisionSnapshot:
        """Build an immutable DecisionSnapshot.

        Args:
            symbols: Trading pairs to snapshot.
            htf_timeframes: HTF timeframes (default: config.TIMEFRAMES_HTF).
            ltf_timeframes: LTF timeframes (default: config.TIMEFRAMES_LTF).

        Returns:
            Frozen DecisionSnapshot ready for D1 + D2 consumption.
        """
        if htf_timeframes is None:
            htf_timeframes = TIMEFRAMES_HTF
        if ltf_timeframes is None:
            ltf_timeframes = TIMEFRAMES_LTF

        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()

        snapshot_id = hashlib.sha256(
            f"{now_ts:.3f}-{','.join(symbols[:5])}".encode()
        ).hexdigest()[:16]

        candles: dict[str, tuple] = {}
        data_quality: dict[str, str] = {}

        all_tfs = htf_timeframes + ltf_timeframes
        for symbol in symbols:
            for tf in all_tfs:
                key = f"{symbol}:{tf}"
                raw = self._md.get_candles(symbol, tf) if hasattr(self._md, "get_candles") else ()
                quality = self._assess_quality(raw, tf)
                candles[key] = raw if raw else ()
                data_quality[key] = quality

        # BTC reference data
        btc_candles: tuple = ()
        try:
            btc_candles = self._md.get_candles("BTCUSDT", "1H") if hasattr(self._md, "get_candles") else ()
        except Exception:
            pass

        return DecisionSnapshot(
            snapshot_id=snapshot_id,
            snapshot_timestamp=now_ts,
            processing_timestamp=now_ts,
            symbol=",".join(symbols[:10]) + ("..." if len(symbols) > 10 else ""),
            market_data_version=_CODE_VERSION,
            configuration_hash=_CONFIG_HASH,
            code_version=_CODE_VERSION,
            candles=candles,
            data_quality=data_quality,
            btc_candles=btc_candles,
        )

    @staticmethod
    def _assess_quality(candles: tuple, timeframe: str) -> str:
        """Determine data quality state for a candle set using the full quality gate."""
        from backend.data_quality_gate import validate_candles
        result = validate_candles(candles, timeframe)
        return result.state
