# Judah Scanner

**CRT + SMC Institutional Crypto Signal Scanner**

Identifies high-probability trade setups using Candle Range Theory (timing) and Smart Money Concepts (structure) — the same hybrid methodology used by proprietary trading firms.

## Signal Tiers

| Tier | Score | Min RR | Meaning |
|------|-------|--------|---------|
| SNIPER | >= 70 | 1.5:1 | Highest probability setups |
| OPPORTUNITY | >= 55 | 1.5:1 | Strong setups |
| WATCH | >= 40 | 1.5:1 | Partial confirmation, monitor |

## Quick Start

1. Run `scripts/install.bat` (first time only — creates venv + installs deps)
2. Run `scripts/start.bat` — opens dashboard at http://localhost:8000
3. For public access: Run `scripts/start-ngrok.bat`

## Architecture

- Event-driven: scans only when candles change (via Binance WebSocket)
- 529 USDT pairs x 3 timeframes (1H, 4H, 1D)
- CRT Engine: range detection, displacement, OTE retracement, session scoring
- SMC Engine: swing points, VSP, order blocks, MSB, FVG, liquidity sweeps
- Signal Builder: combines CRT+SMC, calculates Entry/SL/TP/RR
- Freshness tracking: live score degradation as price drifts from entry
- Multi-TF confluence: +10 boost when same signal fires on 2+ timeframes
- FVG ledger: persistent tracking of fair value gaps per coin+TF
- Volume profile: POC and value area computation
- Performance tracking: win/loss stats by tier, session, timeframe

## Customization

Edit `backend/config.py` to tune all parameters — scores, thresholds, sessions, timing.
