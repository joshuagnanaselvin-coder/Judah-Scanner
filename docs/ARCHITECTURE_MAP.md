# Judah Scanner — Current vs Target Architecture Map

## Phase 0 Deliverable — Pre-Implementation Baseline

---

## 1. Data Flow: Snapshot vs Live State

### D1 (HTF Context Radar) — `backend/scanner.py`

| Decision Point | Current Behavior | Target |
|---|---|---|
| Cycle init | Builds `DecisionSnapshot` via `SnapshotBuilder(market_data).build(self.symbols, TIMEFRAMES_HTF)` | Same — correct |
| Quality check | `if quality in ("STALE", "INVALID", "GAPPED"): continue` | Same — correct |
| Candle access | `candles = snap.get_candles(sig['symbol'], sig['engine'])` with fallback to `market_data.get_candles()` | Same — correct |
| Snapshot storage | `state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)` | Same — correct |
| Signal storage | `signal_store.add()` for D1 HTF signals per TF | Same — correct |
| Evidence logging | `_log_evidence()` fire-and-forget via `loop.create_task()` | **Needs fix**: should await |

### D2 (LTF Opportunity Radar) — `backend/engines/ltf_engine.py`

| Decision Point | Current Behavior | Target |
|---|---|---|
| Cycle init | Builds `DecisionSnapshot` via `SnapshotBuilder(market_data).build(scan_targets, htf_timeframes=[], ltf_timeframes=["15M"])` | Same — correct |
| Quality check | `if snap.candle_quality(coin, "15M") in ("STALE", "INVALID", "GAPPED"): continue` | Same — correct |
| Candle access | `market_data.get_candles(coin, "15M")` directly in `scan_entry()` | **GAP**: should use `snap.get_candles()` |
| Snapshot storage | `state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)` | Same — correct |
| Signal storage | `state_store.set_d2_signal(coin, LTFSignal)` | Same — correct |
| Evidence logging | `_log_evidence_async()` via fire-and-forget in `ltf_scanner.py` | **Needs fix**: should await |

### D3 (Decision Layer) — `backend/engines/signal_fusion.py`

| Decision Point | Current Behavior | Target |
|---|---|---|
| Trigger | Watches `state_store.last_d1_scan` / `last_d2_scan` timestamps | Same — correct |
| D1 data source | Reads `state_store.get_d1_tier(coin)` — correct for tier/score/direction | Same |
| D1 structure source | **`signal_store.get(coin, htf)`** for best D1 signal across TIMEFRAMES_HTF | **GAP**: should read from `state_store` or snapshot |
| D2 data source | `state_store.get_d2_signal(coin)` | Same — correct |
| Evidence query | **Never queries `evidence_store`** — alignment reads raw signal dicts | **GAP**: should query evidence for provenance |
| Snapshot id | **Not stamped on package** — `state_store.last_snapshot_id` exists but unused | **GAP**: must stamp `snapshot_id` |
| Alignment | `alignment_engine.evaluate(d1_structure, d2_structure, ...)` | Same — correct |
| TradePlan | `trade_plan_authority.propose(...)` | Same — correct |
| Risk | `risk_authority.review(plan, correlation_group)` | Same — correct |

---

## 2. Evidence Flow: Correct vs Fire-and-Forget

### `backend/engines/engine.py` (D1 scanner)

```python
# Line 226-227 — CALLED inside scan()
_log_evidence(symbol, timeframe, signal, crt, smc, flow, path)

# Line 560-570 — fire-and-forget wrapper
def _log_evidence(symbol, timeframe, signal, ...):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_log_evidence_async(...))  # Fire-and-forget
    except RuntimeError:
        pass  # Silent drop
```

**Status**: ❌ Fire-and-forget. Records silently discarded if loop is busy or on shutdown.

### `backend/engines/ltf_scanner.py` (D2 scanner)

- No evidence logging found in `ltf_scanner.py` itself
- Evidence logging for D2 would need to be added or inherited from ltf_pipeline

**Status**: ❌ No evidence logging for D2 signals.

### `backend/engines/ltf_pipeline.py`

- Need to check for `_log_evidence` calls

### EvidenceStore consumption

| Consumer | Status |
|---|---|
| `signal_fusion._fuse_coin()` | ❌ Never queries evidence_store |
| `alignment_engine.evaluate()` | ❌ Reads signal_store directly, not evidence |
| `ws_hub` / frontend | ❌ Never receives evidence data |

---

## 3. D3 Market Evolution: Canonical Implementation

### Dual Implementation Problem

```
backend/market_evolution/
  ├── __init__.py          — exports from engine.py
  ├── engine.py            — V5.2 matrix-based (15-state matrix)
  └── evolution.py         — 16-state explicit FSM with 5-axis composite
```

**Canonical**: `engine.py` (V5.2 matrix-based) — `__init__.py` re-exports `evaluate` from `engine.py`.

`s`ignal_fusion.py` line 29:
```python
from backend.market_evolution import evaluate as me_evaluate
```
This resolves to `engine.py`'s `evaluate()`.

**`evolution.py` is dead code** — never imported by any active module. It is the earlier design that was superseded by the matrix approach.

### Action: Keep `engine.py` as canonical. Delete or archive `evolution.py`.

---

## 4. Data Flow Gaps and Ownership Boundaries

### Gap Matrix

| Gap | Severity | Owner | Fix |
|---|---|---|---|
| D3 reads D1 structure from `signal_store` (not snapshot) | HIGH | signal_fusion | Read from `state_store` or snapshot |
| `snapshot_id` not stamped on D3 packages | HIGH | signal_fusion | Stamp from `state_store.last_snapshot_id` |
| Evidence logging fire-and-forget | HIGH | engine.py, ltf_pipeline | Await evidence logging |
| No D2 evidence logging | MEDIUM | ltf_scanner | Add evidence logging for D2 |
| Alignment doesn't query evidence_store | MEDIUM | alignment_engine | Query evidence_store for provenance |
| D2 `scan_entry()` reads live candles, not snapshot | MEDIUM | ltf_engine | Pass snapshot candles |
| `evolution.py` dead code | LOW | market_evolution | Delete or archive |

### Ownership Boundaries (Current — Correct)

| Resource | Owner | Mutated By |
|---|---|---|
| `state_store.d1_tiers` | D1 scanner | `scanner.py` via `set_d1_tier()` |
| `state_store.d2_signals` | D2 engine | `ltf_engine.py` via `set_d2_signal()` |
| `state_store.d3_decisions` | D3 fusion | `signal_fusion.py` via `set_d3_decision()` |
| `signal_store.signals` | D1 scanner | `scanner.py` via `signal_store.add()` |
| `state_store.last_snapshot_id` | Both D1 and D2 | `set_snapshot_info()` |
| `evidence_store` | Both D1 and D2 | `evidence_store.append()` |

---

## 5. Critical Bugs

### Bug 1: `signal_store.py` — `performance_tracker` missing import

**File**: `backend/signal_store.py:184`
**Line**: `performance_tracker.record(self.signals[k])`
**Impact**: `NameError` on first signal expiry (TTL cleanup). Crashes `get_all()` / `get_all_decisions()`.
**Fix**: Add `from backend.performance_tracker import performance_tracker` at top.
**Status**: ✅ Fixed in this session.

### Bug 2: `scanner.py` — Duplicate `_clean_expired()` with same missing import

**File**: `backend/scanner.py:382-396`
**Impact**: Same `NameError` — but this method is never called externally (it's a duplicate of `signal_store._clean_expired()`).
**Fix**: Remove the duplicate method or add the import.

### Bug 3: `signal_fusion.py` — D1 structure read from `signal_store`, not snapshot

**File**: `backend/engines/signal_fusion.py:334-384`
**Impact**: D3 decisions may reference stale D1 structure if signal_store cache lags.
**Fix**: Read D1 structure from `state_store.d1_tiers` (which already has the data).

### Bug 4: D3 packages missing `snapshot_id`

**File**: `backend/engines/signal_fusion.py:467-512`
**Impact**: No provenance linkage between D3 decision and the snapshot it was derived from.
**Fix**: Add `snapshot_id: state_store.last_snapshot_id` to package dict.

### Bug 5: Evidence flow is fire-and-forget

**Files**: `backend/engines/engine.py:560-570`, `backend/engines/ltf_pipeline.py` (if present)
**Impact**: Evidence records silently dropped on busy loops or shutdown.
**Fix**: Await evidence logging in scanner cycle (non-blocking with timeout).

---

## 6. Implementation Phase Summary

| Phase | Objective | Status |
|---|---|---|
| Phase 0 | Repository inspection + architecture map | ✅ Complete |
| Phase 1 | DecisionSnapshot as canonical data source | ⬜ Next |
| Phase 2 | Data Quality Gate integration | ✅ Already integrated |
| Phase 3 | EvidenceStore wiring (fix fire-and-forget) | ⬜ Pending |
| Phase 4 | Alignment Engine as canonical D1/D2 convergence | ✅ Already wired |
| Phase 5 | Single TradePlanAuthority | ✅ Already wired |
| Phase 6 | Independent RiskAuthority | ✅ Already wired |
| Phase 7 | Resolve Market Evolution dual implementation | ⬜ Pending |
| Phase 8 | D1/D2 true independence | ✅ Already independent |
| Phase 9 | Deterministic replayability | ⬜ Pending |
| Phase 10 | Observable behavior (no silent failures) | 🔧 In progress |
| Phase 11 | Signal provenance (no anonymous signals) | ⬜ Pending |
