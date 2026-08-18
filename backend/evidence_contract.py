"""Phase 4 — Evidence Contract.

Evidence is a first-class, immutable contract between D1, D2, and D3.
Every analytical observation (CRT, SMC, Flow, Momentum, Structure, etc.)
produces an EvidenceRecord with explicit freshness and status.

This contract guarantees:
  - No degraded result looks identical to a fully-supported result
  - Evidence cannot silently cross snapshot boundaries
  - Every piece of evidence is traceable to its source and snapshot
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidenceStatus(Enum):
    """Lifecycle states for a single evidence record."""
    FULL = "FULL"           # Complete, verified, current
    PARTIAL = "PARTIAL"     # Some components available, others missing
    DEGRADED = "DEGRADED"   # Source failed or returned incomplete data
    FAILED = "FAILED"       # Source produced no usable output
    STALE = "STALE"         # Was FULL but freshness expired


class EvidenceSource(Enum):
    """Enumeration of all possible evidence sources across D1 and D2."""
    # D1 HTF sources
    CRT = "CRT"
    SMC = "SMC"
    HTF_STRUCTURE = "HTF_STRUCTURE"
    HTF_LIQUIDITY = "HTF_LIQUIDITY"
    HTF_VOLATILITY = "HTF_VOLATILITY"
    HTF_REGIME = "HTF_REGIME"
    # D2 LTF sources
    FLOW = "FLOW"
    MOMENTUM = "MOMENTUM"
    LTF_STRUCTURE = "LTF_STRUCTURE"
    NASCENT_MOVE = "NASCENT_MOVE"
    ENTRY_PRECISION = "ENTRY_PRECISION"
    HTF_CONTEXT = "HTF_CONTEXT"
    FVG = "FVG"
    ORDER_BLOCK = "ORDER_BLOCK"
    SWING_POINT = "SWING_POINT"
    LIQUIDITY_POOL = "LIQUIDITY_POOL"
    # D3 sources
    ALIGNMENT = "ALIGNMENT"
    MARKET_EVOLUTION = "MARKET_EVOLUTION"
    CONFIDENCE = "CONFIDENCE"
    TRADE_PLAN = "TRADE_PLAN"
    RISK = "RISK"
    # Cross-cutting
    DATA_QUALITY = "DATA_QUALITY"
    CONFLUENCE = "CONFLUENCE"
    BOOST = "BOOST"


class EvidenceDimension(Enum):
    """Which dimension produced this evidence."""
    D1 = "D1"
    D2 = "D2"
    D3 = "D3"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class EvidenceRecord:
    """Immutable evidence record — the atomic unit of scanner intelligence.

    Every CRT calculation, SMC detection, flow measurement, etc. produces
    one EvidenceRecord. EvidenceStore manages lifecycle (TTL, dedup, expiry).

    Attributes:
        evidence_id:  Unique ID (UUID-based, generated at creation)
        snapshot_id:  DecisionSnapshot this evidence belongs to
        symbol:       Trading pair (e.g. "BTCUSDT")
        dimension:    D1, D2, D3, or SYSTEM
        source:       Which analytical component produced this
        observation:  Human-readable label (e.g. "OB Proximity", "FVG Fill")
        value:        The raw measured value (float, int, or str)
        strength:     Normalized 0.0-1.0 — how strong is this evidence
        confidence:   Normalized 0.0-1.0 — how confident are we in this value
        timestamp:    When the evidence was produced (epoch seconds)
        freshness:    Calculated freshness: "hot", "warm", "cool", "cold", "dead"
        status:       FULL, PARTIAL, DEGRADED, FAILED, or STALE
        reason:       Optional explanation for DEGRADED/FAILED/STALE states
    """
    evidence_id: str
    snapshot_id: str
    symbol: str
    dimension: EvidenceDimension
    source: EvidenceSource
    observation: str
    value: Any
    strength: float        # 0.0 – 1.0
    confidence: float      # 0.0 – 1.0
    timestamp: float       # epoch seconds
    freshness: str         # hot | warm | cool | cold | dead
    status: EvidenceStatus
    reason: str = ""       # only populated for non-FULL statuses

    # Backward-compat aliases (legacy code may reference these)
    @property
    def data_quality(self) -> str:
        """Alias for status — maps EvidenceStatus to legacy quality strings."""
        mapping = {
            EvidenceStatus.FULL: "VALID",
            EvidenceStatus.PARTIAL: "DEGRADED",
            EvidenceStatus.DEGRADED: "DEGRADED",
            EvidenceStatus.FAILED: "INVALID",
            EvidenceStatus.STALE: "STALE",
        }
        return mapping.get(self.status, "UNKNOWN")

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API/WS transport."""
        return {
            "evidence_id": self.evidence_id,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "dimension": self.dimension.value,
            "source": self.source.value,
            "observation": self.observation,
            "value": self.value,
            "strength": round(self.strength, 3),
            "confidence": round(self.confidence, 3),
            "timestamp": self.timestamp,
            "freshness": self.freshness,
            "status": self.status.value,
            "reason": self.reason,
            "data_quality": self.data_quality,
        }

    def is_tradeable(self) -> bool:
        """Evidence is usable for trading decisions."""
        return self.status in (EvidenceStatus.FULL, EvidenceStatus.PARTIAL)

    def is_stale(self) -> bool:
        return self.status == EvidenceStatus.STALE

    def age_seconds(self, now: float | None = None) -> float:
        if now is None:
            import time
            now = time.time()
        return now - self.timestamp

    def __repr__(self) -> str:
        return (f"EvidenceRecord({self.source.value}:{self.observation} "
                f"val={self.value} status={self.status.value} "
                f"strength={self.strength:.2f})")


def create_evidence(
    snapshot_id: str,
    symbol: str,
    dimension: EvidenceDimension,
    source: EvidenceSource,
    observation: str,
    value: Any,
    strength: float = 0.5,
    confidence: float = 0.5,
    timestamp: float | None = None,
    freshness: str = "hot",
    status: EvidenceStatus = EvidenceStatus.FULL,
    reason: str = "",
) -> EvidenceRecord:
    """Factory function — the only way to create EvidenceRecords.

    Centralizes ID generation and validation.
    """
    if timestamp is None:
        import time
        timestamp = time.time()
    # Clamp numeric fields
    strength = max(0.0, min(1.0, float(strength)))
    confidence = max(0.0, min(1.0, float(confidence)))
    return EvidenceRecord(
        evidence_id=str(uuid.uuid4())[:12],
        snapshot_id=snapshot_id,
        symbol=symbol,
        dimension=dimension,
        source=source,
        observation=observation,
        value=value,
        strength=strength,
        confidence=confidence,
        timestamp=timestamp,
        freshness=freshness,
        status=status,
        reason=reason,
    )
