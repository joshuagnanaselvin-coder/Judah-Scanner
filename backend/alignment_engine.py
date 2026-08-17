"""AlignmentEngine — explicit alignment classification between D1 and D2.

Decides whether D1 (HTF) and D2 (LTF) are aligned, in conflict,
or have insufficient evidence. Replaces the loose boolean alignment
flags in signal_fusion._compute_alignment with explicit categories.

Categories:
    STRONG_ALIGNMENT      — both dimensions agree strongly
    PARTIAL_ALIGNMENT     — some agreement, weaker signal
    CONFLICT              — directions disagree
    INSUFFICIENT_EVIDENCE — not enough data to decide
    DEGRADED              — D1 or D2 data quality is poor
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AlignmentLevel(Enum):
    STRONG_ALIGNMENT = "STRONG_ALIGNMENT"
    PARTIAL_ALIGNMENT = "PARTIAL_ALIGNMENT"
    CONFLICT = "CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class AlignmentResult:
    """Immutable alignment verdict between D1 and D2."""
    level: AlignmentLevel
    score: float              # 0.0–1.0 confidence in the verdict
    components: dict[str, bool] = field(default_factory=dict)
    rationale: str = ""
    d1_quality: str = "VALID"
    d2_quality: str = "VALID"

    def is_tradeable(self) -> bool:
        return self.level in (AlignmentLevel.STRONG_ALIGNMENT, AlignmentLevel.PARTIAL_ALIGNMENT)

    def is_strong(self) -> bool:
        return self.level == AlignmentLevel.STRONG_ALIGNMENT

    def is_conflict(self) -> bool:
        return self.level == AlignmentLevel.CONFLICT

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "score": round(self.score, 3),
            "components": self.components,
            "rationale": self.rationale,
            "tradeable": self.is_tradeable(),
            "strong": self.is_strong(),
            "conflict": self.is_conflict(),
            "d1_quality": self.d1_quality,
            "d2_quality": self.d2_quality,
        }


class AlignmentEngine:
    """Computes AlignmentResult from D1 and D2 structure summaries."""

    def evaluate(
        self,
        d1_structure: dict[str, Any],
        d2_structure: dict[str, Any],
        d1_tier: str,
        d2_tier: str,
        d1_direction: str,
        d2_direction: str,
        d1_quality: str = "VALID",
        d2_quality: str = "VALID",
    ) -> AlignmentResult:
        """Compute explicit alignment verdict.

        Args:
            d1_structure: D1 dict from signal_fusion package.
            d2_structure: D2 dict from signal_fusion package.
            d1_tier / d2_tier: Tier strings ("SNIPER", "OPPORTUNITY", etc.).
            d1_direction / d2_direction: Direction strings.
            d1_quality / d2_quality: Data quality states.

        Returns:
            AlignmentResult with level, score, components, rationale.
        """
        # 1. Quality gate — degraded data always returns DEGRADED.
        if d1_quality not in ("VALID", "DEGRADED", "INCOMPLETE"):
            return AlignmentResult(
                level=AlignmentLevel.DEGRADED,
                score=0.0,
                rationale=f"D1 data quality: {d1_quality}",
                d1_quality=d1_quality,
                d2_quality=d2_quality,
            )
        if d2_quality not in ("VALID", "DEGRADED", "INCOMPLETE"):
            return AlignmentResult(
                level=AlignmentLevel.DEGRADED,
                score=0.0,
                rationale=f"D2 data quality: {d2_quality}",
                d1_quality=d1_quality,
                d2_quality=d2_quality,
            )

        # 2. Insufficient evidence — neither tier has any signal.
        if d1_tier in ("WEAK", "REJECTED", "") and d2_tier in ("WEAK", "REJECTED", ""):
            return AlignmentResult(
                level=AlignmentLevel.INSUFFICIENT_EVIDENCE,
                score=0.0,
                rationale=f"both D1={d1_tier} and D2={d2_tier} have no signal",
                d1_quality=d1_quality,
                d2_quality=d2_quality,
            )

        components = {
            "direction_agreement": False,
            "htf_ob_alignment": False,
            "htf_zone_alignment": False,
            "htf_liquidity_proximity": False,
            "premium_discount_agreement": False,
        }
        score = 0
        rationale_parts: list[str] = []

        # 3. Direction agreement (0–20)
        d1_dir = (d1_direction or "").upper()
        d2_dir = (d2_direction or "").upper()
        if d1_dir and d2_dir and d1_dir == d2_dir:
            components["direction_agreement"] = True
            score += 20
            rationale_parts.append(f"dir={d1_dir} agree")

        # 4. OB alignment (0–20)
        d1_ob = (d1_structure.get("ob_zone") or "").upper()
        d2_ob = (d2_structure.get("ob_zone") or "").upper()
        if d1_ob and d2_ob and d1_ob == d2_ob and d1_ob not in ("UNKNOWN", ""):
            components["htf_ob_alignment"] = True
            score += 20
            rationale_parts.append(f"OB={d1_ob} align")

        # 5. Premium/Discount alignment (0–20)
        d1_pd = (d1_structure.get("premium_discount") or "").upper()
        d2_pd = (d2_structure.get("premium_discount") or "").upper()
        if d1_pd and d2_pd and d1_pd == d2_pd and d1_pd != "UNKNOWN":
            components["premium_discount_agreement"] = True
            score += 20
            rationale_parts.append(f"zone={d1_pd} align")

        # 6. HTF liquidity proximity (0–20)
        d1_liq_swept = d1_structure.get("liq_swept", False)
        d2_liq_level = d2_structure.get("liq_level", 0)
        if d1_liq_swept and d2_liq_level > 0:
            components["htf_liquidity_proximity"] = True
            score += 20
            rationale_parts.append("HTF liquidity swept")

        # 7. HTF structural zone alignment (FVG, MSB) (0–20)
        d1_fvg = (d1_structure.get("fvg_type") or "").upper()
        d2_fvg = (d2_structure.get("fvg_type") or "").upper()
        if d1_fvg and d2_fvg and d1_fvg == d2_fvg and d1_fvg not in ("", "NONE"):
            components["htf_zone_alignment"] = True
            score += 20
            rationale_parts.append(f"FVG={d1_fvg} align")

        # Conflict detection — explicit
        is_conflict = (
            bool(d1_dir) and bool(d2_dir) and d1_dir != d2_dir
            and d1_tier in ("SNIPER", "OPPORTUNITY", "WATCH")
            and d2_tier in ("SNIPER", "OPPORTUNITY", "WATCH")
        )

        # 8. Determine level from score
        score_normalized = score / 100.0

        if is_conflict:
            return AlignmentResult(
                level=AlignmentLevel.CONFLICT,
                score=score_normalized,
                components=components,
                rationale=f"directions conflict: D1={d1_dir} vs D2={d2_dir}",
                d1_quality=d1_quality,
                d2_quality=d2_quality,
            )

        if score >= 60:
            level = AlignmentLevel.STRONG_ALIGNMENT
        elif score >= 20:
            level = AlignmentLevel.PARTIAL_ALIGNMENT
        else:
            level = AlignmentLevel.INSUFFICIENT_EVIDENCE

        if not rationale_parts:
            rationale_parts.append(f"weak alignment ({score}/100)")

        return AlignmentResult(
            level=level,
            score=score_normalized,
            components=components,
            rationale="; ".join(rationale_parts),
            d1_quality=d1_quality,
            d2_quality=d2_quality,
        )


# Module-level singleton
alignment_engine = AlignmentEngine()