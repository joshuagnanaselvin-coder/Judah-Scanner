"""Phase 19 — Failure Safety Policies.

Defines and enforces timeout/stale/malformed/missing/network behavior
for every external dependency in the scanner pipeline.

Dependencies:
  1. Bybit REST API  — candle data, funding rates, open interest
  2. Bybit WebSocket  — real-time ticker, trade, candle updates
  3. EvidenceStore    — internal evidence aggregation (can be corrupted)
  4. StateStore       — internal state (can be corrupted)

Policy: DO NOT GENERATE SIGNAL if any dependency is in FAILED state.
        DO NOT PROPAGATE degraded data to downstream stages without marking.
        Always emit DEGRADED or FAILED status — never silently skip.

Ownership:
  Policy:    System-level (this file)
  Enforcement: Each pipeline stage (scanner.py, ltf_engine.py, signal_fusion.py)
  Monitoring: Health checks, logs, WS status events
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger("judah.failure")


class DependencyStatus(Enum):
    """Health status for an external dependency."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class FailureAction(Enum):
    """What to do when a dependency fails."""
    SKIP_COIN = "SKIP_COIN"           # Skip this coin, continue others
    SKIP_CYCLE = "SKIP_CYCLE"         # Abort entire cycle, retry next cycle
    USE_STALE = "USE_STALE"           # Use last known good data with warning
    ABORT_PIPELINE = "ABORT_PIPELINE" # Hard stop, require manual intervention


@dataclass(frozen=True)
class DependencyPolicy:
    """Failure policy for one external dependency."""
    name: str
    timeout_sec: float
    stale_after_sec: float
    max_retries: int
    retry_backoff_sec: float
    failure_action: FailureAction
    emit_degraded: bool = True


# ── Dependency Policies ────────────────────────────────────────────────

# Bybit REST API — candle data
REST_POLICY = DependencyPolicy(
    name="bybit_rest",
    timeout_sec=10.0,
    stale_after_sec=300.0,       # 5 min — market data gets stale fast
    max_retries=2,
    retry_backoff_sec=1.0,
    failure_action=FailureAction.SKIP_COIN,
    emit_degraded=True,
)

# Bybit WebSocket — live updates
WS_POLICY = DependencyPolicy(
    name="bybit_ws",
    timeout_sec=5.0,
    stale_after_sec=60.0,        # 1 min — WS data must be fresh
    max_retries=3,
    retry_backoff_sec=0.5,
    failure_action=FailureAction.SKIP_CYCLE,
    emit_degraded=True,
)

# EvidenceStore — internal evidence aggregation
EVIDENCE_POLICY = DependencyPolicy(
    name="evidence_store",
    timeout_sec=0.0,             # Internal — no network timeout
    stale_after_sec=900.0,       # 15 min — evidence lives for signal TTL
    max_retries=0,
    retry_backoff_sec=0.0,
    failure_action=FailureAction.SKIP_COIN,
    emit_degraded=True,
)

# StateStore — internal shared state
STATE_POLICY = DependencyPolicy(
    name="state_store",
    timeout_sec=0.0,
    stale_after_sec=0.0,         # No TTL — explicit clear/refresh
    max_retries=0,
    retry_backoff_sec=0.0,
    failure_action=FailureAction.ABORT_PIPELINE,
    emit_degraded=True,
)


# ── Failure State Tracker ──────────────────────────────────────────────

@dataclass
class _DepState:
    """Runtime state for one dependency."""
    status: DependencyStatus = DependencyStatus.UNKNOWN
    last_ok: float = 0.0
    fail_count: int = 0
    last_error: str = ""
    updated_at: float = 0.0


class FailureTracker:
    """Tracks health of all external dependencies.

    Thread-safe for sync/async access via threading.Lock.
    """

    def __init__(self):
        self._states: dict[str, _DepState] = {
            "bybit_rest": _DepState(),
            "bybit_ws": _DepState(),
            "evidence_store": _DepState(),
            "state_store": _DepState(),
        }
        self._lock = threading.Lock()

    def record_success(self, name: str):
        with self._lock:
            self._states[name] = _DepState(
                status=DependencyStatus.HEALTHY,
                last_ok=time.time(),
                fail_count=0,
                last_error="",
                updated_at=time.time(),
            )

    def record_failure(self, name: str, error: str = "", policy: DependencyPolicy | None = None):
        with self._lock:
            state = self._states.get(name)
            if state is None:
                return
            new_fail = state.fail_count + 1
            if new_fail >= policy.max_retries if policy else new_fail >= 3:
                status = DependencyStatus.FAILED
            else:
                status = DependencyStatus.DEGRADED
            self._states[name] = _DepState(
                status=status,
                last_ok=state.last_ok,
                fail_count=new_fail,
                last_error=str(error)[:200],
                updated_at=time.time(),
            )
            logger.warning(f"[failure] {name} → {status.value} (failures={new_fail}): {error[:100]}")

    def record_stale(self, name: str):
        with self._lock:
            state = self._states.get(name)
            if state is None:
                return
            if state.status != DependencyStatus.FAILED:
                self._states[name] = _DepState(
                    status=DependencyStatus.DEGRADED,
                    last_ok=state.last_ok,
                    fail_count=state.fail_count,
                    last_error=f"stale since {state.updated_at:.0f}",
                    updated_at=time.time(),
                )
            logger.warning(f"[failure] {name} → DEGRADED (stale data)")

    def get_status(self, name: str) -> DependencyStatus:
        with self._lock:
            state = self._states.get(name)
            if state is None:
                return DependencyStatus.UNKNOWN
            # Check staleness
            import time as _time
            age = time.time() - state.updated_at
            policy_map = {
                "bybit_rest": REST_POLICY,
                "bybit_ws": WS_POLICY,
                "evidence_store": EVIDENCE_POLICY,
                "state_store": STATE_POLICY,
            }
            policy = policy_map.get(name)
            if policy and age > policy.stale_after_sec and state.status == DependencyStatus.HEALTHY:
                return DependencyStatus.DEGRADED
            return state.status

    def is_healthy(self, name: str) -> bool:
        return self.get_status(name) == DependencyStatus.HEALTHY

    def is_failed(self, name: str) -> bool:
        return self.get_status(name) == DependencyStatus.FAILED

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                name: {
                    "status": s.status.value,
                    "fail_count": s.fail_count,
                    "last_error": s.last_error[:100] if s.last_error else "",
                    "updated_at": round(s.updated_at, 1),
                }
                for name, s in self._states.items()
            }


# Singleton
failure_tracker = FailureTracker()

# ── Policy Enforcement Helpers ─────────────────────────────────────────


def should_generate_signal() -> bool:
    """Check if any dependency has FAILED status.

    Policy: DO NOT GENERATE SIGNAL if any dependency is FAILED.
    This prevents generating signals from stale/corrupt data.
    """
    for name in failure_tracker._states:
        if failure_tracker.is_failed(name):
            logger.error(f"[failure] BLOCKING signal generation — {name} is FAILED")
            return False
    return True


def check_dependency(name: str, policy: DependencyPolicy) -> DependencyStatus:
    """Check a dependency against its policy.

    Returns the current status. Logs warning if DEGRADED/FAILED.
    Caller should act based on policy.failure_action.
    """
    status = failure_tracker.get_status(name)
    if status == DependencyStatus.DEGRADED:
        logger.warning(f"[failure] {name} DEGRADED — action={policy.failure_action.value}")
    elif status == DependencyStatus.FAILED:
        logger.error(f"[failure] {name} FAILED — action={policy.failure_action.value}")
    return status
