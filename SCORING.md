# Scoring System

Complete reference for Judah Scanner's scoring methodology, from HTF detection through the final Decision Layer.

## D1 Score (HTF — 1H/4H/1D)

Each timeframe is scored independently using CRT + SMC factors. The composite D1 score is a weighted average of the active timeframes.

### Score Factors

| Factor | Points | Description |
|--------|:---:|-------------|
| Range Established | +10 | Price outside valid CR range |
| Range Broken | +15 | Close outside range + displacement |
| OTE Retracement | +10-25 | Price at Fib retracement of displacement |
| Premium/Discount Zone | +10 | Price in 25th/75th percentile |
| MSB (Market Structure Break) | +15 | Bullish/Bearish MSB confirmed |
| Order Block | +5-15 | Untested OB in direction |
| FVG (Fair Value Gap) | +5-15 | Unfilled FVG in direction |
| Liquidity Sweep | +10 | Recent sweep of equal highs/lows |
| Session Score | +5-15 | Favorable session (London/NY killzone) |
| Volume Profile | +5-10 | POC alignment + value area support |

### D1 Tiers

```
Score >= 85  → SNIPER
Score >= 65  → OPPORTUNITY
Score >= 40  → WATCH
Score < 40   → REJECTED
```

### Freshness Decay

Scores decay as the signal ages:
- Each 5-minute interval: score × 0.985
- Decay resets when a new candle opens at a higher timeframe
- Signals older than 60 minutes without refresh are marked COOLING/STALE

## D2 Score (LTF — 15M)

D2 scores execution-level quality independently. It does NOT inherit D1 scores.

### Score Factors

| Factor | Points | Description |
|--------|:---:|-------------|
| D2 Scenario | +10-25 | G2/G3/BOS + POI alignment |
| MSB on 15M | +10-15 | Confirmed structure break |
| Entry Precision | +5-20 | Distance to key level (lower = better) |
| OB Mitigation | +10-15 | Price at order block |
| FVG Fill | +5-15 | Price filling gap |
| Liquidity Target | +10 | Valid take-profit target |
| Displacement | +5-10 | Strong prior candle move |
| Premium/Discount | +5 | Price in favorable CRT zone |
| Session | +5-10 | Killzone bonus |

### D2 Tiers

```
Score >= 85  → SNIPER
Score >= 65  → OPPORTUNITY
Score >= 40  → WATCH
Score < 40   → REJECTED
```

## Decision Layer: Signal Types

The Decision Layer fuses D1 + D2 scores and directions to produce actionable signal types.

### Classification Logic

```
1. Type C (Full Confluence):
   D1 SNIPER (>= 85) + D2 SNIPER (>= 85) + directions align
   → Highest conviction. Full size, wide stop.

2. Type A (HTF Structure):
   D1 approved (SNIPER or OPPORTUNITY, >= 65) + D2 >= 50 + directions align
   → Strong HTF backing with LTF confirmation.

3. Type B (LTF Momentum):
   D1 NOT approved (REJECTED or WATCH) + D2 >= 72 + nascent move + Entry Precision >= 16
   → Independent LTF play. Reduced size, tight stop. D2 is fully independent.

4. Type E (Conflict/Trap):
   Both D1 and D2 valid (>= 65) but OPPOSING directions
   → Warning: institutional disagreement. Do not trade. Alert only.

5. Type D (HTF Early Warning):
   D1 approved (>= 65) + D2 NOT aligned (but not REJECTED)
   → HTF says go but LTF disagrees. Watch only.

6. None:
   Everything below thresholds.
```

### Position Sizing

| Type | Position Mult | Stop Mult | Rationale |
|------|:---:|:---:|----------|
| C | 1.0x | 1.5x | Highest conviction, full allocation |
| A | 0.75x | 1.5x | Strong HTF backing |
| B | 0.35x | 1.0x | Independent LTF play, tight risk |
| D | 0.0x | 1.5x | HTF-LTF conflict, no entry |
| E | 0.0x | 1.5x | Directional conflict, alert only |

## Expected Value (EV)

Each signal carries an EV estimate:

```
EV = (Win_Rate × Avg_Win) - (Loss_Rate × Avg_Loss)
```

Win rate is estimated from the D2 score:
- SNIPER (85+): 75%
- OPPORTUNITY (65+): 60%
- WATCH (40+): 45%
- Below: 35%

Avg win/loss derived from Risk:Reward ratio with 1% risk per trade.

## Market Evolution Confidence

The 16-state matrix produces an `evolutionConfidence` score (0-100%) based on:
- Stability of current state (how long in same state)
- Directional agreement across timeframes
- Volume confirmation
- Prior state transitions

| Confidence | Meaning |
|:---:|----------|
| >= 85% | Very High — strong institutional consensus |
| >= 70% | High — reliable signal |
| >= 50% | Medium — moderate confidence |
| < 50% | Low — conflicting data |
