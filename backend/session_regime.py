"""Session Regime Engine — institutional session expectancy.

Hedge funds know that different market sessions have different characteristics:
  - ASIA (00:00-08:00 UTC): Range-bound, accumulation, low volume
  - LONDON OPEN (08:00-11:00 UTC): Trend initiation, high volume, best signal quality
  - LONDON CLOSE (11:00-12:00 UTC): Reversal territory, profit-taking
  - OVERLAP (13:00-16:00 UTC): Highest volume, best for execution
  - NY OPEN (13:30-16:30 UTC): Continuation, trend extension
  - OFF HOURS (rest): Low quality, avoid trading

Each session gets a different:
  - base_conviction_mult: multiplier for signal quality (0.7-1.3)
  - preferred_signal_types: which D3 signal types perform best
  - min_score_boost: additional score points for good setups

The engine tracks per-session performance metrics (win rate, avg R) and
adjusts session modifiers accordingly.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.config import (
    KILLZONE_LONDON_START, KILLZONE_LONDON_END,
    KILLZONE_NY_START, KILLZONE_NY_END,
    KILLZONE_LONDON_CLOSE_START, KILLZONE_LONDON_CLOSE_END,
)

logger = logging.getLogger("judah.session_regime")

# Session definitions with institutional expectations
SESSION_REGIMES = {
    "ASIA_OPEN": {
        "name": "Asia",
        "utc_start": 0.0,
        "utc_end": 8.0,
        "description": "Range-bound, accumulation. Slow moves, low conviction.",
        "base_conviction_mult": 0.85,       # Lower conviction
        "score_boost": 0,                     # No bonus
        "preferred_types": ["D"],             # Early warning only
        "avoid_types": ["B", "C"],            # Avoid momentum/confluence plays
        "min_tier": "WATCH",                 # Don't trade below WATCH
    },
    "LONDON_OPEN": {
        "name": "London",
        "utc_start": 8.0,
        "utc_end": 11.0,
        "description": "Trend initiation. Best session for new positions.",
        "base_conviction_mult": 1.10,         # Higher conviction
        "score_boost": 3,                     # +3 pts bonus
        "preferred_types": ["A", "C", "B"],   # Structure + confluence + momentum
        "avoid_types": [],
        "min_tier": "WATCH",
    },
    "LONDON_CLOSE": {
        "name": "London Close",
        "utc_start": 11.0,
        "utc_end": 13.5,
        "description": "Reversal territory. Profit-taking, London session winds down.",
        "base_conviction_mult": 0.95,         # Slightly lower
        "score_boost": 0,
        "preferred_types": ["D", "E"],         # Warning + conflict detection
        "avoid_types": ["B"],                  # Avoid fresh momentum
        "min_tier": "WATCH",
    },
    "OVERLAP": {
        "name": "Overlap",
        "utc_start": 13.5,
        "utc_end": 16.5,
        "description": "Highest volume. Both London + NY active. Best execution.",
        "base_conviction_mult": 1.15,         # Highest conviction
        "score_boost": 5,                     # +5 pts bonus
        "preferred_types": ["A", "B", "C"],   # All types preferred
        "avoid_types": [],
        "min_tier": "WATCH",
    },
    "NY_EVENING": {
        "name": "NY Evening",
        "utc_start": 16.5,
        "utc_end": 20.0,
        "description": "NY continuation, then transition to Asia.",
        "base_conviction_mult": 0.95,
        "score_boost": 0,
        "preferred_types": ["A", "D"],
        "avoid_types": ["B"],
        "min_tier": "WATCH",
    },
    "OFF_HOURS": {
        "name": "Off Hours",
        "utc_start": 20.0,
        "utc_end": 24.0,
        "description": "Between NY close and Asia open. Lowest quality.",
        "base_conviction_mult": 0.80,
        "score_boost": 0,
        "preferred_types": ["D"],
        "avoid_types": ["B", "C"],
        "min_tier": "OPPORTUNITY",            # Only OPPORTUNITY+ allowed
    },
}


@dataclass
class SessionState:
    """Tracks per-session performance metrics for adaptive weighting."""
    session: str
    total_signals: int = 0
    wins: int = 0
    losses: int = 0
    total_r: float = 0.0  # Sum of R multiples (wins positive, losses negative)
    sniper_count: int = 0
    opportunity_count: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / max(self.total_signals, 1)

    @property
    def avg_r(self) -> float:
        return self.total_r / max(self.total_signals, 1)

    @property
    def sharpe_approx(self) -> float:
        """Approximate Sharpe from win rate and avg R."""
        if self.total_signals < 3:
            return 0.0
        # Simplified: sharpe ≈ avg_r / std_r
        # For 2-outcome (win R, loss -1): std ≈ (win_rate * (avg_r)^2 + (1-win_rate))^0.5
        wr = self.win_rate
        avg_r = self.avg_r
        variance = wr * (avg_r - wr * avg_r) ** 2 + (1 - wr) * (-1 - wr * avg_r) ** 2
        std = variance ** 0.5
        return avg_r / std if std > 0 else 0.0


class SessionRegimeEngine:
    """Institutional session regime detector with adaptive weighting.

    Tracks performance per session and adjusts modifiers accordingly.
    Start with static institutional defaults, then adapt as real trade data
    comes in from the performance tracker.
    """

    def __init__(self):
        self.states: dict[str, SessionState] = {}
        self._initialized = False

    def get_session_info(self, symbol: str, timeframe: str = "15M") -> dict[str, Any]:
        """Get session regime for the current time.

        Returns dict with:
          - session: session name
          - utc_hour: current UTC hour
          - conviction_mult: multiplier (0.7-1.3)
          - score_boost: bonus points
          - preferred_types: valid D3 signal types
          - avoid_types: signal types to avoid
          - min_tier: minimum tier to trade
          - sharpe: session Sharpe (if enough data)
        """
        utc_hour = self._current_utc_hour()
        session = self._classify_session(utc_hour)
        regime = SESSION_REGIMES.get(session, SESSION_REGIMES["OFF_HOURS"])

        # Adaptive adjustment based on realized performance
        conviction_mult = regime["base_conviction_mult"]
        if self._initialized and session in self.states:
            state = self.states[session]
            if state.total_signals >= 5:
                # If session has positive Sharpe, boost conviction
                if state.sharpe_approx > 0.5:
                    conviction_mult = min(conviction_mult * 1.05, 1.30)
                # If session has negative Sharpe, reduce conviction
                elif state.sharpe_approx < 0.0:
                    conviction_mult = max(conviction_mult * 0.95, 0.70)

        return {
            "session": session,
            "session_name": regime["name"],
            "description": regime["description"],
            "utc_hour": utc_hour,
            "conviction_mult": round(conviction_mult, 3),
            "score_boost": regime["score_boost"],
            "preferred_types": regime["preferred_types"],
            "avoid_types": regime["avoid_types"],
            "min_tier": regime["min_tier"],
            "sharpe": round(self.states.get(session, SessionState(session)).sharpe_approx, 3)
                      if self._initialized else 0.0,
        }

    def should_trade_type(self, signal_type: str, symbol: str, timeframe: str = "15M") -> bool:
        """Check if a signal type should be traded in the current session.

        Returns True if the signal type is preferred or at least not avoided.
        Preferred types get a full conviction mult; neutral types get 0.95x.
        """
        info = self.get_session_info(symbol, timeframe)
        if signal_type in info["preferred_types"]:
            return True
        if signal_type in info["avoid_types"]:
            return False
        return True  # Neutral — allowed but not encouraged

    def get_conviction_multiplier(self, signal_type: str, symbol: str, timeframe: str = "15M") -> float:
        """Get the conviction multiplier for a signal type in the current session.

        Preferred types get full conviction mult, avoided get 0.5x, neutral get 0.95x.
        """
        info = self.get_session_info(symbol, timeframe)
        if signal_type in info["preferred_types"]:
            return info["conviction_mult"]
        if signal_type in info["avoid_types"]:
            return 0.50
        return min(info["conviction_mult"], 0.95)

    def record_trade_outcome(self, symbol: str, signal_type: str, win: bool, r_multiple: float):
        """Record a trade outcome for adaptive session weighting.

        Call this when a trade closes (via performance_tracker).
        """
        session = self._classify_session(self._current_utc_hour())
        if session not in self.states:
            self.states[session] = SessionState(session=session)
            self._initialized = True

        state = self.states[session]
        state.total_signals += 1
        if win:
            state.wins += 1
            state.total_r += r_multiple
        else:
            state.losses += 1
            state.total_r += r_multiple  # r_multiple is negative for losses

        # Track signal type distribution
        if signal_type == "C":
            state.sniper_count += 1
        elif signal_type in ("A", "B"):
            state.opportunity_count += 1

    def get_stats(self) -> dict:
        """Return session performance stats."""
        return {
            session: {
                "total_signals": s.total_signals,
                "win_rate": round(s.win_rate, 3),
                "avg_r": round(s.avg_r, 3),
                "sharpe": round(s.sharpe_approx, 3),
            }
            for session, s in self.states.items()
        }

    def _classify_session(self, utc_hour: float) -> str:
        """Classify current hour into a session regime."""
        # Order matters: most-specific first
        # OVERLAP (NY open during London close) — highest priority
        if KILLZONE_NY_START <= utc_hour < KILLZONE_NY_END:
            return "OVERLAP"
        # LONDON_CLOSE (10:30-12:00 UTC) — must check before LONDON_OPEN
        if KILLZONE_LONDON_CLOSE_START <= utc_hour < KILLZONE_LONDON_CLOSE_END:
            return "LONDON_CLOSE"
        # LONDON_OPEN (08:00-10:30 UTC)
        if KILLZONE_LONDON_START <= utc_hour < KILLZONE_LONDON_CLOSE_START:
            return "LONDON_OPEN"
        # OVERLAP (fallback: 13:30-16:30 UTC)
        if 13.5 <= utc_hour < 16.5:
            return "OVERLAP"
        # ASIA (00:00-08:00 UTC)
        if 0.0 <= utc_hour < 8.0:
            return "ASIA_OPEN"
        # NY Evening (16:30-20:00 UTC)
        if 16.5 <= utc_hour < 20.0:
            return "NY_EVENING"
        return "OFF_HOURS"

    @staticmethod
    def _current_utc_hour() -> float:
        """Get current UTC hour as float (e.g. 8.5 = 08:30 UTC)."""
        now = datetime.now(timezone.utc)
        return now.hour + now.minute / 60.0


# Module-level singleton
session_regime = SessionRegimeEngine()
