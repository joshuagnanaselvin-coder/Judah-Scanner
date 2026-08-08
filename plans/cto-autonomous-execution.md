# Judah Scanner — Autonomous CTO Execution

## Master Prompt for Claude Code

---

# ═══════════════════════════════════════════════════════════════════
# EXECUTION MODE: AUTONOMOUS WITH AUDIT CHECKPOINTS
# ═══════════════════════════════════════════════════════════════════

You are the **Chief Technical Officer** of the Judah Scanner project. You have full authority to implement changes. Your job is to execute the complete CTO plan, phase by phase, with an audit checkpoint at the end of each phase.

**YOU MUST NOT skip phases. YOU MUST NOT combine phases. Execute each phase fully, then STOP and present your audit summary.**

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 0: CONTEXT & ARCHITECTURE (READ ONLY — DO NOT MODIFY CODE)
# ═══════════════════════════════════════════════════════════════════

## What Judah Scanner Is

Judah Scanner is a **Market Intelligence Operating System** for Binance futures. It uses Candle Range Theory (CRT) + Smart Money Concepts (SMC) to scan ~529 USDT pairs and surface high-probability trade setups.

**Current architecture (BROKEN for fast movers):**
- D1 — HTF Scanner: scans 1H/4H/1D across all 529 pairs every 15 seconds. Approves ~20-30 coins as SNIPER/OPPORTUNITY/WATCH.
- D2 — LTF Scanner: scans 15M but ONLY for coins D1 already approved.
- D3 — Fusion Engine: reads D1 tiers + D2 scores → maps into a 3x3 grid → 9 buckets (READY, BUILDING, WAIT, EARLY, DEVELOPING, MONITOR, TRAP, IGNORE). Also passes through the 16-state Market Evolution Matrix (Dormant, Awakening, Compression, Expansion, Institutional Entry, etc.). Broadcasts to frontend.

## What To Keep, What To Kill

- **KEEP**: 16-state Market Evolution Matrix (market_evolution/) — interpretive framework, stays.
- **KILL**: 3x3 D3 Fusion bucket system (READY/BUILDING/WAIT/EARLY/DEVELOPING/MONITOR/TRAP/IGNORE) — replaced by Decision Layer with Signal Types.
- **KILL**: D1→D2 gating — D2 must scan ALL 529 pairs in parallel.

## Your Knowledge Domains

1. Python engineering — async patterns, concurrency, clean architecture, testing, error handling, performance.
2. Candle Range Theory (CRT) — range identification, displacement candles, retracement zones, OTE, range breaks.
3. Smart Money Concepts (SMC) — order blocks, FVGs, MSB, CHoCH, SMS, liquidity pools, killzones.
4. Flow Analysis — volume profile, delta, CVD, order book imbalance, absorption, effort vs. result, VPOC.
5. Momentum/Impulse — relative strength, acceleration, divergence, impulse vs. corrective structure.
6. Institutional Trading Psychology — how hedge funds, prop firms, and market makers actually enter positions, manage risk, and engineer liquidity.

## Operating Principles

- No layer may bypass another. No module may own multiple responsibilities.
- Every scoring weight must be defensible.
- Every fix must include: what the bug is, why it matters, the fix, how to verify it.

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 1: SCORING FRAMEWORK — IMPLEMENT THE 7 CORRECTIONS
# ═══════════════════════════════════════════════════════════════════

**OBJECTIVE**: Apply all 7 corrections to the existing scoring framework, then update ALL code files that reference the old values.

**DO NOT move to Phase 2 until all 7 fixes are applied and verified.**

## Fix 1 — Type B minimum score raised to 72 + Entry Precision gate

**Change**:
- Type B minimum D2 score: 65 → **72**
- Add minimum sub-score: Entry Precision must be >= **18/25** for Type B classification
- If Entry Precision < 18, the signal cannot be Type B regardless of total score
- Update the Signal Type classification logic in the code
- Update the Decision Matrix

**Files to change**: scoring engine, signal classifier, config constants

## Fix 2 — D2 minimum thresholds raised

**Change**:
- Entry Precision minimum: 12 → **15**
- Flow minimum: 5 → **8**
- Momentum minimum: 0 → **8**
- New D2 minimum total: 26 (was 12)

**Files to change**: D2 scoring engine, config constants

## Fix 3 — Decay rates differentiated per signal type

**Change**:
- Type A (2h TTL): decay = **0.94x per 5 min** (was 0.95x)
- Type C (4h TTL): decay = **0.98x per 5 min** (unchanged)
- This makes Type C genuinely longer-lived than Type A

**Files to change**: decay engine, config constants

## Fix 4 — Acceleration state enforcement

**Pick one approach and implement fully:**

- **Option A**: Acceleration = **0x** for new entries. Add frontend flag "ACCELERATION — TAKE PROFITS ONLY."
- **Option B**: Keep 0.75x but change rule to "reduced entries only, move stop to breakeven immediately."

**Your decision**: Based on institutional flow, choose Option A or B. Then implement: update the State multiplier table, add enforcement in the Decision Layer, update frontend display.

**Files to change**: position sizing engine, decision layer, state multiplier table, frontend

## Fix 5 — Remove Appendix A EV table, replace with formula

**Change**:
- Remove the fixed EV numbers table from documentation
- Replace with the formula: EV = (Win_Rate × Avg_Win) - (Loss_Rate × Avg_Loss)
- Add a per-signal EV calculator in the scoring engine that computes EV at classification time

**Files to change**: documentation, scoring engine (add EV calculator)

## Fix 6 — Type C threshold raised to SNIPER on both sides

**Change**:
- Type C (Full Confluence) requires: D1 >= **85** AND D2 >= **85** (both SNIPER tier)
- Previously was D1 >= 70, D2 >= 70 (which allowed OPPORTUNITY-tier scores)
- Type A stays at D1 >= 70, D2 >= 50
- Type B stays at D2 >= 72

**Files to change**: signal classifier, decision matrix, config constants

## Fix 7 — IGNORE threshold split: nascent moves get lower bar

**Change**:
- Signals WITHOUT nascent_move AND score < 60 → **IGNORE** (was < 50)
- Signals WITH nascent_move: minimum 60 (not 50)
- The Nascent Move Detector's 5-condition check is the real filter, not the raw score

**Files to change**: decision matrix, signal classifier

## Phase 1 Completion Criteria

- All 7 fixes applied in code
- All config constants updated
- Decision Matrix updated
- Documentation updated
- No references to old values remain in active code

---

# ═══════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ⚠️  AUDIT CHECKPOINT 1: PHASE 1 COMPLETE — STOP HERE  ⚠️  ║
# ║  Present your audit summary. Wait for approval.            ║
# ╚══════════════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════

**Present**:
1. Summary of all 7 fixes applied (what changed, old value → new value)
2. List of files modified
3. Any decisions you made (e.g., Fix 4 Option A vs B and why)
4. Verification: show that old values no longer exist in the codebase

**DO NOT proceed to Phase 2 until approved.**

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 2: CODE REMEDIATION (All 12 Issues + Architecture Changes)
# ═══════════════════════════════════════════════════════════════════

**OBJECTIVE**: Fix all 12 bugs identified in the code review + architecture changes for parallel scanning + bucket removal.

## 2.1 The 12 Bug Fixes

For each bug, fix it completely (code + tests + documentation). Track your progress.

### Bug 1: Score ceiling inconsistencies
- **Problem**: config.py says 90 max, engine.py caps at 85/105/120, signal_builder.py says 105, ltf_pipeline.py says 120.
- **Fix**: Establish single source of truth in config.py. D1 max = 100, D2 max = 100. Remove all intermediate caps that conflict. Enforce ceiling only at the final aggregation step.
- **Files**: config.py, engine.py, signal_builder.py, ltf_pipeline.py
- **Verify**: All scoring paths produce max 100. No file documents a different ceiling.

### Bug 2: Duplicated code — _synth_crt_score and _build_smc_only_context
- **Problem**: engine.py and ltf_pipeline.py both have near-identical copies. Bug fixes need to be applied twice.
- **Fix**: Extract both functions into a shared module (e.g., `scoring_utils.py` or `crt_engine.py`). Both engine.py and ltf_pipeline.py import from the shared module. Remove the duplicates.
- **Files**: engine.py, ltf_pipeline.py, new scoring_utils.py
- **Verify**: Both callers produce identical output. No duplicate function definitions remain.

### Bug 3: Duplicate health endpoint in main.py
- **Problem**: Two @app.get("/api/health") definitions. Second shadows first. First references signal_store.signals (not a method). Dead code.
- **Fix**: Remove the first (earlier) definition. Keep the second one (it uses signal_store.get_all() which is correct). Verify the endpoint works.
- **Files**: main.py
- **Verify**: Only one /api/health endpoint exists. Returns correct response.

### Bug 4: Blocking scan() in async context
- **Problem**: scanner.py uses asyncio.Semaphore to limit concurrency, but scan() is synchronous and blocking. The semaphore bounds parallelism but doesn't make it async. WS read loop gets starved.
- **Fix**: Convert scan() to run in an executor (asyncio.to_thread or run_in_executor). The semaphore can stay as a concurrency limiter on top of the async execution. Alternatively, move the scan loop to a separate process or thread pool.
- **Files**: scanner.py, possibly engine.py
- **Verify**: WS read loop latency stays under 100ms under full load (529 pairs). Scan throughput is maintained or improved.

### Bug 5: MarketData candle mutation without lock protection
- **Problem**: _handle_kline modifies self.candles dict (existing.append, del existing[0]) from WS read loop. get_candles reads from multiple scan coroutines. No lock protection. del existing[0] is O(n) and could race with reads.
- **Fix**: Add asyncio.Lock protection around all self.candles mutations. Replace del existing[0] with a more efficient eviction strategy (e.g., collections.deque with maxlen, or periodic compaction). Ensure get_candles acquires the lock for reads.
- **Files**: market_data.py
- **Verify**: No race conditions under stress test (simulate high WS message velocity). No data corruption.

### Bug 6: D2 TTL (15min) vs D3 TTL (30min) mismatch
- **Problem**: D2 signals expire after 15 minutes (config). But D3 reads D2 signals with a 30-minute window (state_store.py:138 has if age < 1800). Expired D2 signals still show in D3.
- **Fix**: Align D3's D2 signal age check to 15 minutes (900 seconds). OR extend D2 TTL to 30 minutes if the business logic supports it. Pick one and document the rationale.
- **Files**: config.py, state_store.py, ltf_engine.py
- **Verify**: Expired D2 signals are correctly removed from D3 display.

### Bug 7: pre_filter.py unused (dead code)
- **Problem**: pre_filter.py exists and implements a pre-filter, but nobody calls it. candidate_selector.py is used instead.
- **Fix**: Delete pre_filter.py. Verify no imports reference it anywhere in the codebase.
- **Files**: Delete pre_filter.py
- **Verify**: No import errors. candidate_selector.py is the only pre-filter path.

### Bug 8: Session scoring function has unused parameters
- **Problem**: session.py:87-97 — session_score() accepts signal_direction, timestamp_utc, displacement_ratio, liquidity_swept, liquidity_direction but only uses signal_direction (and actually ignores even that — returns static score from session name).
- **Fix**: Either implement the full logic using the parameters, or remove the unused parameters and add a TODO comment if the enhanced logic is planned. Don't leave dead parameters.
- **Files**: session.py
- **Verify**: Function signature matches usage. No unused parameter warnings.

### Bug 9: is_in_ote and is_in_optimal_ote are identical
- **Problem**: Both functions at candle_math.py:113-117 do `return 50 <= pct <= 62`. Two names for the same thing.
- **Fix**: Consolidate into one function. Pick the clearer name (is_in_ote). Replace all calls to is_in_optimal_ote with is_in_ote. Remove is_in_optimal_ote.
- **Files**: candle_math.py, all files that call is_in_optimal_ote
- **Verify**: No references to is_in_optimal_ote remain. All callers use is_in_ote.

### Bug 10: CRT score cap discrepancy
- **Problem**: config.py says CRT_SCORE_DISPLACEMENT = 15, CRT_SCORE_RETRACEMENT = 15, CRT_SCORE_SESSION = 10, CRT_SCORE_RANGE_BREAK = 10 (totaling 50). But crt_engine.py:49 has _CRT_MAX_SCORE = 30 and actual weights are: consolidation 8, range candle 8, displacement 3, retest 4, zone 2 = 25 max. Neither config values nor docstring match the implementation.
- **Fix**: Choose ONE set of weights and apply consistently. Recommendation: Use the crt_engine.py implementation weights (consolidation 8, range candle 8, displacement 3, retest 4, zone 2 = 25 max, capped at 25). Update config.py to match. Update all docstrings. Ensure the CRT scoring in both D1 and D2 uses the same weights.
- **Files**: config.py, crt_engine.py, engine.py, ltf_pipeline.py
- **Verify**: All CRT scoring paths produce consistent results. Config values match implementation.

### Bug 11: StateStore double-singleton pattern
- **Problem**: state_store.py uses both __new__ singleton AND creates state_store = StateStore() at module level. Redundant and fragile.
- **Fix**: Keep the __new__ singleton pattern. Remove the module-level instantiation (state_store = StateStore()). Update all import sites that reference the module-level instance to use StateStore() constructor instead (the __new__ guard ensures it's still a singleton).
- **Files**: state_store.py, all files that import state_store
- **Verify**: Only one StateStore instance exists. No import errors.

### Bug 12: WS send_json error handling gaps
- **Problem**: ws_hub.py:24-30 — if ws.send_json() raises an unexpected exception, it could crash the broadcast coroutine. No per-client error logging.
- **Fix**: Add specific exception handling per client in the broadcast loop. Log which client failed and why. Ensure the broadcast continues for remaining clients even if one fails. Add a try/except around the entire send loop that catches all exceptions and logs them.
- **Files**: ws_hub.py
- **Verify**: Simulate a broken client connection. Broadcast continues for other clients. Error is logged.

## 2.2 Architecture Changes

### D3 Fusion Bucket System Removal

**What to DELETE**:
1. The 3x3 bucket classification logic in signal_fusion.py (the grid that maps to READY/BUILDING/WAIT/EARLY/DEVELOPING/MONITOR/TRAP/IGNORE)
2. StateStore.d3_fusion (the bucket state dictionary) — replaced by StateStore.d3_decisions
3. Any frontend code that renders the 9 buckets
4. Any API endpoints that serve bucket data

**What REPLACES it**:
1. Decision Layer (signal classifier) that outputs Signal Types A/B/C/D/E
2. StateStore.d3_decisions that stores signal type + market evolution state + position size + action
3. Frontend that renders Signal Type + Market Evolution State instead of buckets

**Files to modify**: signal_fusion.py (delete bucket logic, keep/rewrite as Decision Layer), state_store.py, frontend JS, any API routes

### D1-D2 Gating Code Removal

**What to change**:
1. ltf_engine.py / ltf_pipeline.py: Remove the code that reads StateStore for D1-approved pairs before scanning. D2 should scan ALL 529 pairs every cycle.
2. scanner.py: Verify D1 scans all pairs independently (this is already correct, but verify).
3. StateStore: Remove any "approved_pairs" or "d1_tier_filter" mechanism that gates D2.

**Files to modify**: ltf_engine.py, ltf_pipeline.py, state_store.py

## Phase 2 Completion Criteria

- All 12 bugs fixed and verified
- D3 Fusion bucket system fully removed (not just disabled)
- D1-D2 gating fully removed
- D2 scans all 529 pairs independently
- StateStore.d3_decisions replaces StateStore.d3_fusion
- No regressions: existing functionality preserved

---

# ═══════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ⚠️  AUDIT CHECKPOINT 2: PHASE 2 COMPLETE — STOP HERE  ⚠️  ║
# ║  Present your audit summary. Wait for approval.            ║
# ╚══════════════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════

**Present**:
1. Summary of all 12 bug fixes (old → new for each)
2. Summary of architecture changes (what was deleted, what replaced it)
3. List of all files modified, created, or deleted
4. Verification results for each fix
5. Confirmation that D2 now scans all 529 pairs independently
6. Confirmation that the bucket system is fully removed (not just disabled)

**DO NOT proceed to Phase 3 until approved.**

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 3: SCORING REDESIGN + PARALLEL ARCHITECTURE + DECISION LAYER
# ═══════════════════════════════════════════════════════════════════

**OBJECTIVE**: Implement the complete new scoring engine, parallel D1/D2 architecture, Nascent Move Detector, and Decision Layer with Signal Types A/B/C/D/E.

## 3.1 D1 Scoring Engine — 100 Points (HTF: 1H/4H/1D)

Implement the complete D1 scoring pipeline. Each sub-scorer is a separate function/class.

| Category | Max | Min | Implementation |
|---|---|---|---|
| CRT Range Quality | 20 | 8 | displacement(10) + retracement(5) + boundaries(5) |
| SMC Confluence | 25 | 10 | OB(8) + FVG(7) + MSB(6) + CHoCH(4) |
| Flow Confirmation | 15 | 5 | volume(5) + delta(5) + effort/result(5) |
| Momentum | 15 | 6 | impulse(5) + relative strength(5) + divergence(5) |
| Institutional Timing | 10 | 0 | killzone(4) + session(3) + days(3) |
| Risk/Reward Quality | 10 | 0 | RR ratio(6) + structural stop(4) |
| Confluence Bonus | 5 | 0 | 1pt per satisfied factor, max 5 |
| **TOTAL** | **100** | **29** | |

After summing, run the **Fatal Flaw Check**. If any fatal flaw triggers, return score = 0 and tier = IGNORE.

Fatal flaws: regular divergence, R:R < 1.5:1, no structural stop, opposing MSB, delta strongly opposing on impulse candle.

**Output**: score (0-100), tier (SNIPER 85+/OPPORTUNITY 65-84/WATCH 40-64/IGNORE 0-39), direction (LONG/SHORT/NEUTRAL)

**Files to create/modify**: scoring/d1_scorer.py, scoring/crt_scorer.py, scoring/smc_scorer.py, scoring/flow_scorer.py, scoring/momentum_scorer.py, scoring/timing_scorer.py, scoring/rr_scorer.py, scoring/fatal_flaws.py

## 3.2 D2 Scoring Engine — 100 Points (LTF: 15M, ALL 529 pairs)

Same structure but LTF-adapted.

| Category | Max | Min | Implementation |
|---|---|---|---|
| Entry Precision | 25 | 15 | OB retest(10) + FVG fill(8) + wick rejection(7) |
| LTF Structure Break | 20 | 0 | MSB(8) + CHoCH(7) + swing break(5) |
| Immediate Flow | 20 | 8 | volume(7) + delta(7) + effort/result(6) |
| Nascent Move Confidence | 15 | 0 | 5-condition detector (pass=15, partial=8, fail=0) |
| HTF Context Bonus | 10 | -5 | same dir +5, neutral +2, opposing -5, no data +3 |
| Momentum Quality | 10 | 0 | impulse(5) + acceleration(5) |
| Timing & Session | 5 | 0 | killzone(3) + session(2) |
| Confluence Bonus | 5 | 0 | 1pt per factor, max 5 |
| **TOTAL** | **100** | **26** | |

Fatal flaws: no structure + no precision, delta opposing 2+ candles, volume < 1.0x avg, entry > 2% past OB/FVG.

**Output**: score (0-100), tier, direction, nascent_move (bool)

**Files to create/modify**: scoring/d2_scorer.py (same sub-scorers as D1 but adapted for 15M), scoring/nascent_detector.py

## 3.3 Nascent Move Detector (Key Differentiator)

Implement the 5-condition check for Type B detection.

Conditions (all pass/fail):
1. 15M structure break (close above/below swing point with >= 1.5x volume)
2. OB interaction (retesting impulse OB within 15-30 min of break)
3. Volume + Delta (breakout candle >= 2x avg volume AND delta >= 60% aligned)
4. Liquidity sweep (stop-loss cluster taken out within last 2h, >= 0.5% of price)
5. No opposing HTF structure (1H/4H have no DIRECT opposing signal)

Logic:
- 5/5 pass → full Type B confidence (15 pts in D2)
- 3-4/5 pass → partial Type B (8 pts in D2), reduced position
- Condition 5 fails (others pass) → Type E Conflict/Trap
- < 3/5 → not a nascent move

**Files to create**: scoring/nascent_detector.py

## 3.4 Signal Type Classifier (Decision Layer)

Replace the old D3 Fusion bucket logic with Signal Type classification.

Classification order (first match wins):
1. Type C: D1 >= 85 AND D2 >= 85 AND directions align
2. Type A: D1 >= 70 AND D2 >= 50 AND directions align
3. Type B: D1 not approved AND D2 >= 72 AND nascent_move = True
4. Type D: D1 >= 70 AND D2 not aligned
5. Type E: Both valid but opposing directions
6. No Signal: everything else

**Files to create**: decision/signal_classifier.py

## 3.5 Position Sizing Engine

Formula: Effective Size = Base(1%) × Type_Mult × Score_Factor × State_Mult × Session_Factor × Correlation_Factor

Caps: 3% per trade, 5% per direction.

Type multipliers: A=0.75, B=0.35, C=1.0
State multipliers: from the 16-state table (see framework doc section 7.5)
Session factors: Killzone=1.0, Normal=0.9, Asian=0.7

Correlation filter: max 4 same-direction positions. 3+ → 0.5x, 2 → 0.75x.

**Files to create**: decision/position_sizer.py, decision/correlation_filter.py

## 3.6 Regime Awareness

Detect market regime per pair: Trending Up, Trending Down, Ranging, Volatile/Choppy.

Adjust scoring weights:
- Trending: Momentum +20%, CRT range -10%
- Ranging: CRT range +20%, Momentum -10%
- Volatile: Flow +15%, structural -5%, min score raised to 65

**Files to create**: analysis/regime_detector.py

## 3.7 Decision Layer Output

Every decision output includes:
- signal_type (A/B/C/D/E)
- d1_score, d1_tier, d1_direction
- d2_score, d2_tier, d2_direction
- market_evolution_state (from market_evolution engine)
- position_size (calculated)
- stop_width (1.5x ATR for A/C, 1.0x for B)
- ttl (4h for C, 2h for A, 15min for B, 1h for D)
- action (EXECUTE/WATCH/ALERT/IGNORE)
- expected_value (calculated per signal)

Write to StateStore.d3_decisions[pair].

**Files to create**: decision/decision_layer.py
**Files to modify**: state_store.py (add d3_decisions, remove d3_fusion)

## 3.8 Parallel D1/D2 Execution

Ensure both scanners run independently:
- D1: scans all 529 pairs on 1H/4H/1D every 15 seconds → writes to StateStore.d1_signals
- D2: scans all 529 pairs on 15M every 5 seconds → writes to StateStore.d2_signals
- Neither gated by the other
- Decision Layer polls both stores independently every 2 seconds

**Files to modify**: scanner.py, ltf_engine.py, ltf_pipeline.py, decision/decision_layer.py

## Phase 3 Completion Criteria

- D1 scoring engine produces correct 0-100 scores with tiers
- D2 scoring engine produces correct 0-100 scores with tiers
- Nascent Move Detector correctly identifies Type B signals
- Signal Type classifier correctly classifies A/B/C/D/E
- Position sizing engine calculates correct sizes
- Regime awareness adjusts weights correctly
- D1 and D2 scan all 529 pairs in parallel
- Decision Layer outputs complete decisions to StateStore.d3_decisions
- All unit tests pass for each component
- No imports of old bucket system remain

---

# ═══════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ⚠️  AUDIT CHECKPOINT 3: PHASE 3 COMPLETE — STOP HERE  ╠═══╣
# ║  Present your audit summary. Wait for approval.            ║
# ╚══════════════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════

**Present**:
1. Summary of what was built (each component)
2. File tree: what was created, modified, deleted
3. Test results: unit tests per component, pass/fail counts
4. Sample output: show 5 example decisions (signal type, score, position size, action)
5. Confirmation that D1 and D2 run in parallel (show both scanning all 529 pairs)
6. Any design decisions you made that diverged from the framework doc

**DO NOT proceed to Phase 4 until approved.**

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 4: INTEGRATION + TESTING + FRONTEND
# ═══════════════════════════════════════════════════════════════════

**OBJECTIVE**: Wire everything together, add tests, backtest, and update the frontend.

## 4.1 Integration

- Wire D1 scorer into scanner.py scan pipeline
- Wire D2 scorer into ltf_engine.py scan pipeline
- Wire Decision Layer into the D3 polling cycle
- Wire StateStore.d3_decisions to the WebSocket broadcaster
- Verify end-to-end: Binance WS → MarketData → D1 scorer → StateStore → D2 scorer → StateStore → Decision Layer → StateStore.d3_decisions → WebSocket → Frontend

## 4.2 Unit Tests

Write unit tests for:
- Each D1 sub-scorer (CRT, SMC, Flow, Momentum, Timing, RR, Confluence, Fatal Flaws)
- Each D2 sub-scorer
- Nascent Move Detector (test all 5 conditions, partial passes, failures)
- Signal Type Classifier (all 5 types + edge cases)
- Position Sizer (all type/state/session combinations)
- Correlation Filter (0, 1, 2, 3, 4+ same-direction positions)
- Regime Detector (all 4 regimes)
- Decision Layer (end-to-end classification)
- Each of the 12 bug fixes (regression tests)

Target: minimum 80% code coverage for scoring and decision modules.

## 4.3 Backtest Framework

Build a backtest runner that:
1. Loads historical kline data for all 529 pairs
2. Runs D1 and D2 scorers against historical data
3. Records every signal with its type, score, and outcome
4. Measures:
   - Win rate per signal type (A/B/C/D)
   - Average R:R per signal type
   - Expected value per signal type
   - Fast mover capture rate (how many LTF-first breakouts did Nascent Move Detector catch?)
   - False positive rate per signal type
   - Old scoring vs. new scoring comparison

## 4.4 Frontend Update

- Remove the 9-bucket display (READY/BUILDING/WAIT/EARLY/DEVELOPING/MONITOR/TRAP/IGNORE)
- Replace with Signal Type display (A/B/C/D/E) + Market Evolution State
- Add Type E alerts with "POTENTIAL FAKEOUT" flag
- Add position size display
- Add EV display per signal

**Files to modify**: Frontend JS/HTML

## Phase 4 Completion Criteria

- End-to-end pipeline working (WS → D1 → D2 → Decision → Frontend)
- Unit tests passing (80%+ coverage)
- Backtest report complete with metrics
- Frontend showing Signal Types + Market Evolution States (no buckets)

---

# ═══════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ⚠️  AUDIT CHECKPOINT 4: PHASE 4 COMPLETE — STOP HERE  ⚠️  ║
# ║  Present your audit summary. Wait for approval.            ║
# ╚══════════════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════

**Present**:
1. Integration test results (end-to-end pipeline working?)
2. Unit test report (coverage %, pass/fail counts, any skipped tests)
3. Backtest report (win rate, RR, EV per signal type, fast mover capture rate, false positive rate)
4. Frontend screenshots or description of the new display
5. Performance metrics (scan throughput, WS latency, memory usage)

**DO NOT proceed to Phase 5 until approved.**

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 5: POLISH + PRODUCTION READINESS
# ═══════════════════════════════════════════════════════════════════

**OBJECTIVE**: Harden the system for production.

## 5.1 Error Handling Hardening

- Review ALL public API endpoints for proper error handling
- Review ALL external API calls (Binance REST, Binance WS) for retry logic
- Add circuit breakers for sustained failures
- Add graceful degradation (if one component fails, the rest keep running)

## 5.2 Logging

- Structured logging (JSON format) for all scoring decisions
- Scoring breakdown logs (each sub-score logged with its components)
- Signal type classification logs
- Pipeline latency logs (time per pair per scanner)
- Error logs with context (pair, timeframe, error type)

## 5.3 Monitoring

- Scoring distribution (histogram of scores per scan cycle)
- Pipeline latency (D1 scan time, D2 scan time, Decision Layer processing time)
- WS message rates (incoming klines, outgoing signals)
- Signal type distribution (how many A/B/C/D/E per cycle)
- Error rates per component

## 5.4 Documentation

- Update README.md with new architecture
- Write SCORING_METHODOLOGY.md (the complete scoring framework for traders)
- Write SIGNAL_PLAYBOOK.md (what each signal type means, when to take it)
- Write ARCHITECTURE.md (system design, data flow, StateStore schema)
- Write DEPLOYMENT.md (how to deploy, configure, monitor)

## 5.5 Alert System

- Type E (Conflict/Trap) signals trigger alerts
- Pipeline failure alerts (if D1 or D2 stops scanning)
- WS connection loss alerts
- Scoring anomaly alerts (sudden spike in SNIPER signals = possible data issue)

## Phase 5 Completion Criteria

- All error handling paths tested
- Structured logging active
- Monitoring dashboards/configs in place
- Documentation complete
- Alert system configured
- System ready for production deployment

---

# ═══════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════════════╗
# ║  ✅  FINAL AUDIT: PHASE 5 COMPLETE — PROJECT DELIVERED  ⚠️  ║
# ║  Present final summary. This is the handoff point.         ║
# ╚══════════════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════

**Present**:
1. Complete file tree (created, modified, deleted)
2. Summary of all changes across all 5 phases
3. Final test report (all tests passing, coverage %)
4. Final backtest report (all metrics)
5. Documentation index
6. Known limitations and future improvements
7. Deployment checklist

---

# [[═══════════════════════════════════════════════════════════════════
# EXECUTION RULES
# ═══════════════════════════════════════════════════════════════════

1. **Execute one phase at a time.** Do not start Phase 2 until Phase 1 audit is approved.
2. **Each phase must be complete before the audit checkpoint.** No "I'll finish that in the next phase."
3. **The audit summary must be concrete.** Show specific numbers, file paths, test results. Not vague statements.
4. **If you encounter a blocker**, document it in the audit summary and propose alternatives. Do not silently skip.
5. **If a phase takes multiple turns**, that's fine. Stay in the phase until completion criteria are met.
6. **You have full access to the codebase.** Read files, modify files, run tests. Use all available tools.
7. **Follow Python best practices.** Type hints, docstrings, error handling, async correctness.

## BEGIN WITH PHASE 1 NOW.
