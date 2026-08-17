# Judah Scanner — Top-1% Engineering Implementation Plan

## Purpose

This document is the implementation contract for Claude Code.

The objective is to take the current working Judah Scanner repository and implement the engineering architecture required to make it a highly reliable, deterministic, explainable, replayable, production-grade market intelligence scanner.

This is **not** a request to add more indicators, more dimensions, AI, or unnecessary infrastructure.

The target is:

> **Top-1% engineering quality for Judah's actual problem: real-time multi-timeframe market intelligence across hundreds of crypto symbols.**

Use the existing working repository as the baseline.

---

# 1. NON-NEGOTIABLE IMPLEMENTATION RULES

## 1.1 Preserve the core intelligence architecture

The target architecture is:

```text
                         MARKET DATA
                              |
                              v
                    DATA QUALITY GATE
                              |
                              v
                    DECISION SNAPSHOT
                              |
                 +------------+------------+
                 |                         |
                 v                         v
        D1 CONTEXT RADAR          D2 OPPORTUNITY RADAR
                 |                         |
                 v                         v
           D1 EVIDENCE               D2 EVIDENCE
                 |                         |
                 +------------+------------+
                              |
                              v
                       EVIDENCE STORE
                              |
                              v
                       ALIGNMENT ENGINE
                              |
                              v
                    D3 MARKET EVOLUTION
                         + CONFIDENCE
                              |
                              v
                     TRADE PLAN AUTHORITY
                              |
                              v
                       RISK AUTHORITY
                              |
                              v
                       SIGNAL / OUTPUT
```

### Critical architectural invariant

D1 and D2 must perform their primary scanning **independently and in parallel**.

D1 must not require D2 to produce its primary context result.

D2 must not require D1's opinion to produce its primary opportunity result.

Their first meaningful interaction occurs at the Evidence/Alignment boundary.

---

# 2. IMPLEMENTATION PHILOSOPHY

Prioritize:

1. Correctness
2. Snapshot integrity
3. Determinism
4. Explicit state ownership
5. Evidence provenance
6. Failure safety
7. Replayability
8. Observability
9. Bounded resource usage
10. Performance
11. Maintainability

Do NOT prioritize:

- microsecond optimization
- unnecessary microservices
- Kafka/event-bus complexity
- Kubernetes
- GPU infrastructure
- AI decision making
- additional scanner dimensions
- large-scale ML
- unnecessary databases
- wholesale language rewrites

Do not turn Judah into a distributed systems experiment.

Use the simplest architecture that can reliably satisfy the requirements.

---

# 3. IMPLEMENTATION SEQUENCE

Implement in this order.

Do not randomly modify multiple architectural layers at once.

## Phase 0 — Baseline and architecture map

Before modifying code:

- inspect the complete repository
- identify current D1 implementation
- identify current D2 implementation
- identify current fusion/alignment implementation
- identify Market Evolution
- identify StateStore
- identify SignalStore
- identify TradePlan/Risk logic
- identify candle cache
- identify market-data ingestion
- identify WebSocket/output paths
- identify existing tests
- identify deployment/runtime configuration

Produce an internal dependency map.

Do not rewrite working logic unnecessarily.

---

# 4. PHASE 1 — DECISION SNAPSHOT

## Objective

Every scanner decision must be based on one coherent market snapshot.

Create a conceptual `DecisionSnapshot`.

Minimum fields:

```text
snapshot_id
snapshot_timestamp
processing_timestamp
symbol
market_data_version
configuration_hash
code_version
candles
data_quality
liquidity_state
```

Where practical, include timeframe-specific candle generations/timestamps.

## Rules

- Snapshot is immutable after creation.
- D1 reads the snapshot.
- D2 reads the same snapshot.
- Alignment reads evidence generated from the same snapshot.
- D3 reads the same decision lineage.
- TradePlan reads the validated decision lineage.
- No downstream component should silently re-read mutable live candle state for the same decision.

## Acceptance criteria

For a given:

```text
snapshot_id
code_version
configuration_hash
```

the same input must produce the same decision.

---

# 5. PHASE 2 — DATA QUALITY GATE

Create/strengthen a centralized data-quality gate before intelligence processing.

Validate:

- timestamp validity
- candle ordering
- duplicate candles
- missing candles
- OHLC validity
- high >= low
- open/close inside valid range where applicable
- volume validity
- stale data
- incomplete/partial candle policy
- timeframe consistency
- symbol consistency
- sufficient history
- liquidity availability

Every data state must be explicit.

Allowed examples:

```text
VALID
STALE
MISSING
GAPPED
INVALID
INCOMPLETE
DEGRADED
```

Invalid/stale data must not silently continue as valid data.

## Acceptance criteria

A bad market-data condition must produce an explicit state and predictable behavior.

No silent fallback to stale market data.

---

# 6. PHASE 3 — INDEPENDENT D1 AND D2

## D1 — Context Radar

D1 answers:

> Where is the market and what is its context?

D1 should produce structured evidence such as:

- HTF structure
- directional context
- regime
- liquidity environment
- volatility
- premium/discount/location where applicable
- structural bias
- contextual evidence
- evidence strength
- freshness
- D1 confidence

D1 must NOT own:

- final Entry
- final SL
- final TP
- position size
- final trade decision

## D2 — Opportunity Radar

D2 answers:

> Is there an actionable opportunity developing?

D2 may use:

- displacement
- momentum
- liquidity sweep
- FVG
- OB
- flow
- trigger quality
- opportunity structure
- entry location
- opportunity strength
- freshness
- D2 confidence

D2 must NOT require D1 approval to perform its primary scan.

## Parallel execution

For each valid snapshot:

```text
snapshot
  |
  +----> D1
  |
  +----> D2
```

Run independently where the existing runtime architecture allows.

Do not introduce uncontrolled shared mutable state.

D1 and D2 should return explicit result objects.

---

# 7. PHASE 4 — EVIDENCE CONTRACT

Make evidence a first-class contract.

Suggested structure:

```text
EvidenceRecord
    evidence_id
    snapshot_id
    symbol
    dimension
    source
    observation
    value
    strength
    confidence
    timestamp
    freshness
    status
    reason
```

Example sources:

```text
CRT
SMC
FLOW
MOMENTUM
LIQUIDITY
STRUCTURE
VOLATILITY
```

Evidence should be distinguishable as:

```text
FULL
PARTIAL
DEGRADED
FAILED
STALE
```

Do not allow a degraded result to look identical to a fully supported result.

---

# 8. PHASE 5 — EVIDENCE STORE

The Evidence Store is the first convergence point.

It must preserve:

- snapshot identity
- symbol
- dimension
- evidence source
- timestamp
- freshness
- status
- strength
- confidence
- reason

It must handle:

- duplicate evidence
- stale evidence
- conflicting evidence
- missing evidence
- partial evidence
- repeated scans
- expiration

Evidence from one snapshot must not silently contaminate another snapshot.

---

# 9. PHASE 6 — ALIGNMENT ENGINE

Alignment is the first true interaction between D1 and D2.

Do NOT implement:

```text
D3 = D1_score + D2_score
```

Instead evaluate:

```text
D1 evidence
+
D2 evidence
+
freshness
+
data quality
+
evidence completeness
+
agreement
+
conflict
+
missing evidence
```

Alignment should explicitly classify:

```text
STRONG_ALIGNMENT
PARTIAL_ALIGNMENT
CONFLICT
INSUFFICIENT_EVIDENCE
DEGRADED
```

Example:

```text
D1 = Bullish
D2 = Bullish
Evidence = Fresh
Quality = Valid
Conflict = None

=> STRONG_ALIGNMENT
```

versus:

```text
D1 = Bullish
D2 = Bearish

=> CONFLICT
```

No implicit assumptions.

---

# 10. PHASE 7 — MARKET EVOLUTION AS A REAL STATE MACHINE

Market Evolution is D3.

Do not create D4 for MVP.

D3 owns:

- market evolution state
- state transition
- transition confidence
- evolution evidence

The existing 16 market-evolution states should be preserved initially unless the implementation proves that a state is invalid, redundant, or impossible.

For every state define:

```text
state
entry conditions
required evidence
allowed previous states
allowed next states
exit conditions
invalidation
confidence
```

Build an explicit transition table.

Example:

```text
CONSOLIDATION
    |
    | displacement + liquidity event
    v
AWAKENING
    |
    | sustained expansion
    v
EXPANSION
```

And invalid transitions must be rejected.

## Acceptance criteria

No impossible state transition.

No silent state mutation.

Every transition must be explainable.

---

# 11. PHASE 8 — CONFIDENCE

Confidence remains part of D3.

Do NOT create an independent D4 scanner.

Confidence should represent the quality of the Market Evolution conclusion.

Conceptually:

```text
D1 strength
+
D2 strength
+
alignment
+
evidence quality
+
evidence freshness
+
market-data quality
+
evolution stability
=
D3 confidence
```

Do not imply that:

```text
confidence = probability
```

unless it has been empirically calibrated.

Until calibration exists, call it:

```text
confidence_score
```

not:

```text
win_probability
```

---

# 12. PHASE 9 — SINGLE TRADE PLAN AUTHORITY

There must be exactly one authority responsible for the final trade plan.

Create/standardize:

```text
TradePlan
    entry
    stop_loss
    take_profit
    rr
    invalidation
    risk
    position_size
    reason
```

Upstream engines provide evidence and market intelligence.

They do not independently override TradePlan values.

## Required invariant

No other module should independently become the final authority for:

- Entry
- SL
- TP
- Risk
- Position Size

---

# 13. PHASE 10 — RISK AUTHORITY

Separate market intelligence from capital risk.

Flow:

```text
D3
 |
 v
TradePlan
 |
 v
Risk Authority
 |
 +-- exposure constraints
 +-- risk limits
 +-- position sizing
 +-- system health
 +-- data quality
 |
 v
Approved / Rejected
```

A high-confidence market decision must still be rejected if system/data/risk conditions are unsafe.

---

# 14. PHASE 11 — NO SILENT FAILURES

Audit and remove dangerous patterns such as:

```python
except Exception:
    pass
```

or equivalent silent failure paths.

Every failure should become an explicit state:

```text
SUCCESS
DEGRADED
REJECTED
FAILED
STALE
INCOMPLETE
```

Failures must be observable.

A fallback must preserve its degraded status.

Example:

```text
CRT = FAILED
SMC = PASS
FLOW = PASS

D2 status = DEGRADED
```

It must not appear identical to:

```text
CRT = PASS
SMC = PASS
FLOW = PASS

D2 status = FULL
```

---

# 15. PHASE 12 — SIGNAL PROVENANCE

Every final signal should be reconstructable.

Attach:

```text
signal_id
snapshot_id
symbol
code_version
configuration_hash
data_version
d1_evidence_ids
d2_evidence_ids
alignment_id
market_evolution_id
trade_plan_id
risk_decision_id
timestamps
```

The objective:

> Given one signal ID, an engineer must be able to reconstruct why Judah produced it.

---

# 16. PHASE 13 — REPLAYABILITY

Implement a minimal deterministic replay mechanism.

Input:

```text
DecisionSnapshot
Code Version
Configuration Version
```

Output:

```text
D1
D2
Evidence
Alignment
D3
Market Evolution
Confidence
TradePlan
Risk
```

Compare replayed output with the original.

## Acceptance criterion

Identical snapshot + identical code/configuration must produce identical output.

If not, emit a `REPLAY_MISMATCH` diagnostic.

Do not hide mismatches.

---

# 17. PHASE 14 — DETERMINISM

Remove or isolate nondeterminism caused by:

- unordered iteration
- mutable shared state
- timing-dependent reads
- async ordering
- uncontrolled retries
- fallback timing
- database timing
- network timing
- global mutable objects
- inconsistent timestamps

Where ordering matters, make it explicit.

Where timestamps matter, derive them from the decision snapshot.

Do not use `time.time()` deep inside business logic as an implicit source of truth for the same decision.

---

# 18. PHASE 15 — STATE OWNERSHIP

For every major state object answer:

```text
Who owns it?
Who writes it?
Who reads it?
When is it valid?
When does it expire?
What happens after restart?
```

Avoid multiple authorities.

Avoid uncontrolled global mutable state.

Every cache/store must have:

```text
owner
maximum size
TTL/expiry policy
cleanup policy
```

---

# 19. PHASE 16 — MEMORY SAFETY

Audit:

- dictionaries
- caches
- signal history
- scan tracking
- queues
- websocket client lists
- background tasks
- historical state

Nothing should grow indefinitely.

For every long-lived collection implement or verify:

```text
MAX SIZE
TTL
EVICTION
CLEANUP
```

Do not add persistent storage merely to avoid bounded-memory design.

---

# 20. PHASE 17 — WEBSOCKET SAFETY

WebSocket output must not block the scanner's critical path.

Use bounded delivery.

Do not create uncontrolled fire-and-forget tasks.

Protect against:

- slow clients
- disconnected clients
- repeated reconnects
- queue growth
- failed sends

A slow frontend must never stall market intelligence computation.

---

# 21. PHASE 18 — PERFORMANCE ENGINEERING

Do not optimize blindly.

First instrument the critical path.

Measure:

```text
market_data
snapshot
d1
d2
evidence
alignment
d3
trade_plan
risk
broadcast
total_cycle
```

For each where practical:

```text
P50
P95
P99
P99.9
MAX
```

Identify:

- duplicate lookups
- O(n²) operations
- repeated calculations
- unnecessary allocations
- blocking I/O
- repeated state access
- serialization overhead
- event-loop blocking
- slow clients

Do not chase microseconds.

Optimize only measured bottlenecks.

---

# 22. PHASE 19 — FAILURE SAFETY

For each external dependency define behavior for:

- timeout
- stale data
- malformed data
- missing data
- network failure
- database failure
- process failure

The default for uncertain market intelligence should be:

```text
DO NOT GENERATE NEW ACTIONABLE SIGNAL
```

until valid data is restored.

Do not continue trading intelligence from stale state without an explicit policy.

---

# 23. PHASE 20 — RESTART / RECOVERY

Verify:

- startup state
- cache initialization
- stale-state cleanup
- signal recovery
- duplicate prevention
- watchdog recovery
- database reconnection
- market-data reconnection
- WebSocket recovery

A restart must not create:

- duplicate signals
- stale signals
- corrupted state
- phantom positions
- invalid Market Evolution transitions

---

# 24. PHASE 21 — OBSERVABILITY

Build enough telemetry to answer:

> "Why did Judah generate this signal?"

At minimum track:

```text
cycle_id
snapshot_id
symbol
stage
start_time
end_time
duration
status
failure_reason
data_freshness
d1_status
d2_status
alignment_status
d3_state
confidence
trade_plan_status
risk_status
```

Use structured logs.

Do not log secrets.

---

# 25. PHASE 22 — TEST STRATEGY

Do not only chase test count.

Test architecture.

## Unit

Pure calculations.

## Integration

D1 → Evidence.

D2 → Evidence.

Evidence → Alignment.

Alignment → D3.

D3 → TradePlan.

TradePlan → Risk.

## Determinism

Same snapshot → same result.

## State-machine

Every valid transition.

Every invalid transition.

## Data quality

- stale
- missing
- duplicate
- out-of-order
- malformed
- partial

## Failure

- API timeout
- network failure
- database failure
- process restart
- slow client

## Concurrency

D1/D2 parallel execution.

Shared-state safety.

## Replay

Original result == replay result.

## Soak

24h → 72h → 7 days.

Track memory, CPU, latency and errors.

---

# 26. PHASE 23 — PROPERTY / STATE-MACHINE TESTING

Where practical, add property-based or state-machine tests.

Guarantees should include:

- impossible states cannot occur
- invalid transitions are rejected
- stale evidence cannot become fresh
- missing evidence cannot become positive evidence
- invalid market data cannot generate actionable output
- replay produces identical output
- duplicate events do not corrupt state

---

# 27. PHASE 24 — PRODUCTION READINESS

Before declaring production-ready, verify:

### Data

- valid
- fresh
- coherent
- traceable

### D1

- independent
- deterministic
- explainable

### D2

- independent
- deterministic
- explainable

### Evidence

- immutable lineage
- freshness
- provenance

### Alignment

- explicit agreement/conflict

### D3

- formal state machine
- deterministic
- explainable

### TradePlan

- single authority

### Risk

- independent authority
- system-health gates

### Runtime

- bounded memory
- controlled concurrency
- failure recovery
- graceful shutdown

### Observability

- full signal reconstruction

### Replay

- deterministic

### Testing

- unit
- integration
- state
- replay
- failure
- concurrency
- soak

---

# 28. DO NOT BUILD

Explicitly reject the following for this implementation unless a concrete requirement proves they are necessary:

- D4 scanner
- D5 scanner
- LLM deciding trades
- AI-generated trade decisions
- knowledge graph
- giant event bus
- Kafka
- Kubernetes
- microservice explosion
- GPU infrastructure
- FPGA infrastructure
- nanosecond optimization
- wholesale rewrite into another language
- dozens of additional indicators
- dozens of confidence dimensions
- self-modifying strategies
- automatic threshold mutation
- unnecessary databases
- unnecessary distributed state

Top-1% does NOT mean maximum complexity.

Top-1% means maximum reliability and decision quality for the required problem.

---

# 29. 20% THAT CREATES 80% OF VALUE

Prioritize these five things above everything else:

## 1. Immutable Decision Snapshot

## 2. Truly independent D1 + D2

## 3. Evidence → Alignment → D3

## 4. Deterministic Market Evolution + Confidence

## 5. Replayable TradePlan with full provenance

If these five are excellent, Judah has a strong foundation.

---

# 30. IMPLEMENTATION GATES

Do not move to the next phase until the current gate passes.

## Gate 1 — Snapshot

```text
Same snapshot
→ D1/D2 see same market state
```

PASS required.

## Gate 2 — Independence

```text
D1 can scan without D2.
D2 can scan without D1.
```

PASS required.

## Gate 3 — Convergence

```text
D1 evidence
+
D2 evidence
→ Alignment
```

PASS required.

## Gate 4 — D3

```text
Alignment
→ deterministic Market Evolution
```

PASS required.

## Gate 5 — TradePlan

Exactly one authority.

PASS required.

## Gate 6 — Replay

```text
Original == Replay
```

100/100 test cases should match.

## Gate 7 — Failure Safety

No stale/invalid data produces actionable output.

PASS required.

## Gate 8 — Soak

Run continuously and verify:

- memory stable
- latency stable
- no task leaks
- no state corruption
- no uncontrolled queue growth
- no silent failures

PASS required.

---

# 31. CODING DISCIPLINE

Before changing any file:

1. Understand current behavior.
2. Identify callers.
3. Identify tests.
4. Identify state ownership.
5. Identify downstream contracts.
6. Make the smallest safe change.
7. Run relevant tests.
8. Run full tests.
9. Verify architecture remains intact.
10. Document the change.

Do not perform broad refactors unless required.

Do not combine unrelated fixes.

Do not change behavior merely for stylistic reasons.

---

# 32. REQUIRED FINAL REPORT FROM CLAUDE CODE

After implementation, report:

## Architecture

What changed and why.

## D1

How independence was achieved.

## D2

How independence was achieved.

## Snapshot

How snapshot integrity is guaranteed.

## Evidence

How provenance and freshness work.

## Alignment

How convergence works.

## D3

How Market Evolution is represented and validated.

## Confidence

How confidence is calculated.

## TradePlan

Where final authority lives.

## Risk

Where risk authority lives.

## Replay

How replay works.

## Determinism

How deterministic behavior is enforced.

## Performance

P50/P95/P99 where measurable.

## Failure Safety

How failures are handled.

## Observability

What can now be reconstructed.

## Tests

Before vs after test counts and categories.

## Remaining Risks

Anything still unproven.

## Files Changed

List every modified file.

## Migration / Deployment

Exact steps required to deploy safely.

---

# 33. FINAL SUCCESS DEFINITION

Judah is considered successful when it can reliably perform:

```text
MARKET DATA
    ↓
VALID SNAPSHOT
    ↓
   +---------+
   |         |
   D1       D2
   |         |
   +---------+
       ↓
    EVIDENCE
       ↓
   ALIGNMENT
       ↓
 MARKET EVOLUTION
       ↓
  CONFIDENCE
       ↓
   TRADE PLAN
       ↓
      RISK
       ↓
    OUTPUT
```

with:

```text
same input
     =
same decision
```

and:

```text
every decision
     =
traceable
     +
explainable
     +
replayable
     +
data-valid
```

and:

```text
failure
     =
safe degradation
     or
safe stop
```

not silent continuation.

---

# 34. FINAL INSTRUCTION TO CLAUDE CODE

Implement this fully against the current Judah Scanner repository.

Do not redesign Judah into a different product.

Do not add unnecessary dimensions.

Do not turn this into a generic enterprise platform.

Do not optimize for impressive architecture diagrams.

Optimize for:

**correct market intelligence + deterministic decisions + reliable runtime +
complete provenance + safe failure + replayability + measurable performance.**

The target is not "more features."

The target is:

> **A scanner that can be trusted.**

Before declaring completion, run the complete test suite and perform an architecture-level verification against every invariant in this document.

If an invariant cannot be proven, explicitly report it as UNPROVEN instead of claiming success.
