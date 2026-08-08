# System Architecture

Deep-dive into Judah Scanner's codebase structure, data flow, and component interactions.

## Directory Structure

```
Judah Scanner/
├── backend/
│   ├── main.py                    # FastAPI entry point — REST + WebSocket
│   │                               # Endpoints: /, /api/*, /ws, /ws-fusion
│   ├── config.py                  # All tunable parameters
│   │                               # Scores, thresholds, sessions, timing
│   ├── scanner.py                 # D1 HTF Scanner
│   │                               # Event-driven: scans on candle change
│   │                               # WebSocket push via /ws
│   ├── market_data.py             # Binance WebSocket + candle management
│   │                               # Maintains rolling candle buffer
│   │                               # Triggers scan callbacks on close
│   ├── signal_store.py            # D1 signal store (in-memory dict)
│   │                               # Keyed by coin+timeframe
│   │                               # Composite scores, SMC structures, freshness
│   ├── state_store.py             # D2 + D3 state store
│   │                               # D2 signals: keyed by coin
│   │                               # D3 decisions: keyed by coin
│   │                               # D1 tiers: aggregated HTF state
│   ├── ws_hub.py                  # WebSocket connection manager
│   │                               # Broadcast to all connected clients
│   │                               # Initial payload on connect
│   ├── performance_tracker.py     # Win/loss tracking by scenario
│   ├── signal_logger.py           # CSV-based signal history
│   ├── engines/
│   │   ├── crt_engine.py          # Candle Range Theory
│   │   │                           # Range detection, displacement, OTE
│   │   │                           # Premium/discount, session scoring
│   │   │                           # Rate of Change (ROC)
│   │   ├── smc_engine.py          # Smart Money Concepts
│   │   │                           # Swing points (HH/HL/LH/LL)
│   │   │                           # Order blocks, FVGs, MSB
│   │   │                           # Liquidity sweeps, volume profile
│   │   ├── signal_builder.py      # Combines CRT+SMC
│   │   │                           # Produces Entry/SL/TP/RR
│   │   │                           # Applies fatal flaw filters
│   │   │                           # FVG ledger, freshness tracking
│   │   ├── ltf_engine.py          # D2 15M LTF Scanner
│   │   │                           # Background task
│   │   │                           # Independent of D1
│   │   │                           # Runs every SCAN_INTERVAL_SECONDS
│   │   ├── ltf_scanner.py         # LTF scan logic
│   │   │                           # Fetches 15M candles
│   │   │                           # Builds signals per coin
│   │   ├── ltf_pipeline.py        # LTF scanning pipeline
│   │   │                           # Manages scan queue, rate limiting
│   │   ├── signal_fusion.py       # D3 Decision Layer
│   │   │                           # Watches D1/D2 state changes
│   │   │                           # Classifies A/B/C/D/E
│   │   │                           # Calculates EV, positions sizing
│   │   │                           # Pushes via /ws-fusion
│   │   ├── correlation_filter.py  # Correlation filtering
│   │   │                           # Reduces correlated pairs
│   │   └── regime_engine.py       # Market regime detection
│   ├── market_evolution/
│   │   ├── mapper.py              # 16-state evolution matrix
│   │   │                           # State transitions
│   │   │                           # Evolution velocity tracking
│   │   ├── history.py             # State history per coin
│   │   │                           # Transition tracking
│   │   ├── __init__.py            # evaluate() + get_dashboard_stats()
│   │   └── states.py              # State definitions
│   ├── helpers/
│   │   ├── candle_math.py         # Candle calculations (ATR, body%, wick%)
│   │   ├── session.py             # Session detection (Asia/London/NY)
│   │   └── impulse_context.py     # Impulse/retracement analysis
│   └── state_store.py             # Central state management
├── frontend/
│   ├── index.html                 # Dashboard HTML
│   │                               # Signal cards, filters, activity bar
│   ├── app.js                     # Frontend JavaScript
│   │                               # WebSocket handlers
│   │                               # Card rendering, sparklines, filters
│   │                               # Type E alert system
│   └── style.css                  # Dark theme CSS
│                               # Mobile-first, premium feel
├── tests/
│   └── test_phase4_integration.py # 36 integration tests
│                               # Signal types, tiers, EV, packaging
├── config.yaml                    # Runtime configuration
├── requirements.txt               # Python dependencies
├── README.md                      # Project overview
├── SCORING.md                     # Scoring system reference
├── PLAYBOOK.md                    # Trading playbook
├── ARCHITECTURE.md                # This file
└── DEPLOY.md                      # Deployment guide
```

## Data Flow

### D1 (HTF) Flow

```
Binance WebSocket → market_data (candle buffer)
                    → on candle close → scanner.scan_pair()
                    → smc_engine + crt_engine + signal_builder
                    → signal_store.set(coin, tf, signal)
                    → WebSocket /ws push to frontend
                    → state_store.set_d1_tier(coin, tier_data)
                    → triggers D3 fusion check
```

### D2 (LTF) Flow

```
Background task (every 120s) → ltf_engine.scan_cycle()
                              → ltf_pipeline.queue all 529 pairs
                              → ltf_scanner per pair:
                                  → fetch 15M candles
                                  → smc_engine + crt_engine + signal_builder
                                  → fatal flaw filters
                                  → state_store.set_d2_signal(coin, signal)
                              → triggers D3 fusion check
```

### D3 (Fusion) Flow

```
State change detected (D1 or D2 updated)
  → FusionEngine._check_and_fuse()
  → For each D2 signal:
      → get D1 tier for coin
      → classify_signal_type() → A/B/C/D/E
      → calculate EV
      → build package (D1 structure + D2 structure + ME state)
      → state_store.set_d3_decision(coin, package)
      → WebSocket /ws-fusion broadcast
      → Frontend renders signal card
```

## Component Dependencies

```
main.py
  ├── config.py (HOST, PORT, TIMEFRAMES_HTF, thresholds)
  ├── market_data.py (ws_connected, candle buffer)
  ├── scanner.py (D1 scan loop, on_new_signals callback)
  ├── signal_store.py (D1 signals in-memory)
  ├── state_store.py (D2 signals + D3 decisions)
  ├── ws_hub.py (WebSocket broadcast)
  ├── performance_tracker.py (stats)
  ├── engines/
  │   ├── signal_fusion.py (D3: classification, EV, packaging)
  │   │   ├── state_store
  │   │   ├── config (thresholds, TYPE_B params)
  │   │   ├── ws_hub (broadcast)
  │   │   ├── market_evolution (evaluate, get_dashboard_stats)
  │   │   └── signal_store (D1 best signal lookup)
  │   ├── ltf_engine.py (D2: background scanner)
  │   │   ├── market_data (fetch candles)
  │   │   ├── state_store (store D2 results)
  │   │   ├── smc_engine
  │   │   ├── crt_engine
  │   │   └── signal_builder
  │   └── smc_engine.py (shared by D1 and D2)
  │       ├── config (SMC parameters)
  │       └── candle_math (ATR, body calculations)
  └── market_evolution/
      ├── mapper.py (16-state matrix)
      └── history.py (state history)
```

## State Management

### signal_store (D1)
```python
signal_store.get(coin, timeframe) → Signal dict or None
signal_store.get_all() → List[Signal]
signal_store.set(coin, timeframe, signal) → None
```

### state_store (D2 + D3 + D1 aggregation)
```python
state_store.get_d1_tier(coin) → D1 tier dict (tier, score, direction, timeframes)
state_store.set_d1_tier(coin, data) → None
state_store.get_d2_signal(coin) → D2Signal object or None
state_store.set_d2_signal(coin, signal) → None
state_store.get_all_d2_signals() → Dict[coin, D2Signal]
state_store.get_all_decisions() → Dict[coin, D3Decision]
state_store.set_d3_decision(coin, pkg) → None
state_store.get_stats() → Stats dict
```

## WebSocket Protocol

### /ws (D1 signals)
```json
{"type": "NEW_SIGNALS", "signals": [...], "timestamp": 1234567890}
{"type": "REFRESH", "signals": [...]}
{"type": "REVALIDATED", "signals": [...]}
```

### /ws-fusion (D3 decisions)
```json
// Initial payload
{"type": "INITIAL", "signals": [...], "stats": {...}}

// New signal
{"type": "signal", "data": {full D3 package}}

// Type E conflict alert
{"type": "TYPE_E_ALERT", "data": {coin, d1_dir, d2_dir, d1_tier, d2_tier, ...}}
```

## Concurrency Model

- **D1 Scanner**: Event-driven via callbacks. Triggered by candle close events from Binance WebSocket.
- **D2 Engine**: Background `asyncio` task, scans all pairs sequentially with rate limiting.
- **D3 Fusion**: Triggered by state changes. Runs synchronously in the scan loop — processes all D2 signals and fuses them.
- **WebSocket**: Uses FastAPI's async WebSocket. Broadcast is fire-and-forget via `asyncio.create_task()`.
- **State stores**: In-memory dicts, protected by asyncio event loop (single-threaded). No explicit locks needed.

## Key Design Decisions

1. **D2 is fully independent**: Scans all 529 pairs regardless of D1 state. Type B signals (D1 not approved) are valid trading opportunities.

2. **D1 defaults to REJECTED**: If no D1 data exists for a coin, D2 signals still classify as Type B (if gates pass).

3. **No sensitivity modes**: Fixed thresholds for all scoring. Avoids complexity and parameter drift.

4. **WebSocket for all real-time data**: No polling. Frontend connects once and receives all updates.

5. **Structured scoring logs**: Every fusion decision logs coin, signal type, scores, directions, and gates — enabling post-hoc analysis.
