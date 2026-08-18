"""Phase 18 — Performance Engineering.

Instruments P50/P95/P99 timing for every pipeline stage.

Usage:
    from backend.performance_monitor import perf_monitor

    with perf_monitor.timer("d1_scan"):
        result = await d1_scan_coin(coin)

    # At end of cycle:
    report = perf_monitor.report()
    logger.info(f"[perf] cycle_report: {report}")
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("judah.perf")

# Rolling window for histogram
_HISTORY_MAX = 500


@dataclass
class _StageSample:
    """Single timing sample for one stage."""
    duration_ms: float
    timestamp: float
    success: bool


@dataclass
class _StageStats:
    """Running stats for a single pipeline stage."""
    history: deque = field(default_factory=lambda: deque(maxlen=_HISTORY_MAX))
    total_samples: int = 0
    error_count: int = 0
    last_ms: float = 0.0

    def record(self, duration_ms: float, success: bool = True):
        self.history.append(duration_ms)
        self.total_samples += 1
        self.last_ms = duration_ms
        if not success:
            self.error_count += 1

    @property
    def percentiles(self) -> dict[str, float]:
        if not self.history:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        sorted_vals = sorted(self.history)
        n = len(sorted_vals)

        def _pct(p):
            idx = int(n * p / 100)
            idx = min(idx, n - 1)
            return sorted_vals[idx]

        return {
            "p50": round(_pct(50), 1),
            "p95": round(_pct(95), 1),
            "p99": round(_pct(99), 1),
        }

    @property
    def avg(self) -> float:
        if not self.history:
            return 0.0
        return round(sum(self.history) / len(self.history), 1)

    @property
    def max(self) -> float:
        if not self.history:
            return 0.0
        return round(max(self.history), 1)

    def to_dict(self) -> dict[str, Any]:
        pcts = self.percentiles
        return {
            "samples": self.total_samples,
            "errors": self.error_count,
            "last_ms": round(self.last_ms, 1),
            "avg_ms": self.avg,
            "max_ms": self.max,
            "p50_ms": pcts["p50"],
            "p95_ms": pcts["p95"],
            "p99_ms": pcts["p99"],
        }


class _Timer:
    """Context manager for timing a stage."""

    def __init__(self, monitor: "PerformanceMonitor", stage: str, success: bool = True):
        self._monitor = monitor
        self._stage = stage
        self._success = success
        self._t0: float = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *args):
        elapsed = (time.perf_counter() - self._t0) * 1000
        self._monitor.record(self._stage, elapsed, self._success)
        return False


class PerformanceMonitor:
    """Thread-safe performance monitor with rolling percentiles.

    Thread-safe for concurrent access — uses threading.Lock because
    this monitor is accessed from both sync and async contexts.
    """

    def __init__(self):
        self._stages: dict[str, _StageStats] = defaultdict(_StageStats)
        self._lock = threading.Lock()
        self._cycle_count = 0

    def record(self, stage: str, duration_ms: float, success: bool = True):
        with self._lock:
            self._stages[stage].record(duration_ms, success)

    def timer(self, stage: str, success: bool = True) -> _Timer:
        return _Timer(self, stage, success)

    def cycle_complete(self):
        with self._lock:
            self._cycle_count += 1

    def get_stats(self, stage: str) -> dict[str, Any]:
        with self._lock:
            return self._stages[stage].to_dict()

    def report(self) -> dict[str, Any]:
        with self._lock:
            stages = {name: stats.to_dict() for name, stats in self._stages.items()}
            return {
                "cycle": self._cycle_count,
                "stages": stages,
                "stage_count": len(stages),
            }

    def reset(self):
        with self._lock:
            self._stages.clear()
            self._cycle_count = 0
            logger.info("[perf] Performance monitor reset")


# Singleton — thread-safe, accessed from sync and async contexts
perf_monitor = PerformanceMonitor()


# Pipeline stage names (canonical list for consistent naming)
PIPELINE_STAGES = [
    "market_data",     # Candle fetch from Bybit
    "snapshot",        # DecisionSnapshot creation
    "d1_scan",         # HTF scanner (1H/4H/1D)
    "d2_scan",         # LTF scanner (15M)
    "evidence_write",  # D1/D2 → EvidenceStore
    "alignment",       # AlignmentEngine.evaluate
    "d3_fusion",       # Signal fusion per coin
    "broadcast",       # WebSocket broadcast
    "total_cycle",     # End-to-end cycle
]
