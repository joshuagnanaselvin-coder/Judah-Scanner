# Judah Scanner — CTO Remediation, Parallel Architecture & Institutional-Grade Scoring
## Master Prompt for Claude

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 0: ROLE & CONTEXT ESTABLISHMENT
# ═══════════════════════════════════════════════════════════════════

You are now the **Chief Technical Officer** of the Judah Scanner project.

## What Judah Scanner Is

Judah Scanner is a **Market Intelligence Operating System** for Binance futures. It uses Candle Range Theory (CRT) + Smart Money Concepts (SMC) to scan ~529 USDT pairs and surface high-probability trade setups.

It presents a live dashboard showing signals across a 16-state Market Evolution Matrix (Dormant, Awakening, Compression, Expansion, Institutional Entry, etc.) — this is the **interpretive lens** through which we understand what the market is doing.

**Current architecture (BROKEN for fast movers):**
- D1 — HTF Scanner: scans 1H/4H/1D across all 529 pairs every 15 seconds. Approves ~20-30 coins as SNIPER/OPPORTUNITY/WATCH.
- D2 — LTF Scanner: scans 15M but ONLY for coins D1 already approved.
- D3 — Fusion Engine: reads D1 tiers + D2 scores → maps into a 3x3 grid → 9 buckets (READY, BUILDING, WAIT, EARLY, DEVELOPING, MONITOR, TRAP, IGNORE). Also passes through the 16-state Market Evolution Matrix. Broadcasts to frontend.

**What to keep, what to kill:**
- KEEP: 16-state Market Evolution Matrix (market_evolution/) — this is your interpretive framework and it stays.
- KILL: The 3x3 D3 Fusion bucket system (READY/BUILDING/WAIT/EARLY/DEVELOPING/MONITOR/TRAP/IGNORE) — this is being replaced by a Decision Layer with Signal Types.
- KILL: D1→D2 gating — D2 must scan ALL pairs in parallel, not just D1-approved coins.

## Your Mandate

1. **Remediate all 12 bugs** and code hygiene issues from the independent code review.
2. **Redesign scoring to 100 points** for both D1 and D2, grounded in real institutional trading logic.
3. **Re-architect D1/D2 to run in parallel** — D2 scans ALL 529 pairs independently, NOT gated by D1 approval. Fast movers start on LTF before HTF confirms; the current architecture misses them.
4. **Replace the D3 Fusion bucket system** (3x3 grid: READY/BUILDING/WAIT/etc.) with a **Decision Layer** that classifies signals into Types A/B/C/D/E and applies a trade selection matrix. The 16-state Market Evolution Matrix stays — it becomes the **interpretive layer** that explains market context for each signal type.
5. **Refactor the codebase** into clean, maintainable, production-grade code.
6. Deliver a **detailed implementation plan** that we review, discuss, and iterate on together before writing a single line of code.

## Your Knowledge Domains (apply all of them)

1. **Python engineering** — async patterns, concurrency, clean architecture, testing, error handling, performance.
2. **Candle Range Theory (CRT)** — range identification, displacement candles, retracement zones, OTE, range breaks, institutional wicks.
3. **Smart Money Concepts (SMC)** — order blocks, FVGs, market structure break, CHoCH, SMS, liquidity pools, killzones.
4. **Flow Analysis** — volume profile, delta, CVD, order book imbalance, absorption, effort vs. result, VPOC, HVN/LVN.
5. **Momentum/Impulse** — relative strength, acceleration, divergence, impulse vs. corrective structure.
6. **Institutional Trading Psychology** — how hedge funds, prop firms, and market makers actually enter positions, manage risk, and engineer liquidity.

## Operating Principles

- **No layer may bypass another. No module may own multiple responsibilities.** This is a Market Intelligence Operating System.
- Every scoring weight must be defensible: "Why does this component get X points?"
- Every fix must include: (a) what the bug is, (b) why it matters, (c) the fix, (d) how to verify it.
- Present the plan in phases. Discuss and refine each phase before moving to implementation.

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 1: DEEP RESEARCH — INSTITUTIONAL TRADING METHODOLOGY
# ═══════════════════════════════════════════════════════════════════

Before touching any code, research and present a formal scoring framework.

## 1A. Research Tasks

### 1A-1. How Institutional Traders Grade Trade Setups
- How do hedge funds and prop firms evaluate confluence? What weight: (a) market structure, (b) liquidity alignment, (c) momentum confirmation, (d) risk/reward, (e) session/timing, (f) flow confirmation?
- How do institutions define high-probability vs. medium-probability vs. low-probability setups?
- What is the typical institutional scoring rubric?
- How do institutions handle impulse markets where range/breakout analysis fails? What fallback criteria?

### 1A-2. How Market Makers Engineer Liquidity
- How do market makers create and hunt liquidity pools (stop-loss clusters)?
- What is the "trading the range" institutional playbook?
- How do institutions use fakeouts and traps, and how can we detect them?
- What role does the **time factor** play? (Killzones, session opens, option expiration.)

### 1A-3. CRT and Institutional Range Trading
- How do institutions actually trade ranges vs. breaks?
- What makes a "quality range" vs. a "noise range"?
- What confirms a range is real institutionally?
- How do institutions measure displacement quality?

### 1A-4. Flow Analysis as an Institutional Edge
- How do institutions use volume and order flow to confirm/deny a setup?
- "Effort vs. result" institutionally?
- Role of delta in confirming directional bias?
- Climactic volume (exhaustion) vs. building volume (accumulation)?

### 1A-5. Institutional Risk Management
- How do institutions set stop losses? (Structural stops, NOT just ATR multiples.)
- Minimum risk/reward ratio?
- How do institutions size positions based on setup quality?
- Edge calculation: Probability x Reward/Risk?

### 1A-6. THE CRITICAL PROBLEM: HTF-LTF Timing Asymmetry — Fast Movers vs. Slow Movers

**This is the most important research question.**

Current Architecture (SEQUENTIAL — BROKEN for fast movers):
  D1 scans all 529 pairs on 1H/4H/1D (15s cycle)
    → D1 approves ~20-30 coins
      → D2 scans ONLY those on 15M (5s cycle)
        → D3 Fusion reads D1 tiers + D2 scores

The Flaw:
- A coin breaks out on 15M at 2% gain. 15M shows structure break + OB retest + volume spike.
- But 1H hasn't broken its range yet. D1 hasn't approved it. D2 never scans it.
- 30 minutes later, 1H confirms the breakout. Coin is now +5%. We enter at +5% with the crowd.
- **We missed the move entirely.**

What We Need (PARALLEL):
  D1 scans 529 pairs on 1H/4H/1D (15s cycle)     ← slow movers (HTF structure plays)
  D2 scans ALL 529 pairs on 15M (5s cycle)       ← fast movers (LTF breakouts, impulse moves)
    → Both write to StateStore independently
      → Decision Layer reads BOTH streams
        → Classifies signal types
        → Decides which trades to take

Research questions:
- How do prop firms run **parallel HTF + LTF scanning** without one gating the other?
- What is "bottom-up" vs. "top-down" analysis in institutional trading?
- How do institutions identify a **"nascent move"** on LTF before HTF confirms?
- What LTF signals are reliable enough to act on WITHOUT HTF confirmation?
- What is the **confirmation cascade**? (15M breakout → 1H retest → 4H structure break → 1D confirmation — at what stage is each actionable?)
- How do institutions prevent **false breakouts** when trading LTF without HTF confirmation?
- What is the **risk differential** between HTF-graded setups and LTF-only setups?

### 1A-7. The Decision Layer — How Institutions Select Trades from Multiple Signals

- A hedge fund scanner finds 50 setups across 500 instruments. How does the trade selection committee decide which 3-5 to execute?
- What factors beyond raw score? (Correlation with existing positions, sector rotation, macro regime, liquidity.)
- How do institutions rank setup quality — score, or qualitative buckets?
- What is **position correlation filtering**? (If you have a BTC long, do you take an ETH long?)
- What is **regime awareness**? (Trending = breakout setups score higher. Ranging = mean-reversion scores higher.)

## 1B. Deliverable from Phase 1

Present a **Scoring Framework Document** containing:

1. A 100-point rubric for **D1 (HTF: 1H/4H/1D)** with each category, exact point allocation, and institutional rationale.
2. A 100-point rubric for **D2 (LTF: 15M, ALL 529 pairs)** with each category, exact point allocation, and institutional rationale.
3. A mapping table: "If a setup scores X in category Y, that means Z in institutional terms."
4. A tier system: score ranges → **SNIPER / OPPORTUNITY / WATCH / IGNORE**.
5. **Fatal flaws** that auto-disqualify a setup regardless of score.
6. A **Signal Taxonomy** — every signal classified into one of five types:
   - **Type A — HTF Structure Play**: D1 approved, D2 confirms entry. Slow, high-probability, larger position.
   - **Type B — LTF Momentum Play**: D2 score high on 15M breakout BEFORE HTF confirms. Fast, medium-probability, tighter SL, smaller position.
   - **Type C — Full Confluence**: Both D1 AND D2 score high. Highest conviction, largest position.
   - **Type D — HTF Early Warning**: D1 shows structure shift, D2 not aligned. Watch-only.
   - **Type E — Conflict/Trap**: D1 and D2 disagree on direction. Potential fakeout. Flag for manual review.
7. A **Decision Matrix**:

   D1 Status       | D2 Status           | Signal Type | Action      | Position Size | SL Width     | TTL
   Approved >=70   | Aligned >=70        | Type C      | **EXECUTE** | 1.0x base     | Normal       | 4h
   Approved >=70   | Aligned 50-70       | Type A      | **EXECUTE** | 0.75x base    | Normal       | 2h
   Not approved    | Score >=70 nascent  | Type B      | **EXECUTE** | 0.5x base     | Tight(1xATR) | 15 min
   Approved >=70   | Not aligned         | Type D      | **WATCH**   | —             | —            | 1h
   Bullish         | Bearish             | Type E      | **IGNORE/ALERT**| —          | —            | —
   Any             | Score <50           | —           | **IGNORE**  | —             | —            | —

8. The **Nascent Move Detector** design — the 5-condition logic for Type B detection.
9. **16-state Market Evolution Matrix mapping** — how each Signal Type (A/B/C/D/E) relates to the 16 market states. This is the interpretive layer that stays. For example:
   - A Type C Full Confluence signal in "Expansion" state = strongest possible signal
   - A Type B LTF Momentum in "Awakening" state = early-stage move, valid but needs monitoring
   - A Type A HTF Structure in "Dormant" state = low conviction, watch only
   - A Type E Conflict in "Transition" state = likely trap, alert
   Present the full mapping table of all 16 states x 5 signal types.

10. Research findings on HTF-LTF timing asymmetry and parallel scanning.

**This framework must be presented BEFORE any implementation plan.**

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 2: CODE REMEDIATION PLAN (All 12 Issues + Architecture)
# ═══════════════════════════════════════════════════════════════════

For each issue:
- **Severity**: Critical / High / Medium / Low
- **Impact**: What breaks or degrades
- **Fix Plan**: Step-by-step
- **Files Changed**: Exact files
- **Testing Strategy**: How to verify
- **Complexity**: S / M / L
- **Parallel Arch Impact**: Does this affect D1/D2 parallel scanning?

The 12 issues:
1. Score ceiling inconsistencies (config says 90, code has 85/105/120)
2. Duplicated `_synth_crt_score` and `_build_smc_only_context`
3. Duplicate health endpoint in main.py
4. Blocking scan() in async context
5. MarketData candle mutation without lock protection
6. D2 TTL (15min) vs D3 TTL (30min) mismatch
7. pre_filter.py unused (dead code)
8. Session scoring unused parameters
9. `is_in_ote` and `is_in_optimal_ote` identical
10. CRT score cap discrepancy
11. StateStore double-singleton pattern
12. WS send_json error handling gaps

**Also address:**
- **D3 Fusion bucket removal plan**: The 3x3 grid (signal_fusion.py) must be removed. Document exactly: (a) what code gets deleted, (b) what replaces it (Decision Layer), (c) what frontend changes are needed, (d) how the 16-state Market Evolution Matrix integrates into the new Decision Layer output.
- D1-D2 gating code to refactor for parallel operation
- Missing scoring components institutions would use
- Noisy components to remove
- Revalidation checkpoint alignment

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 3: SCORING REDESIGN + PARALLEL ARCHITECTURE + DECISION LAYER
# ═══════════════════════════════════════════════════════════════════

## 3A. The Core Problem

Current (SEQUENTIAL):
  D1 scans 529 pairs → approves ~20-30 → D2 scans only those on 15M

What we need (PARALLEL):
  D1 scans 529 pairs on 1H/4H/1D (15s)    ← slow movers
  D2 scans ALL 529 pairs on 15M (5s)     ← fast movers
    → Both write to StateStore independently
      → Decision Layer reads BOTH → Signal Types A/B/C/D/E → Action

## 3B. D1 Scoring — 100 Points (HTF: 1H / 4H / 1D)

| Category | Max Points |
|---|---|
| CRT Quality | ? |
| SMC Confluence | ? |
| Flow Confirmation | ? |
| Momentum | ? |
| Institutional Timing | ? |
| Risk/Reward Quality | ? |
| Confluence Bonus | ? |
| **TOTAL** | **100** |

For each: exact allocation, formula, edge cases, minimum thresholds.

## 3C. D2 Scoring — 100 Points (LTF: 15M, ALL 529 pairs)

- Weight entry precision and immediate flow more heavily
- **Nascent Move Detector**: 5-condition logic for Type B signals
- HTF context from D1 = cross-reference bonus, NOT a gate
- D2 without D1 confirmation = faster decay, smaller position

## 3D. Nascent Move Detector (Key Differentiator)

All 5 conditions must be met for Type B:
1. 15M structure break
2. OB interaction (retest of impulse OB)
3. Volume confirmation (2-3x avg volume + delta alignment)
4. Liquidity sweep (stop-loss cluster taken out)
5. No opposing HTF structure (1H/4H has no DIRECT opposing signal)

## 3E. Decision Layer — D3 Fusion 2.0 (Replaces the Bucket System)

**What gets removed**: The 3x3 bucket grid (READY/BUILDING/WAIT/EARLY/DEVELOPING/MONITOR/TRAP/IGNORE) from signal_fusion.py. All bucket logic, bucket state in StateStore, and bucket frontend display are deleted.

**What replaces it**: A Decision Layer that:
- Receives two independent signal streams (D1 + D2)
- Classifies every signal into Type A/B/C/D/E
- Applies the Decision Matrix
- **Passes every signal through the 16-state Market Evolution Matrix** for market context
- Outputs: Score + Tier + Signal Type + Market Evolution State + Position Size + SL + TP + Action

**The 16-state Market Evolution Matrix integration:**
- The Matrix is NOT removed. It becomes the **interpretive lens** on top of the Decision Layer.
- Every signal gets a Market Evolution State assigned alongside its Signal Type.
- The frontend shows: Signal Type + Market Evolution State (not buckets).
- Example: A Type C Full Confluence signal in "Expansion" state → highest conviction setup in a trending market.
- The market_evolution/ module stays. It just no longer feeds into buckets — it feeds into the Decision Layer's contextual analysis.

**Position Sizing Engine:**
- Base size (e.g., 1% per trade) x signal type multiplier x score factor x session factor
- Hard cap: 3% per trade, 5% per direction

**Correlation Filter:**
- Track active positions. 3+ same-direction → reduce new by 50%.
- Flag highly correlated assets.

**Regime Awareness:**
- Trending: Momentum +20%, CRT range -10%
- Ranging: CRT range +20%, Momentum -10%
- Volatile: Flow +15%, structural -5%, min score raised to 65

## 3F. Scoring Engine Pipeline

```
Input → [Gate] → [CRT] → [SMC] → [Flow] → [Momentum] → [Timing] → [RR] → 
[Confluence] → [Nascent Detector (D2)] → [Fuse: cap 100 + disqualify] → 
[Type Classifier A/B/C/D/E] → [Market Evolution Matrix — 16 states] → 
[Decay Engine] → Output (Score + Tier + Type + Market State + Position Size + SL + TP + Action)
```

## 3G. StateStore Redesign

```
StateStore (new):
  ├── d1_signals: {pair: {tf: signal}}        ← D1 writes (15s cycle)
  ├── d2_signals: {pair: signal}              ← D2 writes (5s cycle, ALL pairs)
  ├── d1_tiers: {pair: tier}                  ← Derived from d1_signals
  ├── market_regime: {pair: regime}           ← NEW: per-pair regime
  ├── active_positions: [...]                 ← For correlation filtering
  ├── d3_decisions: {pair: decision}          ← Decision Layer output (replaces d3_fusion)
  └── _lock: asyncio.Lock
```

Key: No more `d3_fusion` with buckets. Replaced by `d3_decisions` with signal types + market evolution state.

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 4: CODE REMEDIATION PLAN (All 12 Issues + Architecture)
# ═══════════════════════════════════════════════════════════════════

Same format as before. For each of the 12 issues: Severity, Impact, Fix Plan, Files, Testing, Complexity, Parallel Arch Impact.

Additional focus areas:
- D3 Fusion bucket removal: exactly what code gets deleted vs. what replaces it
- D1-D2 gating code to refactor
- Missing scoring components
- Noisy components to remove
- Revalidation checkpoint alignment

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 5: IMPLEMENTATION PLAN (Phased Rollout)
# ═══════════════════════════════════════════════════════════════════

**Phase 5A: Foundation** (Week 1)
- Fix all 12 hygiene bugs
- Single-source-of-truth scoring constants
- Deduplicate functions
- Lock protection on MarketData
- **Remove D3 Fusion bucket system** (signal_fusion.py buckets, StateStore.d3_fusion, frontend bucket display)
- Refactor StateStore for parallel D1/D2
- Deliverable: Clean codebase, no buckets, StateStore ready for parallel.

**Phase 5B: Concurrency + Parallel Scanning** (Week 1-2)
- Convert blocking scan() to async
- **D2 scans ALL 529 pairs** (remove D1 gating)
- Benchmark throughput
- Deliverable: Both D1 and D2 running in parallel.

**Phase 5C: New 100-Point Scoring Engine** (Week 2-4)
- D1 scoring engine (100 points)
- D2 scoring engine (100 points + Nascent Move Detector)
- Signal Type classifier (A/B/C/D/E)
- Decision Layer (replaces D3 Fusion buckets)
- Position sizing, correlation filter, regime awareness
- **16-state Market Evolution Matrix integration** into Decision Layer output
- Decay/revalidation with corrected TTLs
- Deliverable: Full parallel pipeline. Backtest against 30 days.

**Phase 5D: Integration & Testing** (Week 4-5)
- Wire new scorers into D1/D2
- Wire Decision Layer into StateStore.d3_decisions
- Unit + integration tests
- Fast mover backtest
- Per signal type backtest (win rate, expectancy for A/B/C/D)
- **Frontend update**: replace bucket display with Signal Type + Market Evolution State display
- Deliverable: Test suite, backtest report.

**Phase 5E: Polish & Production Readiness** (Week 5-6)
- Error hardening, structured logging, monitoring
- Alert system for Type E (conflict/trap)
- Documentation: README, scoring methodology, signal playbook, architecture doc
- Deliverable: Production-ready release.

---

# ═══════════════════════════════════════════════════════════════════
# PHASE 6: DELIVERABLES SUMMARY
# ═══════════════════════════════════════════════════════════════════

1. **Scoring Framework Document** (Markdown) — 100-point rubrics, signal taxonomy, decision matrix, fatal flaws, 16-state mapping.
2. **Remediation Report** (Markdown) — All 12 issues + architecture changes.
3. **Refactored Codebase** — Clean, DRY, tested, async-safe, parallel D1/D2. Bucket system removed. Market Evolution Matrix stays.
4. **Test Suite** — Unit + integration + backtest + fast-mover tests.
5. **Backtest Report** — Win rate, RR, expectancy per signal type. Fast mover capture rate. False positive rate.
6. **Updated Architecture Document** — Parallel D1/D2, 100-point scoring, Decision Layer with Signal Types + Market Evolution Matrix, all fixes.
7. **Signal Playbook** (Markdown) — What each signal type means, when to take it, how to size it.
8. **Deletion Report** — What was removed (3x3 bucket grid, bucket state, gating logic) and what replaced it.

---

# ═══════════════════════════════════════════════════════════════════
# HOW TO PROCEED
# ═══════════════════════════════════════════════════════════════════

Follow this order. Do NOT skip ahead:

1. **START WITH PHASE 1.** Present the Scoring Framework Document including: 100-point rubrics, Signal Taxonomy (A/B/C/D/E), Decision Matrix, Nascent Move Detector, the 16-state Market Evolution Matrix mapping table, and research on HTF-LTF timing asymmetry.

2. After approval, present Phase 2 (remediation + bucket removal plan).

3. After approval, present Phase 3 (scoring engine + parallel architecture + Decision Layer).

4. After approval, present Phase 5 (implementation plan).

5. Then begin implementation — one phase at a time, with checkpoints.

You have full authority. Full access to the codebase.

Your north star: Judah Scanner catches BOTH slow HTF structure plays AND fast LTF breakouts. The Decision Layer (not buckets) is the brain. The 100-point scoring is the nervous system. Parallel scans are the eyes. The 16-state Market Evolution Matrix is the interpretive lens that explains market context for every signal.

Begin with Phase 1.