# Judah Scanner — Scoring & Architecture Improvement Plan

## Overview

This plan addresses the core issue: D1 scores stuck below 50 due to compounding bugs, conservative scoring design, and frontend-backend desync. Based on a full codebase audit and comparison with institutional systematic trading frameworks (Renaissance, Two Sigma, AQR, prop firms).

## Execution Progress

- [x] **Batch A — Phase 1: Bug Fixes** (Score impact: +10-15 pts per signal)
  - [x] 1.1 Fix `_detect_swept_level()` inverted logic
  - [x] 1.2 Fix VWAP session start for 4H/1D timeframes
  - [x] 1.3 Fix `_score_timing()` cap (claims 10, delivers 7)
  - [x] 1.4 Fix fatal flaw checks referencing non-existent flow keys
  - [x] 1.5 Fix WebSocket reconnection silent failure
- [ ] **Batch B — Phase 2: Scoring Architecture Redesign** (Score impact: +8-12 pts)
  - [ ] 2.1 Calibrate tier thresholds to realistic score distribution
  - [ ] 2.2 Z-score normalization across signal universe
  - [ ] 2.3 Add WEAK tier (weak signals get shown, not rejected)
  - [ ] 2.4 Regime-aware component weights
- [ ] **Batch B — Phase 3: Frontend-Backend Sync**
  - [ ] 3.1 Frontend displays correct composite_score for D1+D2
  - [ ] 3.2 Fix score history sparkline data format
  - [ ] 3.3 Fix scan logs panel (returns empty array)
- [ ] **Phase 4: Advanced (Optional)**
  - [ ] 4.1 Bayesian confidence updating
  - [ ] 4.2 Cross-signal correlation scoring
  - [ ] 4.3 Volume-weighted ATR

---

## Phase 1: Bug Fixes (Batch A)

### Fix 1.1 — Inverted `_detect_swept_level()` logic

**File:** `backend/engines/signal_builder.py:187-208`
**Impact:** +2 to +5 R/R points per signal

The condition `c.close < level and c.close > c.open` is backwards. For a bullish SL at a swing low, a "swept" level means price closed **below** it with a bearish candle (`c.close < level and c.close < c.open`). The current code marks UNSWEPT levels as swept, causing the SL finder to skip valid structural stops → SLs end up further away → worse R/R → lower score.

### Fix 1.2 — Broken VWAP on 4H/1D timeframes

**File:** `backend/engines/flow_analyzer.py:53-72`
**Impact:** +3 to +7 flow points per signal on 4H/1D

The `_find_session_start()` function only checks for "1h" timeframe, so 4H and 1D always fall to the `else` branch (last 50 bars). VWAP is never computed correctly for D1's primary timeframes.

### Fix 1.3 — `_score_timing()` claims 10 pts but maxes at 7

**File:** `backend/engines/engine.py:230-266` + `config.py:89-91`
**Impact:** +3 timing points per signal

The config defines `TIMING_DAYS_MAX = 3` but never implements it. The function returns `min(4 + 3, 10) = 7` max.

### Fix 1.4 — Fatal flaw checks reference non-existent flow keys

**File:** `backend/engines/engine.py:393-399` and `backend/engines/ltf_pipeline.py:58-93`
**Impact:** Delta-opposing and entry-distance checks never fire

`flow.get("delta_history")`, `flow.get("ob_proximity")`, `flow.get("fvg_proximity")` — none of these keys exist in the `analyze_flow()` return dict.

### Fix 1.5 — WebSocket reconnection fails silently

**File:** `frontend/app.js:428-470`
**Impact:** Frontend appears dead if initial WS connection fails

`ws.onclose` only fires after a successful `onopen`. If the initial connection fails at TCP level, neither handler fires.

---

## Phase 2: Scoring Architecture Redesign (Batch B)

### Change 2.1 — Calibrate tier thresholds to realistic score distribution

**Current thresholds:**
```
SNIPER:      >= 85
OPPORTUNITY: >= 65
WATCH:       >= 40
```

**Proposed thresholds (calibrated to realistic distribution after bug fixes):**
```
SNIPER:      >= 65   (top 10% of signals)
OPPORTUNITY: >= 48   (top 30% of signals)
WATCH:       >= 30   (bottom 60% still shown)
```

### Change 2.2 — Z-score normalization

Replace hard 90-pt cap with z-score normalization across the live signal universe each scan cycle.

### Change 2.3 — Add WEAK tier

```
REJECTED (< 20):   Not shown
WEAK (20-34):      Shown with 0.25x position size, "Low Conviction" badge
WATCH (35-49):     Shown with 0.5x position size
OPPORTUNITY (50-64): Shown with 0.75x position size
SNIPER (65+):      Shown with 1.0x position size
```

### Change 2.4 — Regime-aware component weights

Adjust scoring weights based on detected market regime (trending, range-bound, volatile).

---

## Phase 3: Frontend-Backend Sync (Batch B)

### Change 3.1 — Frontend score display

Ensure frontend always displays `composite_score` for both D1 and D2.

### Change 3.2 — Sparkline data format

Standardize score history format in backend and frontend.

### Change 3.3 — Logs panel fallback

`/api/logs` returns empty because `ENABLE_SIGNAL_LOGGING = False`. Add fallback to performance tracker data.

---

## Phase 4: Advanced (Optional)

### Change 4.1 — Bayesian confidence updating

Update confidence based on actual signal outcomes using Beta distribution posterior.

### Change 4.2 — Cross-signal correlation scoring

Penalize scores when too many signals share the same direction in the same session.

### Change 4.3 — Volume-weighted ATR

Use volume-weighted ATR for structural level calculations.
