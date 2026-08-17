"""RiskAuthority — independent risk approval/rejection.

Completely separate from market intelligence. Receives TradePlan
candidates and approves/rejects based purely on portfolio-level
risk constraints:

  - Max portfolio heat (open risk as % of capital)
  - Max single-trade risk
  - Max correlation exposure (no over-concentration)
  - Max drawdown gate (halt new trades if drawdown > threshold)
  - Minimum confidence gate
  - Position sizing caps by signal type

This module knows nothing about OB, FVG, or CRT — only risk math.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from backend.trade_plan_authority import TradePlan

logger = logging.getLogger("judah.risk")


class RiskVerdict(Enum):
    APPROVED = "APPROVED"
    REJECTED_HEAT = "REJECTED_HEAT"
    REJECTED_SINGLE_RISK = "REJECTED_SINGLE_RISK"
    REJECTED_CORRELATION = "REJECTED_CORRELATION"
    REJECTED_DRAWDOWN = "REJECTED_DRAWDOWN"
    REJECTED_CONFIDENCE = "REJECTED_CONFIDENCE"
    REJECTED_POSITION_CAP = "REJECTED_POSITION_CAP"


@dataclass(frozen=True)
class RiskDecision:
    """Risk authority verdict on a trade plan."""
    verdict: RiskVerdict
    plan: TradePlan
    approved_size: float = 0.0        # Adjusted position size (may be reduced)
    portfolio_heat: float = 0.0       # Current open risk as % of capital
    max_allowed_heat: float = 0.0
    max_allowed_single: float = 0.0   # Max risk for a single trade
    rationale: str = ""


class RiskAuthority:
    """Independent risk management authority.

    Configurable via class attributes:
        MAX_PORTFOLIO_HEAT = 0.05      # 5% max open risk
        MAX_SINGLE_RISK = 0.02         # 2% max per trade
        MAX_DRAWDOWN = 0.10            # 10% drawdown → halt new trades
        MIN_CONFIDENCE = 0.30          # Minimum confidence_score
        MAX_CORRELATED = 3             # Max trades in same correlation group
    """

    MAX_PORTFOLIO_HEAT = 0.05
    MAX_SINGLE_RISK = 0.02
    MAX_DRAWDOWN = 0.10
    MIN_CONFIDENCE = 0.30
    MAX_CORRELATED = 3

    def __init__(self, capital: float = 10_000.0):
        self._capital = capital
        self._open_positions: dict[str, dict] = {}
        self._peak_capital = capital
        self._current_capital = capital
        self._lock = None  # asyncio.Lock created lazily

    def _get_lock(self):
        if self._lock is None:
            import asyncio
            self._lock = asyncio.Lock()
        return self._lock

    def review(self, plan: TradePlan, correlation_group: str = "") -> RiskDecision:
        """Review a trade plan for risk compliance.

        Args:
            plan: TradePlan from TradePlanAuthority.
            correlation_group: Grouping key for correlation limits (e.g., "BTC", "ETH").

        Returns:
            RiskDecision with verdict and rationale.
        """
        if not plan.is_accepted():
            return RiskDecision(
                verdict=RiskVerdict.REJECTED_CONFIDENCE,
                plan=plan,
                rationale=f"Plan already rejected: {plan.rejection_reason}",
            )

        drawdown = 1.0 - (self._current_capital / self._peak_capital)

        # 1. Drawdown gate
        if drawdown >= self.MAX_DRAWDOWN:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED_DRAWDOWN,
                plan=plan,
                max_allowed_heat=self.MAX_PORTFOLIO_HEAT,
                max_allowed_single=self.MAX_SINGLE_RISK,
                rationale=f"Drawdown {drawdown:.1%} >= max {self.MAX_DRAWDOWN:.0%}",
            )

        # 2. Confidence gate
        if plan.confidence_score < self.MIN_CONFIDENCE:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED_CONFIDENCE,
                plan=plan,
                max_allowed_heat=self.MAX_PORTFOLIO_HEAT,
                max_allowed_single=self.MAX_SINGLE_RISK,
                rationale=f"Confidence {plan.confidence_score:.0%} < min {self.MIN_CONFIDENCE:.0%}",
            )

        # 3. Single trade risk
        single_risk = plan.position_size_mult * self.MAX_SINGLE_RISK
        if single_risk > self.MAX_SINGLE_RISK:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED_SINGLE_RISK,
                plan=plan,
                max_allowed_heat=self.MAX_PORTFOLIO_HEAT,
                max_allowed_single=self.MAX_SINGLE_RISK,
                rationale=f"Single trade risk {single_risk:.2%} > max {self.MAX_SINGLE_RISK:.2%}",
            )

        # 4. Portfolio heat check
        current_heat = self._calculate_heat()
        remaining_heat = self.MAX_PORTFOLIO_HEAT - current_heat
        if remaining_heat <= 0:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED_HEAT,
                plan=plan,
                portfolio_heat=current_heat,
                max_allowed_heat=self.MAX_PORTFOLIO_HEAT,
                max_allowed_single=self.MAX_SINGLE_RISK,
                rationale=f"Portfolio heat {current_heat:.2%} at limit",
            )

        # 5. Correlation check
        if correlation_group:
            same_group = sum(1 for p in self._open_positions.values()
                           if p.get("correlation_group") == correlation_group)
            if same_group >= self.MAX_CORRELATED:
                return RiskDecision(
                    verdict=RiskVerdict.REJECTED_CORRELATION,
                    plan=plan,
                    portfolio_heat=current_heat,
                    max_allowed_heat=self.MAX_PORTFOLIO_HEAT,
                    max_allowed_single=self.MAX_SINGLE_RISK,
                    rationale=f"Already {same_group} trades in '{correlation_group}' group",
                )

        # 6. Scale position to fit remaining heat
        trade_risk = plan.position_size_mult * self.MAX_SINGLE_RISK
        if trade_risk > remaining_heat:
            scale = remaining_heat / trade_risk if trade_risk > 0 else 0
            approved_size = plan.position_size_mult * scale
        else:
            approved_size = plan.position_size_mult

        return RiskDecision(
            verdict=RiskVerdict.APPROVED,
            plan=plan,
            approved_size=round(approved_size, 4),
            portfolio_heat=current_heat,
            max_allowed_heat=self.MAX_PORTFOLIO_HEAT,
            max_allowed_single=self.MAX_SINGLE_RISK,
            rationale=f"Approved at {approved_size:.2%} of base size",
        )

    def _calculate_heat(self) -> float:
        """Current open risk as fraction of capital."""
        heat = sum(
            p.get("risk_pct", 0)
            for p in self._open_positions.values()
        )
        return heat / self._capital if self._capital > 0 else 0.0

    def register_position(self, symbol: str, risk_pct: float, correlation_group: str = ""):
        """Register an open position."""
        self._open_positions[symbol] = {
            "risk_pct": risk_pct,
            "correlation_group": correlation_group,
            "opened_at": datetime.now(timezone.utc).timestamp(),
        }

    def close_position(self, symbol: str, pnl_pct: float = 0.0):
        """Close a position and update capital."""
        if symbol in self._open_positions:
            del self._open_positions[symbol]
        self._current_capital *= (1 + pnl_pct)
        if self._current_capital > self._peak_capital:
            self._peak_capital = self._current_capital

    @property
    def open_count(self) -> int:
        return len(self._open_positions)

    @property
    def current_heat(self) -> float:
        return self._calculate_heat()

    @property
    def drawdown(self) -> float:
        if self._peak_capital <= 0:
            return 0.0
        return 1.0 - (self._current_capital / self._peak_capital)


# Module-level singleton
risk_authority = RiskAuthority()