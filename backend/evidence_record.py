"""EvidenceRecord — immutable evidence atom for D1 and D2 analysis.

Every observation (OB hit, FVG fill, liquidity sweep, MSB break, etc.)
becomes a frozen EvidenceRecord. No raw floats or dicts pass between
components — only typed, timestamped, attributed evidence atoms.

This is the Evidence Contract: the universal interchange format between
all pipeline stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidenceCategory(Enum):
    """What kind of evidence this is."""
    ORDER_BLOCK = "order_block"
    FAIR_VALUE_GAP = "fair_value_gap"
    LIQUIDITY_POOL = "liquidity_pool"
    MSB_BREAK = "msb_break"
    DISPLACEMENT = "displacement"
    STRUCTURAL_LEVEL = "structural_level"
    REGIME_SHIFT = "regime_shift"
    VOLUME_PROFILE = "volume_profile"
    CANDLE_PATTERN = "candle_pattern"
    CONFLUENCE = "confluence"


class EvidenceStrength(Enum):
    """How strong this evidence is."""
    CRITICAL = 3    # HTF structure, major liquidity sweep
    STRONG = 2      # LTF structure, multiple touches
    MODERATE = 1    # Single touch, partial fill
    WEAK = 0        # Weak signal, early stage


@dataclass(frozen=True)
class EvidenceRecord:
    """Immutable evidence atom.

    Attributes:
        evidence_id:    Unique ID (evidence_id generator in evidence_store).
        category:       What kind of evidence (OB, FVG, liquidity, etc.).
        symbol:         Trading pair.
        timeframe:      Timeframe this evidence was detected on.
        price:          Price level of the evidence.
        strength:       CRITICAL / STRONG / MODERATE / WEAK.
        direction:      BULLISH / BEARISH / NEUTRAL.
        confidence:     0.0–1.0 how confident we are in this evidence.
        candle_time:    Timestamp of the candle that produced this evidence.
        detected_at:    When this was recorded (epoch seconds).
        source:         Which engine produced this ("crt", "smc", "flow", etc.).
        details:        Arbitrary dict of extra fields (zone, atr_mult, etc.).
        snapshot_id:    Which DecisionSnapshot this belongs to.
    """
    evidence_id: str
    category: EvidenceCategory
    symbol: str
    timeframe: str
    price: float
    strength: EvidenceStrength
    direction: str          # BULLISH | BEARISH | NEUTRAL
    confidence: float       # 0.0–1.0
    candle_time: float      # epoch seconds
    detected_at: float      # epoch seconds
    source: str             # engine name
    details: dict[str, Any] = field(default_factory=dict)
    snapshot_id: str = ""

    def is_stale(self, max_age_sec: float = 3600.0) -> bool:
        """Whether this evidence is older than max_age_sec."""
        from backend.data_quality_gate import _current_timestamp
        return (_current_timestamp() - self.detected_at) > max_age_sec

    def aligns_with(self, other: EvidenceRecord) -> bool:
        """Quick check if two evidence records agree on direction and are close in price."""
        if self.direction != other.direction or self.direction == "NEUTRAL":
            return False
        if self.symbol != other.symbol:
            return False
        # Within 0.5% price proximity
        if self.price > 0 and other.price > 0:
            diff_pct = abs(self.price - other.price) / max(self.price, other.price)
            if diff_pct > 0.005:
                return False
        return True

    def summary(self) -> str:
        return (f"[{self.category.value}] {self.symbol} {self.timeframe} "
                f"{self.direction} @ {self.price:.5f} "
                f"strength={self.strength.name} conf={self.confidence:.0%}")
