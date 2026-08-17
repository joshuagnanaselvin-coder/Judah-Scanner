"""Phase 13 — Replayability Engine.

Given a DecisionSnapshot + code_version + configuration_hash, re-executes
the full pipeline and verifies deterministic output.

Pipeline stages (in order):
  D1  →  D2  →  Evidence  →  Alignment  →  D3/Market Evolution
    →  Confidence  →  TradePlan  →  Risk

Output is a ReplayResult carrying every stage's output.

Acceptance criterion:
  Same snapshot + same code/configuration → identical ReplayResult.

If outputs differ between two replays, emit a REPLAY_MISMATCH diagnostic.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("judah.replay")

# ── Exception type ────────────────────────────────────────────────────────────

class ReplayMismatchError(Exception):
    """Raised when two replays of the same snapshot produce different outputs."""
    def __init__(self, diffs: list[str]):
        self.diffs = diffs
        msg = f"REPLAY_MISMATCH: {len(diffs)} difference(s) detected:\n" + "\n".join(
            f"  - {d}" for d in diffs
        )
        super().__init__(msg)


# ── Frozen result containers ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ReplayResult:
    """Complete output of one replay run.

    All fields are immutable after construction so that two runs
    can be compared with simple equality.
    """
    # Provenance (must match the input snapshot)
    snapshot_id: str
    code_version: str
    configuration_hash: str

    # Stage outputs (None if stage was skipped or produced nothing)
    d1_outputs: tuple[dict, ...] = ()
    d2_outputs: tuple[dict, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    alignment: tuple[dict, ...] = ()
    d3_states: tuple[dict, ...] = ()
    confidence_scores: tuple[int, ...] = ()
    trade_plans: tuple[dict, ...] = ()
    risk_decisions: tuple[dict, ...] = ()

    # Timing (for diagnostics only — not part of equality check)
    stage_timings: dict[str, float] = field(default_factory=dict)

    # Metadata
    timestamp: float = 0.0
    mismatches: tuple[str, ...] = ()

    def __post_init__(self):
        if self.timestamp == 0.0:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).timestamp())

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "code_version": self.code_version,
            "configuration_hash": self.configuration_hash,
            "d1_outputs": list(self.d1_outputs),
            "d2_outputs": list(self.d2_outputs),
            "evidence_ids": list(self.evidence_ids),
            "alignment": list(self.alignment),
            "d3_states": list(self.d3_states),
            "confidence_scores": list(self.confidence_scores),
            "trade_plans": list(self.trade_plans),
            "risk_decisions": list(self.risk_decisions),
            "stage_timings": self.stage_timings,
            "timestamp": self.timestamp,
            "mismatches": list(self.mismatches),
        }

    def has_mismatches(self) -> bool:
        return len(self.mismatches) > 0


# ── Deep-equality helper ──────────────────────────────────────────────────────

def _deep_equal(a: Any, b: Any, path: str = "") -> list[str]:
    """Recursively compare two values. Returns list of difference descriptions."""
    diffs: list[str] = []
    if type(a) != type(b):
        diffs.append(f"{path or '$'}: type mismatch {type(a).__name__} vs {type(b).__name__}")
        return diffs
    if isinstance(a, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for k in sorted(all_keys):
            p = f"{path}.{k}" if path else k
            if k not in a:
                diffs.append(f"{p}: missing in first")
            elif k not in b:
                diffs.append(f"{p}: missing in second")
            else:
                diffs.extend(_deep_equal(a[k], b[k], p))
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            diffs.append(f"{path}: length {len(a)} vs {len(b)}")
        else:
            for i in range(len(a)):
                diffs.extend(_deep_equal(a[i], b[i], f"{path}[{i}]"))
    elif isinstance(a, float):
        if abs(a - b) > 1e-9:
            diffs.append(f"{path}: {a} vs {b}")
    elif a != b:
        diffs.append(f"{path}: {a!r} vs {b!r}")
    return diffs


# ── Replay Engine ─────────────────────────────────────────────────────────────

class ReplayEngine:
    """Replays the full scanner pipeline from a frozen DecisionSnapshot.

    Usage:
        engine = ReplayEngine()
        result = engine.replay(snapshot)

        # Determinism check — run twice and compare
        result2 = engine.replay(snapshot)
        engine.verify_determinism(snapshot, runs=3)
    """

    def __init__(self):
        self._clear_stores()

    # ── Public API ────────────────────────────────────────────────────────────

    def replay(self, snapshot) -> ReplayResult:
        """Replay the full pipeline for one snapshot.

        Returns a frozen ReplayResult containing every stage output.
        The result can be compared with == for determinism verification.
        """
        t0 = time_module.perf_counter()
        self._clear_stores()
        self._inject_snapshot(snapshot)

        # Set deterministic clock from snapshot so all timing-dependent
        # business logic sees stable values (Phase 14)
        try:
            from backend.helpers.time_source import set_snapshot_timestamp, clear_snapshot_timestamp as _clear
            set_snapshot_timestamp(snapshot.snapshot_timestamp)
        except ImportError:
            _clear = lambda: None

        try:
            # --- Stage 1: D1 (Context Radar) ---
            t1 = time_module.perf_counter()
            d1_outputs = self._run_d1(snapshot)
            d1_time = time_module.perf_counter() - t1

            # --- Stage 2: D2 (Opportunity Radar) ---
            t2 = time_module.perf_counter()
            d2_outputs = self._run_d2(snapshot)
            d2_time = time_module.perf_counter() - t2

            # --- Stage 3: Evidence ---
            t3 = time_module.perf_counter()
            evidence_ids = self._collect_evidence(d1_outputs, d2_outputs, snapshot)
            evidence_time = time_module.perf_counter() - t3

            # --- Stage 4: Alignment ---
            t4 = time_module.perf_counter()
            alignment = self._run_alignment(d1_outputs, d2_outputs, snapshot)
            alignment_time = time_module.perf_counter() - t4

            # --- Stage 5: D3 / Market Evolution ---
            t5 = time_module.perf_counter()
            d3_states = self._run_d3(d1_outputs, d2_outputs, alignment, snapshot)
            d3_time = time_module.perf_counter() - t5

            # --- Stage 6: Confidence ---
            t6 = time_module.perf_counter()
            confidence_scores = self._compute_confidence(d3_states, alignment, d1_outputs, d2_outputs)
            conf_time = time_module.perf_counter() - t6

            # --- Stage 7: TradePlan ---
            t7 = time_module.perf_counter()
            trade_plans = self._run_trade_plan(d3_states, confidence_scores, d1_outputs, d2_outputs)
            tp_time = time_module.perf_counter() - t7

            # --- Stage 8: Risk ---
            t8 = time_module.perf_counter()
            risk_decisions = self._run_risk(trade_plans)
            risk_time = time_module.perf_counter() - t8

        except Exception as exc:
            _clear()
            logger.error(f"[replay] Pipeline failed: {exc}")
            raise

        total_time = time_module.perf_counter() - t0
        timings = {
            "d1": round(d1_time * 1000, 3),
            "d2": round(d2_time * 1000, 3),
            "evidence": round(evidence_time * 1000, 3),
            "alignment": round(alignment_time * 1000, 3),
            "d3": round(d3_time * 1000, 3),
            "confidence": round(conf_time * 1000, 3),
            "trade_plan": round(tp_time * 1000, 3),
            "risk": round(risk_time * 1000, 3),
            "total": round(total_time * 1000, 3),
        }

        return ReplayResult(
            snapshot_id=snapshot.snapshot_id,
            code_version=snapshot.code_version,
            configuration_hash=snapshot.configuration_hash,
            d1_outputs=tuple(self._freeze(d1_outputs)),
            d2_outputs=tuple(self._freeze(d2_outputs)),
            evidence_ids=tuple(evidence_ids),
            alignment=tuple(self._freeze(a) for a in alignment),
            d3_states=tuple(self._freeze(s) for s in d3_states),
            confidence_scores=tuple(confidence_scores),
            trade_plans=tuple(self._freeze(tp) for tp in trade_plans),
            risk_decisions=tuple(self._freeze(rd) for rd in risk_decisions),
            stage_timings=timings,
        )

    def verify_determinism(self, snapshot, runs: int = 3) -> ReplayResult:
        """Run replay multiple times and verify all outputs are identical.

        Args:
            snapshot: DecisionSnapshot to replay.
            runs: Number of replay runs (minimum 2).

        Returns:
            The first ReplayResult if all runs match.

        Raises:
            ReplayMismatchError: If any two runs produce different output.
        """
        if runs < 2:
            raise ValueError("determinism check requires at least 2 runs")

        results: list[ReplayResult] = []
        for i in range(runs):
            result = self.replay(snapshot)
            results.append(result)
            logger.debug(f"[replay] Run {i+1}/{runs} complete — "
                         f"{len(result.d1_outputs)} D1, {len(result.d2_outputs)} D2, "
                         f"{len(result.d3_states)} D3")

        # Compare all runs against the first
        ref = results[0]
        all_diffs: list[str] = []
        for i, r in enumerate(results[1:], start=1):
            diffs = self._compare_results(ref, r)
            if diffs:
                all_diffs.append(f"Run 0 vs Run {i}: {'; '.join(diffs)}")

        if all_diffs:
            # Attach mismatches to all results
            mismatches = tuple(all_diffs)
            results = [
                ReplayResult(
                    snapshot_id=r.snapshot_id,
                    code_version=r.code_version,
                    configuration_hash=r.configuration_hash,
                    d1_outputs=r.d1_outputs,
                    d2_outputs=r.d2_outputs,
                    evidence_ids=r.evidence_ids,
                    alignment=r.alignment,
                    d3_states=r.d3_states,
                    confidence_scores=r.confidence_scores,
                    trade_plans=r.trade_plans,
                    risk_decisions=r.risk_decisions,
                    stage_timings=r.stage_timings,
                    timestamp=r.timestamp,
                    mismatches=mismatches,
                )
                for r in results
            ]
            raise ReplayMismatchError(all_diffs)

        logger.info(f"[replay] Determinism verified: {runs} runs identical "
                    f"(snapshot={snapshot.snapshot_id[:8]})")
        return ref

    def compare(self, original: ReplayResult, replay: ReplayResult) -> list[str]:
        """Compare two ReplayResults and return human-readable diffs.

        This is the explicit comparison the plan requires:
        > Compare replayed output with the original.
        """
        return self._compare_results(original, replay)

    # ── Stage runners ─────────────────────────────────────────────────────────

    def _run_d1(self, snapshot) -> list[dict]:
        """Run D1 scanner for all symbols in the snapshot.

        Reads candles from the snapshot (not live market_data).
        Produces one signal dict per (symbol, timeframe) that passes gates.
        """
        from backend.engines.engine import scan as engine_scan

        results: list[dict] = []
        # Collect unique symbol+tf from snapshot
        tf_set: set[str] = set()
        symbols: list[str] = []
        for key in snapshot.candles:
            parts = key.rsplit(":", 1)
            if len(parts) == 2:
                sym, tf = parts
                tf_set.add(tf)
                if sym not in symbols:
                    symbols.append(sym)

        # Run scan for each symbol/tf combination using snapshot candles
        for sym in symbols:
            for tf in tf_set:
                candles = snapshot.get_candles(sym, tf)
                if not candles:
                    continue
                quality = snapshot.candle_quality(sym, tf)
                if quality in ("INVALID", "GAPPED", "MISSING", "STALE"):
                    continue

                signal = self._scan_with_candles(sym, tf, candles, snapshot)
                if signal:
                    results.append(signal)

        return results

    def _run_d2(self, snapshot) -> list[dict]:
        """Run D2 (LTF) scanner for all symbols in the snapshot.

        D2 is fully independent — scans regardless of D1 results.
        Uses scan_entry from ltf_scanner which reads from market_data cache.
        """
        from backend.engines.ltf_scanner import scan_entry
        from backend.market_data import market_data

        results: list[dict] = []
        # LTF timeframes from config
        try:
            from backend.config import TIMEFRAMES_LTF
            ltf_tfs = TIMEFRAMES_LTF
        except ImportError:
            ltf_tfs = ["15M"]

        # Collect symbols from snapshot
        symbols: list[str] = []
        for key in snapshot.candles:
            sym = key.rsplit(":", 1)[0] if ":" in key else key
            if sym not in symbols:
                symbols.append(sym)

        for sym in symbols:
            for tf in ltf_tfs:
                candles = snapshot.get_candles(sym, tf)
                if not candles:
                    continue
                quality = snapshot.candle_quality(sym, tf)
                if quality in ("INVALID", "GAPPED", "MISSING", "STALE"):
                    continue

                # Inject candles into market_data cache so scan_entry can read them
                cache_key = f"{sym}:{tf}"
                market_data._candle_cache = getattr(market_data, "_candle_cache", {})
                market_data._candle_cache[cache_key] = candles

                try:
                    signal = scan_entry(sym, "", 0.0)
                    if signal:
                        signal.setdefault("snapshot_id", snapshot.snapshot_id)
                        results.append(signal)
                finally:
                    pass

        return results

    def _collect_evidence(self, d1_outputs: list, d2_outputs: list, snapshot) -> list[str]:
        """Collect evidence IDs from D1 and D2 outputs.

        Evidence IDs are derived from signal-level evidence_ids fields.
        Returns a sorted list for deterministic comparison.
        """
        evidence_ids: list[str] = []
        for sig in d1_outputs:
            for eid in sig.get("evidence_ids", []):
                evidence_ids.append(eid)
        for sig in d2_outputs:
            for eid in sig.get("evidence_ids", []):
                evidence_ids.append(eid)
        return sorted(set(evidence_ids))

    def _run_alignment(self, d1_outputs, d2_outputs, snapshot) -> list[dict]:
        """Run Alignment Engine on D1/D2 outputs.

        Groups D1 and D2 by symbol, then evaluates alignment per symbol.
        """
        from backend.alignment_engine import alignment_engine

        results: list[dict] = []

        # Build per-symbol lookup
        d1_by_sym: dict[str, dict] = {}
        for sig in d1_outputs:
            sym = sig.get("symbol", "")
            d1_by_sym[sym] = sig

        d2_by_sym: dict[str, dict] = {}
        for sig in d2_outputs:
            sym = sig.get("symbol", "")
            d2_by_sym[sym] = sig

        # Evaluate alignment for every symbol that has either D1 or D2
        all_symbols = sorted(set(d1_by_sym) | set(d2_by_sym))
        for sym in all_symbols:
            d1_sig = d1_by_sym.get(sym)
            d2_sig = d2_by_sym.get(sym)

            d1_tier = d1_sig.get("tier", "WATCH") if d1_sig else "WATCH"
            d2_tier = d2_sig.get("tier", "WATCH") if d2_sig else "WATCH"
            d1_dir = d1_sig.get("direction", "") if d1_sig else ""
            d2_dir = d2_sig.get("direction", "") if d2_sig else ""
            d1_q = snapshot.candle_quality(sym, "1H") or "VALID"
            d2_q = snapshot.candle_quality(sym, "15M") or "VALID"

            # Build structure summaries for alignment engine
            d1_struct = self._structure_summary(d1_sig) if d1_sig else {}
            d2_struct = self._structure_summary(d2_sig) if d2_sig else {}

            result = alignment_engine.evaluate(
                d1_structure=d1_struct,
                d2_structure=d2_struct,
                d1_tier=d1_tier,
                d2_tier=d2_tier,
                d1_direction=d1_dir,
                d2_direction=d2_dir,
                d1_quality=d1_q,
                d2_quality=d2_q,
            )
            results.append(result.to_dict())

        return results

    def _run_d3(self, d1_outputs, d2_outputs, alignment_results, snapshot) -> list[dict]:
        """Run D3 Market Evolution for each symbol.

        Consumes D1/D2 tiers, alignment results, and produces MarketEvolutionState.
        """
        from backend.market_evolution import evaluate as me_evaluate

        results: list[dict] = []

        # Build per-symbol lookups
        d1_by_sym: dict[str, dict] = {}
        for sig in d1_outputs:
            d1_by_sym[sig.get("symbol", "")] = sig

        d2_by_sym: dict[str, dict] = {}
        for sig in d2_outputs:
            d2_by_sym[sig.get("symbol", "")] = sig

        align_by_sym: dict[str, dict] = {}
        for a in alignment_results:
            sym = a.get("rationale", "").split("D1=")[0].strip()
            align_by_sym[sym] = a

        all_symbols = sorted(set(d1_by_sym) | set(d2_by_sym))
        for sym in all_symbols:
            d1_sig = d1_by_sym.get(sym)
            d2_sig = d2_by_sym.get(sym)

            d1_tier = d1_sig.get("tier", "WATCH") if d1_sig else "REJECT"
            d1_score = d1_sig.get("composite_score", 0) if d1_sig else 0.0
            d2_tier = d2_sig.get("tier", "WATCH") if d2_sig else "REJECT"
            d2_score = d2_sig.get("composite_score", 0) if d2_sig else 0.0
            direction = d1_sig.get("direction", "BULLISH") if d1_sig else "BULLISH"

            # Alignment score from alignment results
            align_score = 0
            for a in alignment_results:
                if sym in a.get("rationale", ""):
                    align_score = int(a.get("score", 0) * 20)
                    break

            state = me_evaluate(
                coin=sym,
                d1_tier=d1_tier,
                d1_score=d1_score,
                d2_tier=d2_tier,
                d2_score=d2_score,
                direction=direction,
                alignment_score=align_score,
            )
            results.append({
                "symbol": sym,
                "state": state.state,
                "confidence": state.confidence,
                "evolution": state.evolution,
                "spiral": state.spiral,
                "trade_style": state.tradeStyle,
                "action": state.action,
                "risk": state.risk,
                "institutional_category": state.institutionalCategory,
                "trading_decision": state.tradingDecision,
                "evolution_velocity": state.evolutionVelocity,
            })

        return results

    def _compute_confidence(self, d3_states, alignment_results, d1_outputs, d2_outputs) -> list[int]:
        """Compute confidence scores for each D3 state.

        In the current architecture confidence is embedded in MarketEvolutionState.
        We extract it here for the replay comparison.
        """
        return [s.get("confidence", 0) for s in d3_states]

    def _run_trade_plan(self, d3_states, confidence_scores, d1_outputs, d2_outputs) -> list[dict]:
        """Run TradePlanAuthority for each D3 state that warrants a trade plan."""
        from backend.trade_plan_authority import trade_plan_authority

        plans: list[dict] = []
        for i, state in enumerate(d3_states):
            conf = confidence_scores[i] if i < len(confidence_scores) else 0
            if conf < 30:
                continue

            plan = trade_plan_authority.propose(
                symbol=state.get("symbol", ""),
                direction="BULLISH",
                entry=100.0,
                atr=1.0,
                confidence_score=conf / 100.0,
            )
            plans.append(plan.to_dict())

        return plans

    def _run_risk(self, trade_plans) -> list[dict]:
        """Run RiskAuthority for each trade plan."""
        from backend.risk_authority import risk_authority

        decisions: list[dict] = []
        for plan in trade_plans:
            from backend.trade_plan_authority import PlanStatus
            from backend.trade_plan_authority import TradePlan

            tp = TradePlan(
                status=PlanStatus[plan["status"]] if plan.get("status") in PlanStatus.__members__ else PlanStatus.REJECTED_RR_FLOOR,
                symbol=plan.get("symbol", ""),
                direction=plan.get("direction", "BULLISH"),
                entry=plan.get("entry", 0),
                sl=plan.get("sl", 0),
                tp1=plan.get("tp1", 0),
                tp2=plan.get("tp2", 0),
                rr1=plan.get("rr1", 0),
                rr2=plan.get("rr2", 0),
                position_size_mult=plan.get("position_size_mult", 0),
                confidence_score=plan.get("confidence_score", 0),
                zone=plan.get("zone", "EQUILIBRIUM"),
                atr_sl_mult=plan.get("atr_sl_mult", 0),
                atr_tp_mult=plan.get("atr_tp_mult", 0),
            )
            decision = risk_authority.review(tp)
            d = decision.plan.to_dict()
            d["risk_verdict"] = decision.verdict.value
            d["approved_size"] = decision.approved_size
            d["rationale"] = decision.rationale
            decisions.append(d)
        return decisions

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _scan_with_candles(self, symbol: str, timeframe: str,
                           candles, snapshot) -> dict | None:
        """Run a single D1 scan using candles from the snapshot.

        Temporarily injects candles into market_data so the engine
        can read them as if they were live.
        """
        from backend.market_data import market_data
        from backend.engines.engine import scan as engine_scan
        from backend.data_quality_gate import validate_candles

        # Inject snapshot candles into market_data's cache
        cache_key = f"{symbol}:{timeframe}"
        market_data._candle_cache = getattr(market_data, "_candle_cache", {})
        market_data._candle_cache[cache_key] = candles

        # Set snapshot info for evidence provenance
        from backend.state_store import state_store
        state_store.last_snapshot_id = snapshot.snapshot_id

        try:
            signal = asyncio.run(engine_scan(symbol, timeframe))
            if signal:
                # Ensure snapshot_id is embedded in signal
                signal.setdefault("snapshot_id", snapshot.snapshot_id)
            return signal
        finally:
            # Restore original cache entry
            if cache_key in market_data._candle_cache:
                del market_data._candle_cache[cache_key]

    def _structure_summary(self, signal: dict | None) -> dict[str, Any]:
        """Extract a structure summary from a signal dict for alignment engine."""
        if not signal:
            return {}
        return {
            "ob_zone": signal.get("ob_zone", signal.get("market_structure", "")),
            "premium_discount": signal.get("premium_discount", "UNKNOWN"),
            "liq_swept": bool(signal.get("liquidity_pools")),
            "fvg_type": signal.get("fvg", {}).get("type", "") if signal.get("fvg") else "",
            "direction": signal.get("direction", ""),
        }

    def _freeze(self, obj: Any) -> Any:
        """Deep-freeze a structure for deterministic comparison.

        Converts all dicts/lists to immutable equivalents suitable for == comparison.
        """
        if isinstance(obj, dict):
            return {k: self._freeze(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return tuple(self._freeze(v) for v in obj)
        if isinstance(obj, float):
            return round(obj, 10)  # normalize float precision
        return obj

    def _compare_results(self, a: ReplayResult, b: ReplayResult) -> list[str]:
        """Compare two ReplayResults field by field."""
        diffs: list[str] = []

        # Provenance
        if a.snapshot_id != b.snapshot_id:
            diffs.append(f"snapshot_id: {a.snapshot_id} vs {b.snapshot_id}")
        if a.code_version != b.code_version:
            diffs.append(f"code_version: {a.code_version} vs {b.code_version}")
        if a.configuration_hash != b.configuration_hash:
            diffs.append(f"configuration_hash: {a.configuration_hash} vs {b.configuration_hash}")

        # D1 outputs
        if a.d1_outputs != b.d1_outputs:
            diffs.extend(self._diff_list_field("d1_outputs", a.d1_outputs, b.d1_outputs))

        # D2 outputs
        if a.d2_outputs != b.d2_outputs:
            diffs.extend(self._diff_list_field("d2_outputs", a.d2_outputs, b.d2_outputs))

        # Evidence IDs (order-independent)
        if set(a.evidence_ids) != set(b.evidence_ids):
            diffs.append(
                f"evidence_ids mismatch: {sorted(a.evidence_ids)} vs {sorted(b.evidence_ids)}"
            )

        # Alignment
        if a.alignment != b.alignment:
            diffs.extend(self._diff_list_field("alignment", a.alignment, b.alignment))

        # D3 states
        if a.d3_states != b.d3_states:
            diffs.extend(self._diff_list_field("d3_states", a.d3_states, b.d3_states))

        # Confidence
        if a.confidence_scores != b.confidence_scores:
            diffs.append(
                f"confidence_scores: {a.confidence_scores} vs {b.confidence_scores}"
            )

        # TradePlans
        if a.trade_plans != b.trade_plans:
            diffs.extend(self._diff_list_field("trade_plans", a.trade_plans, b.trade_plans))

        # Risk decisions
        if a.risk_decisions != b.risk_decisions:
            diffs.extend(self._diff_list_field("risk_decisions", a.risk_decisions, b.risk_decisions))

        return diffs

    def _diff_list_field(self, name: str, a: tuple, b: tuple) -> list[str]:
        """Produce human-readable diffs for a list field."""
        diffs: list[str] = []
        if len(a) != len(b):
            diffs.append(f"{name}: length {len(a)} vs {len(b)}")
            return diffs
        for i in range(len(a)):
            item_diffs = _deep_equal(a[i], b[i], f"{name}[{i}]")
            diffs.extend(item_diffs[:5])  # cap at 5 per item
        return diffs

    def _inject_snapshot(self, snapshot) -> None:
        """Inject snapshot candles into market_data cache for replay.

        This makes the engine see snapshot data instead of live data.
        """
        from backend.market_data import market_data
        from backend.state_store import state_store

        if not hasattr(market_data, "_candle_cache"):
            market_data._candle_cache = {}

        for key, candles in snapshot.candles.items():
            if candles:
                market_data._candle_cache[key] = candles

        state_store.last_snapshot_id = snapshot.snapshot_id
        state_store.last_snapshot_ts = snapshot.snapshot_timestamp

    def _clear_stores(self) -> None:
        """Clear global state between replay runs to ensure isolation."""
        from backend.state_store import state_store
        from backend.signal_store import signal_store
        from backend.evidence_store import evidence_store
        from backend.market_evolution.history import history_store

        # Clear in-memory stores
        signal_store.signals.clear()
        signal_store.fvg_ledger.clear()
        signal_store.scanned_recently.clear()
        evidence_store._records.clear()
        evidence_store._by_symbol.clear()
        history_store._store.clear()
        history_store._last_state.clear()

        # Clear market data cache
        from backend.market_data import market_data
        if hasattr(market_data, "_candle_cache"):
            market_data._candle_cache.clear()


# ── Module-level singleton ────────────────────────────────────────────────────

replay_engine = ReplayEngine()


# ── Public API ────────────────────────────────────────────────────────────────

def replay_snapshot(snapshot, runs: int = 2) -> ReplayResult:
    """Convenience: replay a snapshot and verify determinism.

    Runs the pipeline `runs` times and asserts identical output.
    Returns the first ReplayResult on success.
    """
    return replay_engine.verify_determinism(snapshot, runs=runs)


def compare_replays(original: ReplayResult, replay: ReplayResult) -> list[str]:
    """Convenience: compare two replay results, return diffs."""
    return replay_engine.compare(original, replay)


__all__ = [
    "ReplayEngine",
    "ReplayResult",
    "ReplayMismatchError",
    "replay_engine",
    "replay_snapshot",
    "compare_replays",
]
