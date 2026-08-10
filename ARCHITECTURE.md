# Judah Scanner — Complete Architecture

## Overview

Judah is a 3-dimensional cryptocurrency scanner for Binance Futures. It analyzes ~500 USDT pairs across multiple timeframes using institutional trading methodology (ICT, Smart Money Concepts, Candle Range Theory) to surface high-probability trade setups.

**Stack:** FastAPI + asyncio (Python backend) | Vanilla JS (frontend) | Binance REST + WebSocket (data)

---

## The 3 Dimensions

```
┌─────────────────────────────────────────────────────────────┐
│  D1 — HTF Scanner (1H / 4H / 1D)                           │
│  backend/scanner.py → backend/engines/engine.py              │
│  Scans: all pairs × 3 HTF timeframes                        │
│  Output: D1 tier per coin (SNIPER/OPP/WATCH/REJECTED)       │
│  Speed: 15-second scan cycle                                 │
├─────────────────────────────────────────────────────────────┤
│  D2 — LTF Scanner (15M)                                      │
│  backend/engines/ltf_engine.py → ltf_pipeline.py → ltf_scanner.py │
│  Scans: all pairs × 15M (independent of D1)                 │
│  Output: LTFSignal with entry, SL, TP, RR, tier             │
│  Speed: 5-second scan cycle                                  │
├─────────────────────────────────────────────────────────────┤
│  D3 — Decision Layer (Fusion)                                │
│  backend/engines/signal_fusion.py                            │
│  Input: D1 tiers + D2 signals from state_store               │
│  Output: Signal Types A/B/C/D/E + Market Evolution + EV      │
│  Speed: 2-second poll cycle                                  │
├─────────────────────────────────────────────────────────────┤
│  D4 — Market Evolution (16-State Matrix)                     │
│  backend/market_evolution/                                   │
│  Maps (D1_tier, D2_tier) → Evolution state + spiral + action │
└─────────────────────────────────────────────────────────────┘
```

---

## Data Flow

```
                    ┌──────────────┐
                    │  Binance API  │
                    │  REST + WS    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  market_data  │
                    │  .py          │
                    │  (Singleton)  │
                    │               │
                    │  - bootstrap() │ ← REST klines on startup
                    │  - WS streams │ ← Live candle updates
                    │  - get_candles()│ ← Cached OHLCV
                    └──────┬───────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │   D1 Scan   │  │   D2 Scan   │  │   Regime    │
   │  scanner.py │  │ltf_engine.py│  │  Engine     │
   │  15s cycle  │  │  5s cycle   │  │  (hourly)   │
   └──────┬──────┘  └──────┬──────┘  └─────────────┘
          │                │
          └────────┬───────┘
                   ▼
          ┌────────────────┐
          │  signal_store  │  ← D1 signals (dicts)
          │  .py           │    - add, refresh, revalidate
          └───────┬────────┘    - FVG ledger, TTL cleanup
                  │
          ┌───────▼────────┐
          │  state_store   │  ← THE shared state bus
          │  .py           │    - d1_tiers (per coin)
          └───────┬────────┘    - d2_signals (LTFSignal objects)
                  │              - d3_decisions (fusion output)
                  │              - positions, regimes
          ┌───────▼────────┐
          │signal_fusion.py│  ← D3: reads state_store
          │ (FusionEngine) │    - classify_signal_type() → A/B/C/D/E
          └───────┬────────┘    - _compute_alignment() → 0-20 score
                  │              - _d1_structural_sl() → tighter SL
                  │              - market_evolution.evaluate() → 16-state
                  │
          ┌───────▼────────┐
          │  ws_hub.py     │  ← broadcast to frontend
          │  (broadcast)   │
          └───────┬────────┘
                  │
          ┌───────▼────────┐
          │  main.py       │  ← FastAPI
          │  /ws-fusion     │    - WebSocket endpoint
          │  /api/*         │    - REST endpoints
          └───────┬────────┘
                  │
          ┌───────▼────────┐
          │  Frontend      │
          │  app.js        │
          │  index.html    │
          │  style.css     │
          └────────────────┘
```

---

## Backend Architecture

### 1. Entry Point: `backend/main.py`

**Purpose:** FastAPI server — serves frontend, REST API, and WebSocket.

**Key routes:**
| Route | Purpose |
|---|---|
| `GET /` | Serve `index.html` |
| `WS /ws-fusion` | Push D3 signals to frontend |
| `WS /ws` | Push D1 signal stream |
| `GET /api/signals` | D1 signals list |
| `GET /api/fusion` | D3 decision signals |
| `GET /api/stats` | Scanner stats |
| `GET /api/logs` | Recent signal logs |
| `GET /api/debug-fusion` | Diagnostic D1/D2/Decision overlap |
| `POST /api/restart` | Full scanner restart |

**Startup sequence (`_bootstrap`):**
1. Fetch USDT pairs from Binance REST API
2. Start D1 Scanner (`scanner.start()`)
3. Start D2 Engine (`ltf_engine.start()`)
4. Start D3 Fusion Engine (`fusion_engine.start()`)

---

### 2. Data Layer

#### `backend/market_data.py` — MarketData (Singleton)

**Purpose:** Single source of truth for all OHLCV data.

| Method | Description |
|---|---|
| `bootstrap(symbols)` | REST pull of 50 candles for every symbol × timeframe on startup |
| `connect_websocket(symbols)` | Subscribe to Binance WS streams for live ticks |
| `get_candles(symbol, tf)` | Return cached candle list (thread-safe with asyncio.Lock) |
| `on_candle_close` | Callback fired on every candle close → triggers WS scan |

**Architecture notes:**
- Singleton pattern (lazy init)
- Stores candles as `dict[str, list[Candle]]` keyed by `"SYMBOL_TIMEFRAME"`
- WS uses Binance's combined stream format (up to 793 streams per connection)
- Candle data shared between D1, D2, and regime engine — no duplication

#### `backend/state_store.py` — StateStore (Singleton)

**Purpose:** Loose-coupling bus between D1, D2, D3. No function calls between dimensions — all communication goes through here.

| Store | Key | Value | Writer | Reader |
|---|---|---|---|---|
| `d1_tiers` | coin | `{tier, score, timeframes}` | D1 Scanner | D2, D3 |
| `d2_signals` | coin | `LTFSignal` object | D2 Engine | D3 |
| `d3_decisions` | coin | Package dict (frontend) | D3 Fusion | Frontend |
| `positions` | coin | `{entry, sl, tp, size}` | Trader (future) | — |
| `regimes` | coin | Regime data | Regime Engine | Frontend |

**Thread safety:** `asyncio.Lock` around all writes.

#### `backend/signal_store.py` — SignalStore (D1 only)

**Purpose:** Stores D1 HTF signals with freshness tracking, revalidation, and TTL.

| Feature | Detail |
|---|---|
| Freshness | HOT (0-12 ticks) → WARM → COOL → COLD → DEAD |
| Decay | Score decays visually but tier is locked to base_score |
| Revalidation | Checkpoints at 15min and 30min — re-scans to confirm |
| TTL | 15 minutes, then marked TIMEOUT |
| FVG Ledger | Tracks FVG creation/fill events per signal |

---

### 3. Scanning Engines

#### D1 Engine: `backend/scanner.py` + `backend/engines/engine.py`

**Pipeline (per coin per timeframe):**
```
Candles → ATR Gate → Range Gate → Flow Gate → CRT → SMC → Signal Builder → Score → Tier
```

**Flow:**
1. **Candidate Selection** (`candidate_selector.py`): Adaptive ATR filter — each coin's threshold = 60% of its 50-period rolling ATR baseline. ~80% of pairs filtered out.
2. **Concurrent Scan** (`engine.py:scan()`): 20 parallel semaphore-limited scans.
3. **CRT Engine** (`crt_engine.py`): 5-step Candle Range Theory (Consolidation → Range Candle → Displacement → Fill → Entry). Max 25 pts.
4. **SMC Engine** (`smc_engine.py`): Smart Money Concepts — MSB, OB, FVG, Liquidity Sweep. Max 20 pts (scaled to 25).
5. **Signal Builder** (`signal_builder.py`): Combines CRT + SMC into entry/SL/TP/RR. SL = nearest structural swing + 0.3× ATR buffer, fallback = 1.5× ATR. Hard cap at 4%.
6. **D1 Scoring** (100 pts): CRT(20) + SMC(25) + Flow(15) + Momentum(15) + Timing(10) + R/R(10) + Confluence(5)
7. **Tier Assignment:** SNIPER ≥85, OPPORTUNITY ≥65, WATCH ≥40, else REJECTED

**Three-pass batch scan:**
- Pass 1: Revalidate + refresh existing signals
- Pass 2: Candidate filter → concurrent scan for new signals
- Pass 3: Build D1 tiers per coin, push to state_store

**WebSocket trigger:** On candle close, offloads `scan()` to background task (non-blocking).

#### D2 Engine: `backend/engines/ltf_engine.py` + `ltf_pipeline.py` + `ltf_scanner.py`

**Pipeline (per coin):**
```
Candles → ATR Gate → Range Gate → Flow Gate → CRT → SMC → Fatal Flaw Check → Score → Tier
```

**Key differences from D1:**
- Own 4-layer pipeline (no shared code with D1's engine.py)
- Uses `LTFSignal` objects (not plain dicts) for persistence
- **D2-specific scoring** (100 pts): Entry Precision(20) + LTF Structure(20) + Flow(15) + Momentum(15) + Nascent Move(10) + HTF Context(10) + Timing(5) + Confluence(5)
- Nascent Move Detector: 5-condition breakout identification
- Minimum threshold gates: EP ≥12, Flow ≥8, Momentum ≥8
- Fatal flaw auto-disqualification (delta opposing, far from OB, etc.)
- Scans ALL pairs regardless of D1 tier (D2 is independent)
- 15M timeframe, 5-second cycle

**LTF Pipeline (`ltf_pipeline.py`):**
- Same Flow→CRT→SMC→Signal path as D1
- Weighted SMC-only fallback for impulse coins
- `_check_d2_fatal_flaws()`: 4 auto-disqualification checks

**LTF Scanner (`ltf_scanner.py`):**
- `LTFSignal` class with __slots__, freshness tracking, score history
- `scan_entry()`: wraps pipeline + nascent move + entry precision
- Age-based freshness: HOT/WARM/COOLING/STALE

**LTF Engine (`ltf_engine.py`):**
- Same 3-pass batch pattern as D1
- Revalidates at 8-min checkpoint (half of 15-min TTL)
- Writes D2 tiers to state_store (triggers D3 fusion)

#### D3 Fusion: `backend/engines/signal_fusion.py`

**Purpose:** Reads D1 + D2 from state_store, classifies signal type, packages for frontend.

**`FusionEngine` class:**
- 2-second poll cycle watching `state_store.last_d1_scan` / `last_d2_scan`
- Fuses every D2 signal (independent of D1 tier)
- Writes package to `state_store.d3_decisions`
- Broadcasts to frontend via `ws_hub.broadcast()`

**Signal Type Classification (`classify_signal_type`):**

| Type | Name | Criteria | Action | Position |
|---|---|---|---|---|
| **C** | Full Confluence | D1 SNIPER ≥85 + D2 SNIPER ≥85 + directions align | EXECUTE | 100% |
| **A** | HTF Structure | D1 ≥65 + D2 ≥50 + directions align | EXECUTE | 75% |
| **B** | LTF Momentum | D1 NOT approved + D2 ≥72 + nascent move + EP ≥18 | EXECUTE | 35% |
| **D** | HTF Early Warn | D1 ≥70 + directions don't align | WATCH | 0% |
| **E** | Conflict/Trap | Both valid (≥65) but opposing directions | ALERT | 0% |

**Alignment Score (0-20):**
1. Direction agreement: +5
2. HTF OB alignment (same OB zone): +5
3. Premium/Discount zone alignment: +5
4. Liquidity proximity: +5

**D1 Structural SL Override:**
- If D1 has a closer OB/MSB/FVG level than D2's SL, use D1's level
- TPs and RR auto-scale proportionally
- Capped at 4% of entry

**Expected Value Calculation:**
- Win rate estimated from D2 score (SNIPER=75%, OPPORTUNITY=60%, WATCH=45%)
- EV = (WinRate × AvgWin) - (LossRate × AvgLoss)
- Avg win/loss derived from RR ratio

---

### 4. Market Evolution Engine

**16-State Matrix** — maps (D1_tier, D2_tier) → state:

```
           D2: REJECT    WATCH    OPPORTUNITY    SNIPER
D1 REJECT   Dormant    Awakening   Emerging    LTF Spike
D1 WATCH    Context B   Compression  Expansion   Trap Zone
D1 OPPORT   Pullback    Expansion    Trend Build  Trend Confirm
D1 SNIPER   Deep Pull   Momentum Cool  Inst Flow  Inst Entry
```

**Each state defines:**
- `name`, `description`, `spiral` (Expansion/Correction/Failure/Neutral)
- `confidence` (0-95%), `risk` (Very High → Very Low)
- `tradeStyle`, `action` (trade instruction)
- `trend` (bool), `reversal` (bool)
- `nextProbableState`

**Evolution Velocity** — how the state is changing:
- `++ strong_improving` — jumped 3+ steps
- `+ improving` — moved up 1-2 steps
- `= stable` — same state for 3+ cycles
- `- weakening` — moved down 1-2 steps
- `-- strong_weakening` — dropped 3+ steps

**Institutional Categories:**
| Category | States | Meaning |
|---|---|---|
| **TREND** | Awakening through Institutional Entry | HTF+LTF aligned, trade with trend |
| **RE-ENTRY** | Compression, Pullback, Deep Pullback, Momentum Cooling | Trend intact, wait for discount entry |
| **REVERSAL** | LTF Spike, Trap Zone | Divergence/trap, counter-trend only |
| **DORMANT** | Dormant | No actionable setup |

---

### 5. Pipeline Engines (Shared Library)

These are called by both D1 and D2 pipelines:

#### `backend/engines/crt_engine.py` — Candle Range Theory
- 5-step ICT methodology: Consolidation → Range Candle → Displacement → Fill → Entry
- Max 25 pts (consolidation 8 + range candle 8 + displacement 3 + fill 3 + zone 2)
- Falls back to `_no_signal()` at any failed step

#### `backend/engines/smc_engine.py` — Smart Money Concepts
- MSB (Market Structure Break): CHOCH=5pts, BOS=2pts
- OB (Order Block): 0-5 pts based on retest count
- FVG (Fair Value Gap): 0-5 pts based on proximity
- Liquidity Sweep: 0-5 pts
- Max 20 pts, scaled to 25

#### `backend/engines/signal_builder.py` — Signal Construction
- Combines CRT + SMC + Flow + Momentum into structured signal
- **SL Logic:**
  1. D1 HTF structural level (OB/MSB/FVG) — if tighter
  2. D2 15M structural swing + 0.3× ATR buffer
  3. 1.5× ATR fallback (no structural level found)
  4. Hard cap at 4% of entry
- **TP Logic:**
  1. Nearest opposing FVG zone
  2. 1:1 minimum, extend to 2.5:1 (capped at 4:1)
  3. Minimum 1.5:1 RR (fatal flaw below this)

#### `backend/engines/flow_analyzer.py` — Flow Detection
- VWAP Reclaim: price reclaimed session VWAP
- Sweep + Reversal: ICT Turtle Soup / Liquidity Grab
- RS vs BTC: relative strength
- Killzone Bonus: London/NY open hours

#### `backend/engines/fast_mover.py` — Impulse Detection
- 5 triggers (volume surge, consecutive, range breakout, ATR expansion, sweep+reclaim)
- Any 2+ triggers → FAST_MOVER flag
- Score: +20/+30/+40 based on trigger count

---

### 6. Helper Modules

#### `backend/helpers/candle_math.py`
ATR, ATR%, body ratio, average body, range metrics, retracement %, OTE zone detection, candle field extraction.

#### `backend/helpers/volume_profile.py`
Volume Profile computation — POC, VAH, VAL.

#### `backend/helpers/impulse_context.py`
Synthetic CRT score for impulse coins (no consolidation pattern). SMC-only context builder.

#### `backend/helpers/session.py`
Killzone detection (London/NY/Asian). Session quality scoring. UTC-aware DST handling.

#### `backend/helpers/price_utils.py`
Price formatting, tick size calculation.

#### `backend/vsp_helpers.py`
Swing point detection, FVG detection, VSP (Valid Swing Point) identification.

#### `backend/liquidity_map.py`
Liquidity pool detection from swing points — identifies equal highs/lows, sweep events.

---

### 7. Configuration: `backend/config.py`

All tunable parameters in one place. Key sections:

| Section | Key Parameters |
|---|---|
| Scanner | `SCAN_INTERVAL_SECONDS=15`, `SIGNAL_TTL_MINUTES=15`, `SCAN_CONCURRENCY=20` |
| Candidate Filter | `ADAPTIVE_ATR_MIN_MULTIPLIER=0.60`, `ADAPTIVE_ATR_BASELINE_MIN_PCT=0.03%` |
| CRT | `CONSOLIDATION_MIN_BARS=5`, `RANGE_CANDLE_BODY_MULT=1.8`, `OTE_LOW=50`, `OTE_HIGH=62` |
| SMC | `SWING_LOOKBACK=3`, `OB_TOUCH_PENALTY=[0,3,7,10]`, `FVG_LOOKBACK=20` |
| Scoring | `TIER_SNIPER=85`, `TIER_OPPORTUNITY=65`, `TIER_WATCH=40` |
| D1 Scoring | CRT(20) + SMC(25) + Flow(15) + Momentum(15) + Timing(10) + R/R(10) + Confluence(5) = 100 |
| D2 Scoring | EP(20) + Structure(20) + Flow(15) + Momentum(15) + Nascent(10) + HTF(10) + Timing(5) + Confluence(5) = 100 |
| SL | `SL_ATR_FALLBACK_MULT=1.5`, `SL_MAX_STRUCTURAL_DISTANCE_PCT=4.0` |
| Position Sizing | `POSITION_BASE_PCT=1.0%`, `POSITION_HARD_CAP=3.0%` |
| D2 Specific | `D2_SCAN_INTERVAL=5s`, `D2_SIGNAL_TTL=15min`, `D2_MIN_EP=12`, `D2_MIN_FLOW=8`, `D2_MIN_MOM=8` |
| Killzones | London 08:00-11:00 UTC, NY 13:30-16:30 UTC |
| Timeframes | D1: 1H/4H/1D, D2: 15M |

---

### 8. Frontend

#### `frontend/index.html`
Single-page app with:
- Header: logo, scan status indicator, WS status, D1/D2/D3 stats, restart button
- Filter bar: Direction chips (Long/Short/All)
- Type E Alert bar: conflict warnings
- Activity bar: D1/D2/D3 live status
- Signals container: expandable cards with empty state

#### `frontend/app.js`
| Function | Purpose |
|---|---|
| `buildCard(s)` | Builds HTML for one signal card (coin links, ME state, scores, trade data, sparklines) |
| `renderSignals()` | Renders all cards, sorted by confidence descending |
| `applyFilters()` | Filters by direction only |
| `initFilters()` | Direction chip click handlers |
| `toggleExpand(id)` | Expand/collapse card body |
| `drawSparklines()` | Canvas sparklines for score history |
| `getMEE(s)` | Parse marketEvolution from signal data |
| `initWebSocket()` | Connect to `/ws-fusion`, handle INITIAL + signal updates |
| `confLevel(n)` | Maps confidence % to color tier |

#### `frontend/style.css`
- Dark theme (`#0c1220` base)
- Mobile-first responsive design
- CSS variables for colors/spacing
- Card expand animation
- Activity bar, filter chips, sparklines

---

## Signal Lifecycle

```
1. CANDIDATE FILTER
   └─ Adaptive ATR gate → ~20% pass

2. D1 SCAN (1H/4H/1D)
   ├─ Flow gate (volume/sweep/VWAP/RS)
   ├─ CRT (5-step pattern detection)
   ├─ SMC (MSB/OB/FVG/Liquidity)
   ├─ Signal Builder (entry/SL/TP/RR)
   └─ 100-pt scoring → Tier (SNIPER/OPP/WATCH/REJECTED)
   → Written to signal_store + state_store.d1_tiers

3. D2 SCAN (15M) — independent
   ├─ Same Flow→CRT→SMC pipeline
   ├─ Nascent Move Detection (5 conditions)
   ├─ Entry Precision scoring
   ├─ Fatal Flaw checks
   ├─ 100-pt scoring → Tier
   ├─ SL: D1 structural → D2 structural + 0.3×ATR → 1.5×ATR fallback
   └─ Written to state_store.d2_signals as LTFSignal

4. D3 FUSION (polls every 2s)
   ├─ Reads D1 tier + D2 signal from state_store
   ├─ classify_signal_type() → A/B/C/D/E
   ├─ _compute_alignment() → 0-20 score
   ├─ _d1_structural_sl() → tighter SL if available
   ├─ Expected Value calculation
   ├─ market_evolution.evaluate() → 16-state matrix
   └─ Package → state_store.d3_decisions → broadcast → frontend

5. FRONTEND DISPLAY
   ├─ Receives via WebSocket
   ├─ Sorts by confidence descending
   ├─ Renders expandable cards
   ├─ TradingView + Binance links
   └─ Type E alerts for conflicts

6. LIFECYCLE MANAGEMENT
   ├─ D1: 15s refresh → 15min revalidation → 30min revalidation → TTL expiry
   ├─ D2: 5s refresh → 8min revalidation → 15min expiry
   ├─ Freshness: HOT → WARM → COOL → COLD → DEAD
   └─ State transitions tracked in Market Evolution history
```

---

## Scoring Architecture

### D1 100-Point Scoring

| Component | Max | What It Measures |
|---|---|---|
| CRT | 20 | Is the entry timed right? (Consolidation + Range Candle + Displacement + Fill + Zone) |
| SMC | 25 | Is smart money structure present? (MSB + OB + FVG + Liquidity) |
| Flow | 15 | Is real money moving RIGHT NOW? (VWAP reclaim, sweep, RS vs BTC) |
| Momentum | 15 | Is price about to explode? (Volume surge, consecutive, ATR expansion) |
| Timing | 10 | Is this the right session? (London/NY killzone, session quality) |
| R/R | 10 | Is the reward worth the risk? (RR ratio + SL quality) |
| Confluence | 5 | Do multiple factors agree? (CRT+SMC+Flow+Momentum+Timing+R/R thresholds) |

### D2 100-Point Scoring

| Component | Max | What It Measures |
|---|---|---|
| Entry Precision | 20 | Is entry at the exact right price? (OB retest + FVG fill + Wick rejection) |
| LTF Structure | 20 | Smart money structure on 15M |
| Flow | 15 | Real-time flow on 15M |
| Momentum | 15 | Price ignition on 15M |
| Nascent Move | 10 | LTF-first breakout detection (5 conditions) |
| HTF Context | 10 | Does D2 agree with D1 direction? |
| Timing | 5 | Session quality on 15M |
| Confluence | 5 | Multi-factor agreement |

### Tier Thresholds (Both D1 and D2)

| Tier | Score | Meaning |
|---|---|---|
| SNIPER | ≥ 85 | Highest probability, lowest risk |
| OPPORTUNITY | ≥ 65 | Strong setup, good RR |
| WATCH | ≥ 40 | Partial confirmation, observe |
| REJECTED | < 40 | No valid setup |

---

## Key Design Patterns

### Singleton Pattern
`MarketData`, `StateStore`, `SignalStore`, `FusionEngine` all use lazy-init singletons. No DI container — direct `import` access.

### Loose Coupling via State Store
D1, D2, D3 never call each other directly. All communication flows through `state_store` (D1 writes tiers, D2 reads them + writes signals, D3 reads both).

### Pipeline Pattern
Both D1 and D2 use the same library of pipeline layers (Flow → CRT → SMC → Signal Builder), but with different scoring weights and parameters.

### Graceful Degradation
- CRT fails → SMC-only fallback with weighted confidence
- No D1 data → defaults to REJECTED (D2 still scans)
- No structural SL → ATR fallback
- No WebSocket clients → no crash (silent skip)

### Fatal Flaw Auto-Disqualification
Both D1 and D2 have `_check_fatal_flaws()` that instantly kill signals with:
- RR < 1.5:1
- No structural SL
- Delta opposing on impulse candle
- MSB direction contradicts signal direction

---

## File Inventory

### Backend Core
| File | Lines | Purpose |
|---|---|---|
| `backend/main.py` | 318 | FastAPI entry, routes, bootstrap |
| `backend/config.py` | 240 | All tunable parameters |
| `backend/market_data.py` | ~300 | REST + WS data, candle cache |
| `backend/scanner.py` | 428 | D1 orchestrator, 3-pass batch |
| `backend/state_store.py` | 197 | Shared state bus (singleton) |
| `backend/signal_store.py` | 217 | D1 signal persistence, freshness, revalidation |
| `backend/ws_hub.py` | 39 | WebSocket broadcast hub |

### Pipeline Engines (Shared)
| File | Lines | Purpose |
|---|---|---|
| `backend/engines/engine.py` | 410 | D1 pipeline: Flow→CRT→SMC→Score |
| `backend/engines/crt_engine.py` | ~400 | Candle Range Theory (5-step) |
| `backend/engines/smc_engine.py` | ~200 | Smart Money Concepts (MSB/OB/FVG/Liq) |
| `backend/engines/signal_builder.py` | ~400 | Entry/SL/TP/RR construction |
| `backend/engines/flow_analyzer.py` | ~400 | VWAP, sweep, RS, killzone |
| `backend/engines/fast_mover.py` | ~150 | Volume surge, consecutive, ATR expansion |

### D2 Specific
| File | Lines | Purpose |
|---|---|---|
| `backend/engines/ltf_engine.py` | 255 | D2 orchestrator, batch scan |
| `backend/engines/ltf_pipeline.py` | 554 | D2's own 4-layer pipeline |
| `backend/engines/ltf_scanner.py` | 367 | LTFSignal class, nascent move, entry precision |

### D3 / Fusion
| File | Lines | Purpose |
|---|---|---|
| `backend/engines/signal_fusion.py` | 621 | Decision Layer: classify, align, SL override, package |

### Market Evolution
| File | Lines | Purpose |
|---|---|---|
| `backend/market_evolution/__init__.py` | — | Package init |
| `backend/market_evolution/models.py` | 105 | FusionContext, Transition, MarketEvolutionState dataclasses |
| `backend/market_evolution/constants.py` | 338 | 16-state matrix, SPIRALS, INSTITUTIONAL_CATEGORIES, TRADING_DECISIONS |
| `backend/market_evolution/engine.py` | 190 | ME entry point: evaluate(), get_dashboard_stats() |
| `backend/market_evolution/mapper.py` | 100 | Tier→state mapping, evolution_velocity |
| `backend/market_evolution/transitions.py` | 76 | State transition computation, momentum velocity |
| `backend/market_evolution/confidence.py` | — | Blended confidence calculation |
| `backend/market_evolution/recommendations.py` | 18 | Trade recommendation lookup |
| `backend/market_evolution/history.py` | ~80 | Per-coin transition history |

### Helpers
| File | Lines | Purpose |
|---|---|---|
| `backend/helpers/candle_math.py` | — | ATR, body ratio, range metrics, OTE |
| `backend/helpers/volume_profile.py` | — | Volume Profile (POC, VAH, VAL) |
| `backend/helpers/impulse_context.py` | — | Synthetic CRT for impulse coins |
| `backend/helpers/session.py` | — | Killzone detection, session scoring |
| `backend/helpers/price_utils.py` | — | Price formatting, tick size |
| `backend/vsp_helpers.py` | — | Swing points, FVG, VSP detection |
| `backend/liquidity_map.py` | — | Liquidity pool detection |
| `backend/candidate_selector.py` | — | Adaptive ATR pre-filter |
| `backend/signal_logger.py` | — | CSV signal logging |
| `backend/performance_tracker.py` | — | Performance tracking |
| `backend/regime_engine.py` | — | Market regime detection |
| `backend/correlation_filter.py` | — | Correlation filter |
| `backend/schemas.py` | — | Candle dataclass |
| `backend/volume_profile.py` | — | Volume profile (legacy?) |

### Frontend
| File | Lines | Purpose |
|---|---|---|
| `frontend/index.html` | 144 | PWA shell, header, filters, card container |
| `frontend/app.js` | ~450 | Card builder, rendering, filters, sparklines, WebSocket |
| `frontend/style.css` | ~460 | Dark theme, responsive, card styles |
| `frontend/manifest.json` | — | PWA manifest |
| `frontend/service-worker.js` | — | Service worker |
| `frontend/sw.js` | — | Cache-busted SW |

---

## Current Issues & Technical Debt

### Inconsistencies
1. **Duplicate `detect_nascent_move()`** — exists in both `ltf_scanner.py:150` and `ltf_pipeline.py:345` with slightly different logic (4 conditions vs 5)
2. **Duplicate `calculate_entry_precision()`** — exists in both `ltf_scanner.py:239` and `ltf_pipeline.py:495`
3. **Duplicate decay constants** — `DECAY_TYPE_A/C` defined in both `config.py:109-113` and `config.py:210-211`
4. **Duplicate state names in STATE_POSITION_MULT** — `config.py:123-140` has states like "Consolidation", "Coiling", "Acceleration" that don't exist in the 16-state matrix
5. **`get_active_coins()`** called in `ltf_scanner.py:141` but not defined on state_store — this would crash if reached
6. **`scan_entry()`** in `ltf_scanner.py` calls `scan_ltf_pipeline()` which also calls `build_signal()` — `build_signal()` is called twice (once in pipeline, once in scanner)

### Frontend Issues
1. **`stat-accent` CSS class** referenced in HTML but never defined in CSS
2. **`spawn_task` / `mcp__ccd_session__spawn_task`** references in comments — not relevant
3. Signal card template has **dead `coin-name` span** replaced with links but old CSS class `.coin-name` was removed

### Architecture Concerns
1. **No separation between D1 and D2 scanner infrastructure** — both use the same pipeline engines but with different scoring weights baked into each caller
2. **`signal_fusion.py` is 621 lines** doing classification, alignment, SL override, EV, packaging, and broadcasting — should be split
3. **No error handling for WebSocket broadcast failures** — dead clients accumulate until they throw
4. **`ws_hub.py` has no reconnect logic** — if all clients disconnect, new connections get no INITIAL payload
5. **No rate limiting on `/api/logs`** — could be abused

---

## How to Run

```bash
# Install dependencies
pip install fastapi uvicorn aiohttp websockets

# Start the scanner
cd "C:\Users\josh-\Desktop\Judah Scanner"
python backend/main.py

# Access dashboard
# http://localhost:8000

# API endpoints
# GET /api/signals    — D1 signals
# GET /api/fusion     — D3 decisions (what frontend shows)
# GET /api/stats      — Scanner stats
# GET /api/logs       — Recent logs
# GET /api/debug-fusion — D1/D2/Decision diagnostic
# POST /api/restart   — Restart scanner
```

---

## Signal Card Fields Explained

When you see a card on the frontend, here's what each field means:

| Field | Source | Meaning |
|---|---|---|
| **Evolution** | ME Engine | ++ Improving / + Improving / = Stable / - Weakening / -- Strong Weakening |
| **Spiral** | ME Engine | Expansion (trend) / Correction (pullback) / Failure (trap) / Neutral |
| **Decision** | ME Engine | Trade With Trend / Prepare Pullback / Wait For Confirmation / Avoid / No Edge |
| **Confidence** | ME Engine | 0-100% blended confidence (matrix + scores + alignment) |
| **D1 score / tier** | D1 Scanner | Best HTF score across 1H/4H/1D |
| **D2 score / tier** | D2 Engine | 15M entry timing score |
| **OB Bullish Discount** | D1 SMC | Price is in a bullish order block (discount zone) |
| **POC range** | D1 Volume Profile | High-volume trading range |
| **1H / 1D scores** | D1 timeframes | Per-timeframe breakdown |
| **Alignment** | D3 Fusion | 0-20 score: direction, OB, zone, liquidity agreement |
| **DISP / NAS** | D2 Pipeline | Displacement strength + Nascent move conditions met |
| **EP** | D2 Pipeline | Entry Precision score (0-25) |
| **Entry** | D2 Signal Builder | Suggested entry price |
| **SL** | D2 Signal Builder | Stop loss (D1 structural → D2 structural + 0.3×ATR → 1.5×ATR) |
| **TP1 / TP2** | D2 Signal Builder | Take profit targets (1:1 and 2.5:1 by default) |
| **RR1 / RR2** | D2 Signal Builder | Risk:Reward ratios |
| **EV** | D3 Fusion | Expected value per trade (positive = profitable on average) |
| **WR** | D3 Fusion | Estimated win rate |
| **Signal Type** | D3 Fusion | A/B/C/D/E classification |
| **Action** | D3 Fusion | EXECUTE / WATCH / ALERT / No Edge |

### How to Take a Trade

1. **Filter for Expansion spiral** + confidence ≥ 60% + EV > 0 + WR ≥ 55%
2. **Check alignment** ≥ 12/20 (D1 and D2 must agree on direction)
3. **Check signal type** — only trade Type A, B, or C (D/E are watch/avoid)
4. **Check Decision** — only enter on "Trade With Trend" or "Prepare Pullback Entry"
5. **Entry** — for "Prepare Pullback Entry", wait for price to reach the OB zone. For "Trade With Trend", enter on current signal.
6. **SL** — use the SL shown (comes from D1 structural if tighter, otherwise D2 structural)
7. **TP1** — close 50% at 1:1 RR, move SL to breakeven
8. **TP2** — close remaining at 2.5:1 RR
9. **Never** trade Type E (conflict) or D (early warning)
