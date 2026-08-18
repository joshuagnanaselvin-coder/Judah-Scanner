# Phase 22 — Test Strategy

## Purpose

Define and implement the complete test architecture for Judah Scanner.
This is the test strategy document, test execution guidelines, and coverage tracking.

## Test Architecture

### Layers

```
┌─────────────────────────────────────────────┐
│             Test Strategy                    │
├─────────────┬───────────────┬───────────────┤
│   UNIT      │  INTEGRATION  │  PROPERTY/    │
│             │               │  STATE-MACHINE│
├─────────────┼───────────────┼───────────────┤
│  Pure calc  │ D1→Evidence   │ Market Evolu- │
│  No I/O     │ D2→Evidence   │ tion state    │
│  No network │ Evidence→Align│ machine       │
│             │ Align→D3      │ EvidenceStore │
│             │ D3→TradePlan  │ invariants    │
│             │ TradePlan→Risk│               │
├─────────────┼───────────────┼───────────────┤
│   REPLAY    │   FAILURE     │    SOAK       │
│             │               │               │
│ Replay ==   │ API timeout   │ Memory leak   │
│ Original    │ Network fail  │ CPU bound     │
│ Determinism │ Stale data    │ Latency dist  │
│             │ Process crash │ Queue growth  │
├─────────────┼───────────────┼───────────────┤
│   CONCUR-   │  DATA QUALITY │               │
│   RENCY     │               │               │
│             │ Stale/missing │               │
│ D1//D2     │ Duplicate     │               │
│ parallel    │ Malformed     │               │
│ Shared-safe │ Out-of-order  │               │
└─────────────┴───────────────┴───────────────┘
```

## Test Categories

### 1. Unit Tests
- Pure calculation functions
- No I/O, no network, no async
- Fast (millisecond scale)
- Target: 100% coverage of pure logic

**Examples:**
- `classify_tier(score)` → correct tier name
- `_check_d2_fatal_flaws()` → correct flaw detection
- `_score_htf_context()` → correct scoring
- `calculate_ev()` → correct EV formula
- `_deep_equal()` → correct diff detection

### 2. Integration Tests
- Multi-component interaction
- Validates data flow between components
- Moderate speed (second scale)

**Examples:**
- D1 output → EvidenceStore append → query
- D2 output → EvidenceStore append → query
- EvidenceStore → AlignmentEngine → evaluate
- Alignment → D3 Market Evolution
- D3 → TradePlan → Risk

### 3. Determinism Tests
- Same input → identical output
- Validates replay engine
- Critical for production trust

**Examples:**
- Snapshot replay produces identical results
- Multiple runs produce same `stage_timings` structure
- `_deep_equal` float tolerance
- `verify_determinism` raises on mismatch

### 4. State-Machine Tests (Phase 23)
- Every valid Market Evolution transition
- Every invalid transition rejection
- Evidence lifecycle (fresh → stale → expired)
- Tier classification boundaries

### 5. Data Quality Tests
- Stale candles rejected
- Missing candles handled
- Duplicate candles filtered
- Malformed OHLC rejected
- Out-of-order candles sorted
- Partial candles handled per policy

### 6. Failure Tests
- API timeout → DEGRADED/FAILED status
- Network failure → no actionable signal
- Stale data → explicit STALE state
- Process restart → clean state
- Slow client → no scanner stall

### 7. Concurrency Tests
- D1/D2 parallel scan safety
- EvidenceStore concurrent writes
- No race conditions
- Bounded memory under concurrent load

### 8. Replay Tests
- Full pipeline replay matches original
- Partial replay (single stage)
- Deterministic hashing

### 9. Soak Tests (manual/Load)
- 24h/72h/7d continuous run
- Memory stable
- CPU bounded
- Latency distribution
- No task leaks
- No queue growth
- No silent failures

## Coverage Targets

| Category       | Target  | Current |
|----------------|---------|---------|
| Pure logic     | 95%     | ~90%    |
| Integration    | 80%     | ~75%    |
| State-machine  | 90%     | ~85%    |
| Data quality   | 85%     | ~80%    |
| Failure        | 70%     | ~60%    |
| Concurrency    | 60%     | ~50%    |
| Replay         | 100%    | 100%    |
| Determinism    | 100%    | 100%    |

## Execution Standards

### Fast Feedback (< 5s)
- All unit tests
- All integration tests
- Run on every commit

### Regression Guard (< 30s)
- Determinism tests
- State-machine tests
- Property tests
- Run on every PR

### Nightly (> 30s)
- Soak tests (shorter variants)
- Full coverage measurement
- Performance baseline

## Naming Convention

```python
tests/
  test_phase<N>_<topic>.py
  test_<component>.py
  conftest.py
  fixtures/
```

- `test_phase<N>` = plan phase number
- `<topic>` = component or behavior
- `conftest.py` = shared fixtures

## Fixture Strategy

- Shared fixtures in `conftest.py`
- Component fixtures in test files
- No global mutable state
- Fresh state per test method
- Factory functions for test data
