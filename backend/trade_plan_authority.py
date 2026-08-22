"""TradePlanAuthority — single source of truth for entry, SL, TP, RR, position size.

Every trade plan must go through this authority. No component computes
entry/SL/TP inline — they call TradePlanAuthority.propose() and receive
a fully-validated TradePlan or a rejection.

Design rules:
  - Single function owns all price-level calculations.
  - Zone discipline: BULLISH entry MUST be in DISCOUNT (≤ midpoint);
    BEARISH entry MUST be in PREMIUM (≥ midpoint).
  - RR floor: minimum 1.2:1 before any plan is accepted.
  - SL width: bounded by type-specific multiplier × ATR.
  - Position size: scaled by confidence_score and signal type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("judah.tradeplan")


class PlanStatus(Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED_RR_FLOOR = "REJECTED_RR_FLOOR"
    REJECTED_ZONE_VIOLATION = "REJECTED_ZONE_VIOLATION"
    REJECTED_SL_WIDTH = "REJECTED_SL_WIDTH"
    REJECTED_INSUFFICIENT_DATA = "REJECTED_INSUFFICIENT_DATA"
    REJECTED_CONFLICT = "REJECTED_CONFLICT"


@dataclass(frozen=True)
class TradePlan:
    """Fully-validated trade plan produced by TradePlanAuthority."""
    status: PlanStatus
    symbol: str
    direction: str           # BULLISH | BEARISH
    entry: float
    sl: float
    tp1: float
    tp2: float
    rr1: float               # Risk:Reward ratio for TP1
    rr2: float               # Risk:Reward ratio for TP2
    position_size_mult: float
    confidence_score: float   # 0.0–1.0
    zone: str                # DISCOUNT | PREMIUM | EQUILIBRIUM
    atr_sl_mult: float
    atr_tp_mult: float
    rationale: str = ""
    signal_id: str = ""
    rejection_reason: str = ""

    def is_accepted(self) -> bool:
        return self.status == PlanStatus.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": round(self.entry, 5),
            "sl": round(self.sl, 5),
            "tp1": round(self.tp1, 5),
            "tp2": round(self.tp2, 5),
            "rr1": round(self.rr1, 2),
            "rr2": round(self.rr2, 2),
            "position_size_mult": round(self.position_size_mult, 2),
            "confidence_score": round(self.confidence_score, 3),
            "zone": self.zone,
            "atr_sl_mult": round(self.atr_sl_mult, 2),
            "atr_tp_mult": round(self.atr_tp_mult, 2),
            "rationale": self.rationale,
            "rejection_reason": self.rejection_reason,
        }


class TradePlanAuthority:
    """Single authority for trade plan generation and validation."""

    # Configurable parameters
    RR_FLOOR = 1.2
    ATR_SL_MULT_DEFAULT = 1.5
    ATR_TP_MULT_DEFAULT = 3.0
    ATR_SL_MULT_TYPE_B = 1.0
    ATR_TP_MULT_TYPE_B = 2.0
    MAX_SL_PCT = 0.04  # Max SL width as % of price (4%)
    MIN_CONFIDENCE = 0.3

    # Signal type → position size multiplier
    TYPE_POSITION_MULT = {"A": 0.75, "B": 0.35, "C": 1.0, "D": 0.0, "E": 0.0}

    def propose(
        self,
        symbol: str,
        direction: str,
        entry: float,
        atr: float,
        d1_zone: str = "EQUILIBRIUM",
        d2_zone: str = "EQUILIBRIUM",
        ob_low: float = 0,
        ob_high: float = 0,
        signal_type: str = "A",
        confidence_score: float = 0.5,
        alignment_level: str = "PARTIAL_ALIGNMENT",
        signal_id: str = "",
        d1_sl: float = 0,
        d2_sl: float = 0,
    ) -> TradePlan:
        """Generate and validate a trade plan.

        Args:
            symbol: Trading pair.
            direction: BULLISH or BEARISH.
            entry: Proposed entry price.
            atr: Current ATR value.
            d1_zone: HTF premium/discount zone.
            d2_zone: LTF premium/discount zone.
            ob_low: Order block low (for SL anchoring).
            ob_high: Order block high.
            signal_type: A/B/C/D/E.
            confidence_score: 0.0–1.0.
            alignment_level: STRONG_ALIGNMENT / PARTIAL_ALIGNMENT / CONFLICT / etc.
            signal_id: ID from evidence store.
            d1_sl: D1 structural SL (if provided).
            d2_sl: D2 structural SL (if provided).

        Returns:
            TradePlan with status (ACCEPTED or rejection reason).
        """
        if entry <= 0 or atr <= 0:
            return TradePlan(
                status=PlanStatus.REJECTED_INSUFFICIENT_DATA,
                symbol=symbol, direction=direction, entry=entry,
                sl=0, tp1=0, tp2=0, rr1=0, rr2=0,
                position_size_mult=0, confidence_score=0,
                zone=d1_zone, atr_sl_mult=0, atr_tp_mult=0,
                rejection_reason="entry or ATR is zero/negative",
            )

        # Conflict rejection
        if alignment_level == "CONFLICT":
            return self._reject(symbol, direction, entry, PlanStatus.REJECTED_CONFLICT,
                                "D1/D2 direction conflict", confidence_score, d1_zone)

        # Choose the tighter SL from D1 or D2 structural levels, or fall back to ATR
        atr_sl_mult = self.ATR_SL_MULT_TYPE_B if signal_type == "B" else self.ATR_SL_MULT_DEFAULT
        atr_tp_mult = self.ATR_TP_MULT_TYPE_B if signal_type == "B" else self.ATR_TP_MULT_DEFAULT
        atr_sl = atr * atr_sl_mult

        # Pick SL: prefer D1 structural if tighter, else ATR-based
        if direction.upper() == "BULLISH":
            candidates = []
            if ob_low > 0 and ob_low < entry:
                candidates.append(("OB_LOW", ob_low))
            if d1_sl > 0 and d1_sl < entry:
                candidates.append(("D1_SL", d1_sl))
            if d2_sl > 0 and d2_sl < entry:
                candidates.append(("D2_SL", d2_sl))
            candidates.append(("ATR", entry - atr_sl))
            sl = max(c[1] for c in candidates)
            sl_label = next((c[0] for c in candidates if c[1] == sl), "ATR")

            tp1 = entry + atr * atr_tp_mult
            tp2 = entry + atr * atr_tp_mult * 2.0
        else:
            candidates = []
            if ob_high > 0 and ob_high > entry:
                candidates.append(("OB_HIGH", ob_high))
            if d1_sl > 0 and d1_sl > entry:
                candidates.append(("D1_SL", d1_sl))
            if d2_sl > 0 and d2_sl > entry:
                candidates.append(("D2_SL", d2_sl))
            candidates.append(("ATR", entry + atr_sl))
            sl = min(c[1] for c in candidates)
            sl_label = next((c[0] for c in candidates if c[1] == sl), "ATR")

            tp1 = entry - atr * atr_tp_mult
            tp2 = entry - atr * atr_tp_mult * 2.0

        risk = abs(entry - sl)
        if risk == 0:
            return self._reject(symbol, direction, entry, PlanStatus.REJECTED_SL_WIDTH,
                                "SL distance is zero", confidence_score, d1_zone)

        # RR calculation
        rr1 = abs(tp1 - entry) / risk
        rr2 = abs(tp2 - entry) / risk

        # RR floor check
        if rr1 < self.RR_FLOOR:
            return TradePlan(
                status=PlanStatus.REJECTED_RR_FLOOR,
                symbol=symbol, direction=direction, entry=entry,
                sl=sl, tp1=tp1, tp2=tp2,
                rr1=round(rr1, 2), rr2=round(rr2, 2),
                position_size_mult=0,
                confidence_score=confidence_score,
                zone=d1_zone,
                atr_sl_mult=atr_sl_mult,
                atr_tp_mult=atr_tp_mult,
                rejection_reason=f"RR1={rr1:.2f} < floor {self.RR_FLOOR}",
            )

        # Zone discipline check
        zone_status = self._check_zone(direction, entry, d1_zone, d2_zone, ob_low, ob_high, signal_type)
        if zone_status != PlanStatus.ACCEPTED:
            return TradePlan(
                status=zone_status,
                symbol=symbol, direction=direction, entry=entry,
                sl=sl, tp1=tp1, tp2=tp2,
                rr1=round(rr1, 2), rr2=round(rr2, 2),
                position_size_mult=0,
                confidence_score=confidence_score,
                zone=d1_zone,
                atr_sl_mult=atr_sl_mult,
                atr_tp_mult=atr_tp_mult,
                rejection_reason=f"zone violation: entry not in {('DISCOUNT' if direction == 'BULLISH' else 'PREMIUM')}",
            )

        # Confidence check
        if confidence_score < self.MIN_CONFIDENCE:
            return TradePlan(
                status=PlanStatus.REJECTED_INSUFFICIENT_DATA,
                symbol=symbol, direction=direction, entry=entry,
                sl=sl, tp1=tp1, tp2=tp2,
                rr1=round(rr1, 2), rr2=round(rr2, 2),
                position_size_mult=0,
                confidence_score=confidence_score,
                zone=d1_zone,
                atr_sl_mult=atr_sl_mult,
                atr_tp_mult=atr_tp_mult,
                rejection_reason=f"confidence {confidence_score:.0%} < min {self.MIN_CONFIDENCE:.0%}",
            )

        # SL width sanity
        sl_pct = risk / entry if entry > 0 else 0
        if sl_pct > self.MAX_SL_PCT:
            return TradePlan(
                status=PlanStatus.REJECTED_SL_WIDTH,
                symbol=symbol, direction=direction, entry=entry,
                sl=sl, tp1=tp1, tp2=tp2,
                rr1=round(rr1, 2), rr2=round(rr2, 2),
                position_size_mult=0,
                confidence_score=confidence_score,
                zone=d1_zone,
                atr_sl_mult=atr_sl_mult,
                atr_tp_mult=atr_tp_mult,
                rejection_reason=f"SL width {sl_pct:.1%} > max {self.MAX_SL_PCT:.0%}",
            )

        # Position sizing
        pos_mult = self.TYPE_POSITION_MULT.get(signal_type, 0.0)
        pos_mult *= confidence_score  # scale by confidence

        return TradePlan(
            status=PlanStatus.ACCEPTED,
            symbol=symbol, direction=direction, entry=entry,
            sl=sl, tp1=tp1, tp2=tp2,
            rr1=round(rr1, 2), rr2=round(rr2, 2),
            position_size_mult=round(pos_mult, 2),
            confidence_score=confidence_score,
            zone=d1_zone,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            rationale=f"SL via {sl_label}, RR1={rr1:.1f}, zone={d1_zone}",
            signal_id=signal_id,
        )

    def _check_zone(self, direction: str, entry: float, d1_zone: str, d2_zone: str,
                    ob_low: float, ob_high: float, signal_type: str = "A") -> PlanStatus:
        """Zone discipline guard.

        Primary check: premium_discount from CRT/HTF analysis.
        OB midpoint is a confirmation, not the gate.

        BULLISH entry should be in DISCOUNT zone.
        BEARISH entry should be in PREMIUM zone.
        EQUILIBRIUM is neutral — only reject if the OPPOSITE zone is detected.
        Type B (LTF Momentum) is lenient: only rejects extreme opposite zone.
        """
        effective_zone = d2_zone if d2_zone != "EQUILIBRIUM" else d1_zone

        # Type B is momentum-based — only reject if in the OPPOSITE extreme zone
        if signal_type == "B":
            if direction.upper() == "BULLISH" and effective_zone == "PREMIUM":
                return PlanStatus.REJECTED_ZONE_VIOLATION
            if direction.upper() == "BEARISH" and effective_zone == "DISCOUNT":
                return PlanStatus.REJECTED_ZONE_VIOLATION
            return PlanStatus.ACCEPTED  # Type B: accept DISCOUNT, EQUILIBRIUM, or PREMIUM(if not extreme)

        if direction.upper() == "BULLISH":
            if effective_zone == "PREMIUM":
                return PlanStatus.REJECTED_ZONE_VIOLATION
            # DISCOUNT or EQUILIBRIUM: check OB midpoint as confirmation
            if ob_low > 0 and ob_high > 0:
                midpoint = (ob_low + ob_high) / 2
                if entry > midpoint * 1.05:  # 5% tolerance above midpoint
                    return PlanStatus.REJECTED_ZONE_VIOLATION
        elif direction.upper() == "BEARISH":
            if effective_zone == "DISCOUNT":
                return PlanStatus.REJECTED_ZONE_VIOLATION
            # PREMIUM or EQUILIBRIUM: check OB midpoint as confirmation
            if ob_low > 0 and ob_high > 0:
                midpoint = (ob_low + ob_high) / 2
                if entry < midpoint * 0.95:  # 5% tolerance below midpoint
                    return PlanStatus.REJECTED_ZONE_VIOLATION
        return PlanStatus.ACCEPTED

    def _reject(self, symbol: str, direction: str, entry: float,
                status: PlanStatus, reason: str,
                confidence: float, zone: str) -> TradePlan:
        return TradePlan(
            status=status,
            symbol=symbol, direction=direction, entry=entry,
            sl=0, tp1=0, tp2=0, rr1=0, rr2=0,
            position_size_mult=0,
            confidence_score=confidence,
            zone=zone,
            atr_sl_mult=0, atr_tp_mult=0,
            rejection_reason=reason,
        )


# Module-level singleton
trade_plan_authority = TradePlanAuthority()