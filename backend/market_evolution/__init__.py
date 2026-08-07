"""Market Evolution Engine — 16-State Matrix (Dimension 3 Lifecycle).

Exports the public API consumed by signal_fusion.py and the frontend.
"""
from .engine import evaluate, evaluate_for_fusion, evaluate_many, evaluate_from_scores, get_dashboard_stats
from .history import history_store
from .models import FusionContext
from .constants import MARKET_EVOLUTION_MATRIX, SPIRALS, EVOLUTION_LABELS, EVOLUTION_SHORT

__all__ = [
    "evaluate",
    "evaluate_for_fusion",
    "evaluate_many",
    "evaluate_from_scores",
    "get_dashboard_stats",
    "history_store",
    "FusionContext",
    "MARKET_EVOLUTION_MATRIX",
    "SPIRALS",
    "EVOLUTION_LABELS",
    "EVOLUTION_SHORT",
]
