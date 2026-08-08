# Judah Scanner

**Institutional Market Evolution Terminal — CRT + SMC Signal Scanner**

Identifies high-probability crypto trade setups by fusing Candle Range Theory (timing) with Smart Money Concepts (structure) across three dimensions: HTF context, LTF execution, and institutional decision classification.

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the server
cd backend
python main.py
# Opens at http://localhost:8000
```

## Architecture

### 3 Dimensions

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   D1: HTF   │    │   D2: LTF   │    │   D3: Fuse  │
│  Scanner    │───▶│   Engine    │───▶│  Decision   │
│             │    │  (15M)      │    │   Layer     │
│ 1H/4H/1D    │    │ 529 pairs   │    │ A/B/C/D/E   │
│ SMC+CRT     │    │ SMC+CRT     │    │ + Market    │
│             │    │ Independent │    │ Evolution   │
└─────────────┘    └─────────────┘    └─────────────┘
       │                 │                 │
       ▼                 ▼                 ▼
   WebSocket           Background         WebSocket
      /ws               Task              /ws-fusion
```

- **D1 (HTF Scanner)**: Scans 529 USDT pairs across 1H/4H/1D. Detects institutional structure — swing points, order blocks, FVGs, liquidity sweeps, volume profile. Pushes signals via WebSocket `/ws`.
- **D2 (LTF Engine)**: Runs as a background task on 15M timeframe. Produces execution-level signals with precise Entry/SL/TP. Fully independent of D1 — it scans all 529 pairs regardless of HTF state.
- **D3 (Fusion Engine)**: Watches D1 and D2 state stores. When either updates, classifies each coin into a Signal Type (A/B/C/D/E) and pushes the decision package to the frontend via WebSocket `/ws-fusion`.

## Signal Types (Decision Layer)

| Type | Name | Action | Criteria |
|------|------|--------|---------|
| **A** | HTF Structure | Execute | D1 >= 70 AND D2 >= 50 AND directions align |
| **B** | LTF Momentum | Execute | D1 not approved AND D2 >= 72 AND nascent move AND Entry Precision >= 16 |
| **C** | Full Confluence | Execute | D1 >= 85 AND D2 >= 85 AND directions align |
| **D** | HTF Early Warning | Watch | D1 >= 70 AND D2 not aligned |
| **E** | Conflict/Trap | Alert | Both D1+D2 valid (>= 65) but opposing directions |

### Position Sizing

| Type | Position Mult | Stop Mult | TTL |
|------|:---:|:---:|:---:|
| A | 0.75x | 1.5x | 120 min |
| B | 0.35x | 1.0x | 15 min |
| C | 1.0x | 1.5x | 240 min |
| D | 0.0x | 1.5x | 60 min |
| E | 0.0x | 1.5x | 0 min (alert only) |

## Signal Tiers (D1 + D2 scoring)

| Tier | Score | Meaning |
|------|:---:|---------|
| SNIPER | >= 85 | Highest probability — full institutional alignment |
| OPPORTUNITY | >= 65 | Strong setup — partial confluence |
| WATCH | >= 40 | Weak signal — monitor only |
| REJECTED | < 40 | No actionable setup |

## Market Evolution Engine (16-State Matrix)

Classifies each signal into one of 4 market types:

| Market Type | Meaning | Color |
|-------------|---------|-------|
| **TREND** | Institutional expansion — trade with the trend | Green |
| **RE_ENTRY** | Pullback in an existing trend — wait for confirmation | Amber |
| **REVERSAL** | Trend failure / potential reversal — high risk | Red |
| **DORMANT** | No institutional context | Gray |

Each state includes: `previousState`, `state`, `nextProbableState`, `evolutionVelocity` (improving/stable/degrading), `institutionalCategory`, `tradingDecision`, and `evolutionConfidence`.

## REST API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard HTML |
| GET | `/api/signals` | D1 HTF signals |
| GET | `/api/fusion` | D3 fusion decisions |
| GET | `/api/pairs` | Scanned pairs + timeframes |
| GET | `/api/stats` | Performance statistics |
| GET | `/api/logs` | Recent signal logs |
| GET | `/api/debug-fusion` | Fusion diagnostic data |
| GET | `/api/performance` | Win/loss tracker summary |
| GET | `/api/health` | Health check |
| POST | `/api/restart` | Restart scanner |

All endpoints return structured JSON errors on failure.

## Configuration

All parameters are in `backend/config.py`:

- `TIMEFRAMES_HTF`: HTF timeframes (1H, 4H, 1D)
- `TIER_SNIPER_SCORE`: SNIPER threshold (default 85)
- `TIER_OPPORTUNITY_SCORE`: OPPORTUNITY threshold (default 65)
- `TIER_WATCH_SCORE`: WATCH threshold (default 40)
- `TYPE_B_MIN_D2_SCORE`: Type B minimum D2 score (default 72)
- `TYPE_B_ENTRY_PRECISION_GATE`: Type B Entry Precision minimum (default 16)
- `D2_SIGNAL_TTL_MINUTES`: D2 signal TTL (default 30)
- `SCAN_INTERVAL_SECONDS`: Scan loop interval (default 120)
- Session definitions, CRT thresholds, SMC parameters, etc.

## File Structure

```
backend/
  main.py              # FastAPI entry point — REST + WebSocket
  config.py            # All tunable parameters
  scanner.py           # D1 HTF scanner (WebSocket /ws)
  market_data.py       # Binance WebSocket + candle management
  signal_store.py      # D1 signal store (in-memory)
  state_store.py       # D2 + D3 state store
  ws_hub.py            # WebSocket connection hub
  engines/
    signal_fusion.py   # D3 decision layer (A/B/C/D/E)
    ltf_engine.py      # D2 15M LTF scanner
    smc_engine.py      # Smart Money Concepts engine
    crt_engine.py      # Candle Range Theory engine
  market_evolution/
    mapper.py          # 16-state market evolution matrix
    history.py         # State history tracking
frontend/
  index.html           # Dashboard UI
  app.js               # Frontend logic + rendering
  style.css            # Dark theme styles
tests/
  test_phase4_integration.py  # 36 integration tests
```

## Development

```bash
# Run tests
python -m pytest tests/ -v
```
