"""Market Evolution - Data Models."""
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from datetime import datetime, timezone


@dataclass
class FusionContext:
    """Explicit input contract for Market Evolution evaluation.

    Phase 7.3 - defines exactly what ME needs from Fusion. Fusion produces
    this object; Market Evolution consumes it. This decouples ME from
    fusion's internal structures.

    Fields
    ------
    coin : str
        Coin symbol (e.g. "BTCUSDT")
    d1_tier : str
        Best D1 tier across timeframes (SNIPER / OPPORTUNITY / WATCH / REJECTED)
    d1_score : float
        Best D1 composite score
    d2_tier : str
        D2 tier derived from score (same thresholds as D1)
    d2_score : float
        D2 composite score
    direction : str
        Signal direction (BULLISH / BEARISH / NEUTRAL)
    alignment_score : int
        HTF/LTF alignment score (0-20), from alignment_engine.compute_alignment()
    """
    coin: str = ""
    d1_tier: str = "REJECTED"
    d1_score: float = 0.0
    d2_tier: str = "REJECTED"
    d2_score: float = 0.0
    direction: str = "BULLISH"
    alignment_score: int = 0


@dataclass
class Transition:
    """A single state transition record."""
    ts: float
    state: str
    spiral: str
    direction: str
    d1_score: float
    d2_score: float
    momentum_velocity: float
    evolution: str
    # Reserved for future AI/persistence layer
    lifecycle: int = 0
    trade_result: Optional[str] = None
    holding_time: Optional[float] = None
    win_loss: Optional[str] = None
    r_multiple: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "state": self.state,
            "spiral": self.spiral,
            "direction": self.direction,
            "d1_score": round(self.d1_score, 1),
            "d2_score": round(self.d2_score, 1),
            "momentum_velocity": round(self.momentum_velocity, 1),
            "evolution": self.evolution,
            "lifecycle": self.lifecycle,
        }


@dataclass
class MarketEvolutionState:
    """The unified Market Evolution output for one coin.

    V5.2 - Institutional Frontend:
      - institutionalCategory: TREND / RE-ENTRY / REVERSAL / DORMANT
      - tradingDecision:       Trade With Trend / Wait For Confirmation /
                               Prepare Pullback Entry / Prepare Reversal /
                               Avoid / No Edge
      - evolutionVelocity:     improving / stable / degrading
      - evolutionConfidence:   blended 0-100% (matrix + D1/D2 + alignment)
    """
    state: str
    description: str
    tradeStyle: str
    action: str
    confidence: int                                # 0-100
    risk: str
    evolution: str
    momentumVelocity: float
    previousState: str
    nextProbableState: str
    spiral: str
    transitionHistory: List[dict] = field(default_factory=list)
    alignmentScore: int = 0                        # V5.1 - 0-20
    institutionalCategory: str = "DORMANT"         # V5.2
    tradingDecision: str = "No Edge"               # V5.2
    evolutionVelocity: str = "stable"              # V5.2
    evolutionConfidence: int = 0                   # V5.2 - blended 0-100

    def to_dict(self) -> dict:
        return asdict(self)
