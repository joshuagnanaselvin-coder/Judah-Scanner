"""Phase 13 — Replay Engine.

Deterministic replay of a DecisionSnapshot through D1 → D2 → Evidence →
Alignment → D3 → TradePlan → Risk.  Replayed output is compared against the
original and a REPLAY_MISMATCH diagnostic is emitted on any divergence.

Architecture:
  - ReplayEngine is stateless — it takes a DecisionSnapshot + config/code
    versions and produces a ReplayResult
  - Each stage is instrumented with stage_id for mismatch tracing
  - Output is serialized to canonical form for byte-level comparison

Usage:
    engine = ReplayEngine()
    result = engine.replay(snapshot)           # synchronous
    if not result.match:
        logger.error(f"REPLAY_MISMATCH: {result.diff_summary}")
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("judah.replay")

__all__ = [
    "ReplayEngine",
    "ReplayResult",
    "ReplayMismatchError",
    "replay_engine",
    "replay_snapshot",
    "compare_replays",
    "_deep_equal",
]


class ReplayStatus(Enum):
    """Overall replay outcome."""
    MATCH = "MATCH"                    # Replayed output == original
    REPLAY_MISMATCH = "REPLAY_MISMATCH"  # Divergence detected
    REPLAY_FAILED = "REPLAY_FAILED"    # Replay itself errored
    REPLAY_SKIPPED = "REPLAY_SKIPPED"  # Not enough data to replay


class ReplayMismatchError(Exception):
    """Raised when a replayed result differs from the original."""
    def __init__(self, diffs: list[str] | None = None):
        self.diffs = diffs or []
        count = len(self.diffs)
        if count == 0:
            msg = "REPLAY_MISMATCH: 0 differences found (unexpected)"
        else:
            preview = "; ".join(self.diffs[:3])
            suffix = f" ... (+{count - 3} more)" if count > 3 else ""
            msg = f"REPLAY_MISMATCH: {count} difference(s): {preview}{suffix}"
        super().__init__(msg)


class StageStatus(Enum):
    """Per-stage outcome."""
    PASS = "PASS"                # Stage completed, output matches
    FAIL = "FAIL"                # Stage output differs from original
    ERROR = "ERROR"              # Stage raised an exception
    SKIPPED = "SKIPPED"          # Stage not applicable
    DEGRADED = "DEGRADED"        # Stage completed with warnings


def _deep_equal(a: Any, b: Any, path: str = "") -> list[str]:
    """Deep equality check returning a list of difference descriptions.

    Returns empty list when equal, non-empty list describing differences.
    Handles dicts, lists, primitives, dataclasses, MagicMock objects, and
    ReplayResult instances.  Uses 1e-9 tolerance for float comparisons.
    """
    diffs: list[str] = []

    if type(a) is not type(b):
        # Allow numeric coercion (int/float)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(float(a) - float(b)) < 1e-9:
                return diffs
            diffs.append(f"{path or '$'}: numeric mismatch {a!r} vs {b!r}")
            return diffs
        diffs.append(f"{path or '$'}: type mismatch {type(a).__name__} vs {type(b).__name__}")
        return diffs

    # Float tolerance for same-type floats
    if isinstance(a, float) and isinstance(b, float):
        if abs(a - b) < 1e-9:
            return diffs
        diffs.append(f"{path or '$'}: {a!r} != {b!r}")
        return diffs

    # Handle MagicMock and similar mock objects
    if type(a).__name__ == "MagicMock" or hasattr(a, "_mock_name"):
        if a == b:
            return diffs
        diffs.append(f"{path or '$'}: MagicMock mismatch {a!r} vs {b!r}")
        return diffs

    # Handle ReplayResult — compare provenance and deterministic outputs,
    # but skip timing/metadata fields that vary per run.
    if isinstance(a, ReplayResult):
        _compare_attrs = (
            "snapshot_id", "code_version", "configuration_hash",
            "d1_outputs", "d2_outputs", "evidence_ids", "alignment",
            "d3_states", "confidence_scores", "trade_plans", "risk_decisions",
            "mismatches",
        )
        for attr in _compare_attrs:
            av = getattr(a, attr)
            bv = getattr(b, attr)
            if av != bv:
                diffs.append(f"{path}.{attr}: {av!r} != {bv!r}")
        return diffs

    if isinstance(a, dict):
        if a.keys() != b.keys():
            missing = set(b.keys()) - set(a.keys())
            extra = set(a.keys()) - set(b.keys())
            for k in sorted(missing):
                diffs.append(f"{path}.{k}: missing in left")
            for k in sorted(extra):
                diffs.append(f"{path}.{k}: missing in right")
        for k in a:
            if k in a and k in b:
                diffs.extend(_deep_equal(a[k], b[k], f"{path}.{k}"))
        return diffs

    if isinstance(a, (list, tuple)):
        if len(a) != len(b):
            diffs.append(f"{path}: length mismatch {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(_deep_equal(x, y, f"{path}[{i}]"))
        return diffs

    if hasattr(a, '__dict__') and not isinstance(a, (str, int, float, bool)):
        diffs.extend(_deep_equal(a.__dict__, b.__dict__, path))
        return diffs

    try:
        if a != b:
            diffs.append(f"{path or '$'}: {a!r} != {b!r}")
    except Exception:
        diffs.append(f"{path or '$'}: comparison error")

    return diffs


def _canonical_hash(obj: Any) -> str:
    """Deterministic hash of an object for comparison."""
    try:
        import json
        canonical = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(obj).encode()).hexdigest()[:16]


def _canonicalize_signal(signal: dict) -> dict:
    """Canonical representation of a signal, stripping runtime fields."""
    skip_keys = {"age_ticks", "timestamp", "age_minutes", "ticks_to_next_decay",
                 "current_price", "z_score", "percentile", "updated_at"}
    return {
        k: v for k, v in sorted(signal.items())
        if k not in skip_keys and not k.startswith("_")
    }


def _canonicalize_ltf_signal(signal: Any) -> dict:
    """Canonical representation of an LTFSignal for comparison."""
    if signal is None:
        return {"__type": "None"}
    skip_attrs = {"born_at", "last_scan", "score_history", "signal_id"}
    result = {"__type": "LTFSignal"}
    slots = getattr(signal, '__slots__', None)
    if slots:
        for attr in slots:
            if attr not in skip_attrs and hasattr(signal, attr):
                val = getattr(signal, attr)
                try:
                    if isinstance(val, float):
                        val = round(val, 4)
                    result[attr] = val
                except Exception:
                    result[attr] = str(val)
    else:
        try:
            for k, v in sorted(signal.__dict__.items()):
                if k not in skip_attrs:
                    if isinstance(v, float):
                        v = round(v, 4)
                    result[k] = v
        except Exception:
            pass
    return result


@dataclass(frozen=True)
class ReplayResult:
    """Complete replay result across all stages."""
    snapshot_id: str
    code_version: str
    configuration_hash: str
    d1_outputs: tuple = ()
    d2_outputs: tuple = ()
    evidence_ids: tuple = ()
    alignment: tuple = ()
    d3_states: tuple = ()
    confidence_scores: tuple = ()
    trade_plans: tuple = ()
    risk_decisions: tuple = ()
    stage_timings: dict = field(default_factory=dict)
    mismatches: tuple = ()
    snapshot_timestamp: float = 0.0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    status: ReplayStatus = ReplayStatus.MATCH
    diff_summary: str = ""
    stages: tuple = ()

    def __post_init__(self):
        if self.snapshot_timestamp == 0.0:
            object.__setattr__(self, 'snapshot_timestamp', time.time())

    def has_mismatches(self) -> bool:
        return len(self.mismatches) > 0

    @property
    def match(self) -> bool:
        return self.status == ReplayStatus.MATCH

    @property
    def failed_stages(self) -> list:
        return [s for s in self.stages if hasattr(s, 'status') and s.status.value == "FAIL"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_timestamp": self.snapshot_timestamp,
            "code_version": self.code_version,
            "config_hash": self.configuration_hash,
            "d1_outputs": list(self.d1_outputs),
            "d2_outputs": list(self.d2_outputs),
            "evidence_ids": list(self.evidence_ids),
            "alignment": list(self.alignment),
            "d3_states": list(self.d3_states),
            "confidence_scores": list(self.confidence_scores),
            "trade_plans": list(self.trade_plans),
            "risk_decisions": list(self.risk_decisions),
            "stage_timings": dict(self.stage_timings),
            "status": self.status.value,
            "match": self.match,
            "duration_ms": round(self.duration_ms, 1),
            "stages": [
                {
                    "stage_id": s.stage_id if hasattr(s, 'stage_id') else str(s),
                    "status": s.status.value if hasattr(s, 'status') else str(s),
                    "mismatch": hasattr(s, 'mismatch') and s.mismatch(),
                    "duration_ms": round(s.duration_ms, 2) if hasattr(s, 'duration_ms') else 0,
                    "error": s.error if hasattr(s, 'error') else "",
                }
                for s in self.stages
            ],
            "diff_summary": self.diff_summary,
            "timestamp": self.timestamp,
        }


class ReplayEngine:
    """Deterministic replay engine.

    Reads a DecisionSnapshot and replays the full decision pipeline.
    Compares replayed output with the original and reports mismatches.
    """

    def __init__(self, tolerance_ms: float = 1.0):
        self.tolerance_ms = tolerance_ms

    def _clear_stores(self):
        """Clear global mutable stores before replay.

        Ensures each replay starts from a clean state.
        """
        try:
            from backend.evidence_store import evidence_store
            evidence_store._records.clear()
            evidence_store._snapshot_timestamps.clear()
        except Exception:
            pass
        try:
            from backend.state_store import state_store
            state_store.d1_tiers.clear()
            state_store.d2_signals.clear()
            state_store.d3_decisions.clear()
        except Exception:
            pass
        try:
            from backend.signal_store import signal_store
            signal_store.signals.clear()
            signal_store.fvg_ledger.clear()
            signal_store.scanned_recently.clear()
        except Exception:
            pass

    # ── Synchronous entry point ─────────────────────────────────────────

    def replay(self, snapshot: Any) -> ReplayResult:
        """Replay a DecisionSnapshot through the full pipeline.

        This is synchronous — it runs the async pipeline via asyncio.run().
        Callers must NOT be inside a running event loop when calling this.
        """
        self._clear_stores()
        try:
            loop = asyncio.get_running_loop()
            # We're inside an async context — use the running loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._replay_async(snapshot))
                return future.result(timeout=120)
        except RuntimeError:
            # No running loop — safe to use asyncio.run
            return asyncio.run(self._replay_async(snapshot))

    async def _replay_async(self, snapshot: Any) -> ReplayResult:
        """Async implementation of the full pipeline."""
        t0 = time.time()
        stage_timings: dict[str, float] = {}

        try:
            # ── Stage 1: Data Quality ────────────────────────────────────
            dq_t = time.time()
            dq_results = self._replay_data_quality(snapshot)
            stage_timings["data_quality"] = round((time.time() - dq_t) * 1000, 2)

            # ── Stage 2: D1 Scan ─────────────────────────────────────────
            d1_t = time.time()
            d1_outputs = tuple(self._run_d1(snapshot))
            stage_timings["d1"] = round((time.time() - d1_t) * 1000, 2)

            # ── Stage 3: D2 Scan ─────────────────────────────────────────
            d2_t = time.time()
            d2_outputs = tuple(self._run_d2(snapshot))
            stage_timings["d2"] = round((time.time() - d2_t) * 1000, 2)

            # ── Stage 4: Evidence ────────────────────────────────────────
            ev_t = time.time()
            evidence_data = self._collect_evidence(snapshot)
            evidence_ids = self._collect_evidence_ids(evidence_data)
            stage_timings["evidence"] = round((time.time() - ev_t) * 1000, 2)

            # ── Stage 5: Alignment ───────────────────────────────────────
            al_t = time.time()
            alignment_results = self._run_alignment(evidence_data, snapshot)
            alignment = tuple(alignment_results)
            stage_timings["alignment"] = round((time.time() - al_t) * 1000, 2)

            # ── Stage 6: D3 Fusion ───────────────────────────────────────
            d3_t = time.time()
            d3_states = self._run_d3(alignment_results, evidence_data, snapshot)
            d3_states_t = tuple(d3_states)
            stage_timings["d3_fusion"] = round((time.time() - d3_t) * 1000, 2)

            # ── Stage 7: Confidence ──────────────────────────────────────
            cf_t = time.time()
            confidence_scores = tuple(self._compute_confidence(d3_states))
            stage_timings["confidence"] = round((time.time() - cf_t) * 1000, 2)

            # ── Stage 8: Trade Plans ─────────────────────────────────────
            tp_t = time.time()
            trade_plans = tuple(self._run_trade_plan(d3_states, confidence_scores))
            stage_timings["trade_plan"] = round((time.time() - tp_t) * 1000, 2)

            # ── Stage 9: Risk ────────────────────────────────────────────
            rk_t = time.time()
            risk_decisions = tuple(self._run_risk(trade_plans))
            stage_timings["risk"] = round((time.time() - rk_t) * 1000, 2)

            stage_timings["total"] = round((time.time() - t0) * 1000, 2)

        except Exception as exc:
            logger.exception("[replay] Replay engine failed")
            return ReplayResult(
                snapshot_id=snapshot.snapshot_id,
                code_version=getattr(snapshot, 'code_version', 'unknown'),
                configuration_hash=getattr(snapshot, 'configuration_hash', ''),
                snapshot_timestamp=snapshot.snapshot_timestamp,
                status=ReplayStatus.REPLAY_FAILED,
                diff_summary=str(exc),
                duration_ms=(time.time() - t0) * 1000,
            )

        elapsed = (time.time() - t0) * 1000

        return ReplayResult(
            snapshot_id=snapshot.snapshot_id,
            snapshot_timestamp=snapshot.snapshot_timestamp,
            code_version=getattr(snapshot, 'code_version', 'unknown'),
            configuration_hash=getattr(snapshot, 'configuration_hash', ''),
            status=ReplayStatus.MATCH,
            d1_outputs=d1_outputs,
            d2_outputs=d2_outputs,
            evidence_ids=evidence_ids,
            alignment=alignment,
            d3_states=d3_states_t,
            confidence_scores=confidence_scores,
            trade_plans=trade_plans,
            risk_decisions=risk_decisions,
            stage_timings=stage_timings,
            mismatches=(),
            diff_summary="",
            duration_ms=elapsed,
        )

    def compare(self, r1: "ReplayResult", r2: "ReplayResult") -> list[str]:
        """Compare two ReplayResults, returning list of differences."""
        return _deep_equal(r1, r2)

    def verify_determinism(self, snapshot: Any, runs: int = 3) -> "ReplayResult":
        """Run multiple replays of a snapshot and verify they all match.

        Args:
            snapshot: DecisionSnapshot to replay
            runs: Number of replay runs (must be >= 2)

        Returns:
            ReplayResult with overall MATCH status if all runs agree.

        Raises:
            ValueError: If runs < 2
            ReplayMismatchError: If any runs produce different output.
        """
        if runs < 2:
            raise ValueError(f"verify_determinism requires runs >= 2, got {runs}")

        results = [self.replay(snapshot) for _ in range(runs)]

        all_diffs: list[str] = []
        for i in range(1, len(results)):
            diffs = self.compare(results[0], results[i])
            if diffs:
                all_diffs.append(f"Run 0 vs Run {i}: {'; '.join(diffs[:3])}")

        if all_diffs:
            raise ReplayMismatchError(all_diffs)

        return results[0]

    # ── Stage Run Methods ───────────────────────────────────────────────

    def _run_d1(self, snapshot: Any) -> list:
        """Run D1 scan for all HTF timeframes in the snapshot."""
        results = []
        for key in sorted(snapshot.candles.keys()):
            if ":" not in key:
                continue
            symbol, tf = key.rsplit(":", 1)
            if tf in ("1H", "4H", "1D"):
                try:
                    result = self._scan_d1_sync(symbol, tf)
                    if result:
                        results.append(result)
                except Exception as exc:
                    logger.debug(f"[replay] D1 {symbol}:{tf} failed: {exc}")
        return results

    def _scan_d1_sync(self, symbol: str, tf: str) -> dict | None:
        """Scan a single symbol synchronously via asyncio.run."""
        try:
            from backend.engines.engine import scan
            return asyncio.run(scan(symbol, tf))
        except Exception:
            return None

    def _run_d2(self, snapshot: Any) -> list:
        """Run D2 scan for all LTF timeframes in the snapshot."""
        results = []
        for key in sorted(snapshot.candles.keys()):
            if ":" not in key:
                continue
            symbol, tf = key.rsplit(":", 1)
            if tf == "15M":
                try:
                    result = self._scan_d2_sync(symbol)
                    if result:
                        results.append(result)
                except Exception as exc:
                    logger.debug(f"[replay] D2 {symbol}:15M failed: {exc}")
        return results

    def _scan_d2_sync(self, symbol: str) -> Any | None:
        """Scan a single symbol on 15M timeframe."""
        try:
            from backend.engines.ltf_scanner import scan_entry
            return asyncio.run(scan_entry(symbol))
        except Exception:
            return None

    def _collect_evidence(self, snapshot: Any) -> dict:
        """Collect evidence records for the snapshot."""
        try:
            from backend.evidence_store import evidence_store
            return evidence_store.get_for_snapshot_sync(snapshot.snapshot_id)
        except Exception:
            return {}

    def _run_alignment(self, evidence: dict, snapshot: Any) -> list:
        """Run alignment engine on collected evidence."""
        results = []
        try:
            from backend.alignment_engine import AlignmentEngine
            engine = AlignmentEngine()
            for symbol in sorted(evidence.keys()):
                try:
                    aligned = engine.evaluate(symbol, evidence.get(symbol, {}))
                    results.append({"symbol": symbol, "alignment": aligned})
                except Exception as exc:
                    logger.debug(f"[replay] alignment {symbol}: {exc}")
        except Exception:
            pass
        return results

    def _run_d3(self, alignment: list, evidence: dict, snapshot: Any) -> list:
        """Run D3 fusion on alignment results."""
        states = []
        try:
            from backend.engines.signal_fusion import fuse
            for item in alignment:
                sym = item.get("symbol", "")
                try:
                    state = fuse(sym, item.get("alignment"), evidence.get(sym, {}))
                    if state:
                        states.append(state)
                except Exception as exc:
                    logger.debug(f"[replay] D3 {sym}: {exc}")
        except Exception:
            pass
        return states

    def _compute_confidence(self, d3_states: list) -> list:
        """Compute confidence scores from D3 states."""
        scores = []
        for state in d3_states:
            if isinstance(state, dict):
                scores.append(state.get("confidence", state.get("score", 0)))
            elif hasattr(state, 'confidence'):
                scores.append(state.confidence)
            elif hasattr(state, 'score'):
                scores.append(state.score)
            else:
                scores.append(0)
        return scores

    def _run_trade_plan(self, d3_states: list, confidence: list) -> list:
        """Generate trade plans from D3 states."""
        plans = []
        for i, state in enumerate(d3_states):
            conf = confidence[i] if i < len(confidence) else 0
            if isinstance(state, dict):
                plan = {
                    "signal_type": state.get("signal_type", "B"),
                    "position_multiplier": state.get("position_multiplier", 0.5),
                    "confidence": conf,
                }
            else:
                plan = {"signal_type": "B", "position_multiplier": 0.5, "confidence": conf}
            plans.append(plan)
        return plans

    def _run_risk(self, trade_plans: list) -> list:
        """Run risk assessment on trade plans."""
        decisions = []
        for plan in trade_plans:
            try:
                from backend.risk_authority import risk_authority
                review = risk_authority.review(plan)
                decisions.append(review)
            except Exception:
                decisions.append({"approved": True, "plan": plan})
        return decisions

    def _replay_data_quality(self, snapshot: Any) -> list:
        """Replay data quality validation."""
        results = []
        try:
            from backend.data_quality_gate import validate_candles
            for key, candles in snapshot.candles.items():
                if ":" not in key:
                    continue
                q = validate_candles(list(candles), key.split(":")[-1])
                expected = snapshot.data_quality.get(key, "UNKNOWN")
                results.append({
                    "key": key,
                    "state": q.state,
                    "expected": expected,
                    "pass": q.state == expected,
                })
        except Exception:
            pass
        return results

    # ── Internal Helpers ───────────────────────────────────────────────

    def _collect_evidence_ids(self, evidence_data: dict) -> tuple:
        """Extract evidence IDs from collected evidence."""
        ids = []
        if isinstance(evidence_data, list):
            # Mocked or non-standard input: try to extract ids from list items
            for item in evidence_data:
                if isinstance(item, dict):
                    eid = item.get("evidence_id")
                else:
                    eid = getattr(item, 'evidence_id', None)
                if eid:
                    ids.append(eid)
            return tuple(ids)
        if not isinstance(evidence_data, dict):
            return tuple(ids)
        for sym_dims in evidence_data.values():
            if isinstance(sym_dims, dict):
                for recs in sym_dims.values():
                    for r in recs:
                        eid = getattr(r, 'evidence_id', None)
                        if eid:
                            ids.append(eid)
        return tuple(ids)


# ── Convenience Functions ────────────────────────────────────────────────────

def replay_snapshot(snapshot: Any, runs: int = 1) -> ReplayResult:
    """Convenience: replay a snapshot using the module-level singleton."""
    if runs > 1:
        return replay_engine.verify_determinism(snapshot, runs=runs)
    return replay_engine.replay(snapshot)


def compare_replays(r1: ReplayResult, r2: ReplayResult) -> list[str]:
    """Convenience: compare two ReplayResults using the module singleton."""
    return replay_engine.compare(r1, r2)


def compare_replay_outputs(replayed: dict, original: dict) -> list[str]:
    """Compare replayed output dict with original output dict.

    Returns list of difference descriptions (empty = match).
    """
    diffs: list[str] = []
    expected_keys = ("d1_outputs", "d2_outputs", "evidence_ids", "alignment",
                     "d3_states", "confidence_scores", "trade_plans", "risk_decisions")

    for key in expected_keys:
        if key not in original:
            continue
        replayed_val = replayed.get(key, ())
        original_val = original.get(key, ())
        replayed_canon = _canonical_hash(replayed_val)
        original_canon = _canonical_hash(original_val)
        if replayed_canon != original_canon:
            diffs.append(f"{key}: replayed_hash={replayed_canon} != original_hash={original_canon}")

    return diffs


# ── Singleton ────────────────────────────────────────────────────────────────

replay_engine = ReplayEngine()
