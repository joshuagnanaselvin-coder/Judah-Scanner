"""Market Evolution - Transition History.

Persists the last N state transitions per coin.
Ready for future AI/persistence layer.
"""
from datetime import datetime, timezone
from typing import List

from .models import Transition


MAX_HISTORY = 20


class CoinHistory:
    """Transition history for one coin."""

    def __init__(self, coin: str):
        self.coin = coin
        self._transitions: List[Transition] = []

    def record(self, state: str, spiral: str, direction: str,
               d1_score: float, d2_score: float,
               momentum_velocity: float, evolution: str,
               alignment_score: int = 0,
               institutional_category: str = "",
               trading_decision: str = "",
               evolution_velocity: str = ""):
        """Append a transition record. Truncate to MAX_HISTORY."""
        t = Transition(
            ts=datetime.now(timezone.utc).timestamp(),
            state=state,
            spiral=spiral,
            direction=direction,
            d1_score=d1_score,
            d2_score=d2_score,
            momentum_velocity=momentum_velocity,
            evolution=evolution,
        )
        self._transitions.append(t)
        if len(self._transitions) > MAX_HISTORY:
            self._transitions = self._transitions[-MAX_HISTORY:]

    def to_dict(self) -> list:
        return [t.to_dict() for t in self._transitions]


class HistoryStore:
    """Holds CoinHistory for all tracked coins.

    Phase 16: MAX_COINS cap prevents unbounded growth.
    When cap is reached, the oldest coin (alphabetical sort) is evicted.
    """

    MAX_COINS = 500

    def __init__(self):
        self._store: dict[str, CoinHistory] = {}
        self._last_state: dict[str, str] = {}

    def get_or_create(self, coin: str) -> CoinHistory:
        if coin not in self._store:
            # Phase 16: enforce MAX_COINS before creating new entry
            if len(self._store) >= HistoryStore.MAX_COINS:
                # Evict oldest (alphabetical sort as deterministic proxy)
                oldest = sorted(self._store.keys())[0]
                del self._store[oldest]
                self._last_state.pop(oldest, None)
                logger.debug(f"[history] Evicted {oldest} (cap {HistoryStore.MAX_COINS})")
            self._store[coin] = CoinHistory(coin)
        return self._store[coin]

    def record(self, coin: str, state: str, spiral: str, direction: str,
               d1_score: float, d2_score: float,
               momentum_velocity: float, evolution: str,
               alignment_score: int = 0,
               institutional_category: str = "",
               trading_decision: str = "",
               evolution_velocity: str = ""):
        hist = self.get_or_create(coin)
        hist.record(state, spiral, direction, d1_score, d2_score,
                    momentum_velocity, evolution, alignment_score,
                    institutional_category, trading_decision,
                    evolution_velocity)
        self._last_state[coin] = state

    def get_last_state(self, coin: str) -> str:
        return self._last_state.get(coin)

    def get_history(self, coin: str) -> list:
        h = self._store.get(coin)
        return h.to_dict() if h else []

    def all_history(self) -> dict:
        return {coin: hist.to_dict() for coin, hist in self._store.items()}


# Singleton
history_store = HistoryStore()
