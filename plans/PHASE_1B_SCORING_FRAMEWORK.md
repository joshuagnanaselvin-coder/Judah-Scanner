# PHASE 1B: SCORING FRAMEWORK DOCUMENT
## Judah Scanner — 100-Point Rubrics, Signal Taxonomy, Decision Matrix, 16-State Mapping

---

## TABLE OF CONTENTS

1. [Institutional Foundation](#1-institutional-foundation)
2. [D1 Scoring Rubric — 100 Points (HTF: 1H/4H/1D)](#2-d1-scoring-rubric--100-points)
3. [D2 Scoring Rubric — 100 Points (LTF: 15M, ALL 529 pairs)](#3-d2-scoring-rubric--100-points)
4. [Nascent Move Detector — Type B Detection](#4-nascent-move-detector--type-b-detection)
5. [Signal Taxonomy (A/B/C/D/E)](#5-signal-taxonomy)
6. [Decision Matrix](#6-decision-matrix)
7. [16-State Market Evolution Matrix Mapping](#7-16-state-market-evolution-matrix-mapping)
8. [HTF-LTF Timing Asymmetry: The Critical Problem](#8-htf-ltf-timing-asymmetry)
9. [Position Sizing Engine](#9-position-sizing-engine)
10. [Complete Scoring Pipeline](#10-complete-scoring-pipeline)

---

## 1. INSTITUTIONAL FOUNDATION

### 1.1 How Institutions Grade Trade Setups

Institutional trading desks (prop firms, hedge funds, market-making desks) evaluate trade setups through a **confluence-based scoring system** rather than single-indicator triggers. The core principle: no single factor is reliable enough. Setups must pass multiple independent tests of validity.

**Key institutional scoring components and typical weightings:**

| Component | Typical Weight | Rationale |
|---|---|---|
| **Market Structure** (higher TF alignment) | 25-30% | The "north star" — higher timeframes define the real trend/range. Institutions anchor to HTF structure because it reflects capital committed at larger scales. |
| **Liquidity Alignment** (OB, FVG, liquidity pools) | 20-25% | Institutions need price to reach their entry with minimal slippage. Liquidity pools (stop clusters) provide the "magnet" that draws price to their zone. |
| **Momentum Confirmation** (displacement, volume, delta) | 15-20% | Confirms the move is institutionally-driven, not retail noise. Climactic vs. building volume distinction is critical. |
| **Risk/Reward Quality** | 15-20% | Institutional minimum R:R is typically 2:1, with 3:1 preferred. Structural stops (not arbitrary ATR multiples) define the risk leg. |
| **Timing/Session** | 10-15% | Killzones matter. Institutions trade highest-liquidity sessions. Setup quality during low-liquidity sessions (Asian, weekend) is discounted. |
| **Effort vs. Result (Flow)** | 10-15% | Volume without price movement = absorption (institutional accumulation/distribution). Volume WITH movement = genuine displacement. |

**Institutional probability tiers:**
- **A-tier (High Conviction)**: Score > 80, 5+ confluence factors aligned, minimum 2.5:1 R:R, HTF + LTF agreement → expected win rate 65-75%
- **B-tier (Medium Conviction)**: Score 60-80, 3-4 confluence factors, minimum 2:1 R:R → expected win rate 50-65%
- **C-tier (Low Conviction / Speculative)**: Score 40-60, 2-3 confluence factors, 1.5:1 R:R minimum → expected win rate 35-50%
- **Disqualified**: < 40 score OR any fatal flaw (see §2.5 below) → not traded

### 1.2 How Market Makers Engineer Liquidity

**Liquidity Pool Creation:**
1. Market makers push price slightly beyond a visible technical level (recent high/low, round number, cluster of stops)
2. This triggers retail stop-losses and breakout entries
3. After the liquidity is taken (stop hunt), price reverses sharply into the opposite direction
4. The "wicks" left behind are the evidence — the point where liquidity was extracted

**Institutional Detection:**
- A sweep followed by an immediate and strong close back inside the range = classic liquidity grab
- The larger the sweep relative to the candle body, the more liquidity was captured
- Sweeps that DON'T reverse within 3-5 candles are genuine breaks, not traps

**"Trading the Range" Playbook:**
- Institutions define a range where stop-loss clusters exist at both extremes
- They push to one extreme to grab liquidity, then trade toward the opposite extreme
- The key: the range must have **structural validity** (proper displacement, time-tested boundaries)

### 1.3 CRT and Institutional Range Trading

**Quality Range Criteria (Institutional):**
1. **Displacement candle quality**: The initiating candle must have:
   - Body ≥ 60% of total candle range
   - Close in the top/bottom 25% of the range
   - Above-average volume (1.5-2x)
   - Minimal upper/lower wick in the direction of the move
2. **Retracement zone**: Institutions watch for price to retrace into the Fibonacci extension zone (0.618-0.786) of the displacement leg
3. **OTE (Optimal Trade Entry)**: The overlap zone of OTE + order block + FVG — where three independent confluence factors align
4. **Range validity**: A range that survived at least 3-4 tests of each boundary is "structural." A brand-new range (1-2 touches) is "nascent."

**What Makes a "Noise Range":**
- Choppy price action without clear displacement
- Wicks that exceed bodies by 3:1 ratio
- Range boundaries touched 6+ times (saturation — about to break)
- No discernible displacement leg

### 1.4 Flow Analysis as Institutional Edge

**Effort vs. Result:**
- **Building volume** (accumulation): Volume gradually increases while price moves sideways → accumulation phase
- **Climactic volume** (exhaustion): Volume spikes 3-5x average on a sharp move → distribution phase. After climax, expect reversal or consolidation.
- **Divergence**: Price makes new high but volume/Delta doesn't confirm → weak move, likely reversal

**Delta Confirmation:**
- Positive delta + price rising = genuine buying pressure (bullish)
- Positive delta + price falling = selling into strength (bearish divergence — institutions distributing)
- Negative delta + price falling = genuine selling pressure (bearish)
- Negative delta + price rising = buying into weakness (bullish divergence — institutions accumulating)

### 1.5 Institutional Risk Management

**Stop Loss Placement (Institutional):**
- **Structural stops**: Beyond the nearest order block, swing point, or FVG — NOT a fixed ATR multiple
- **Buffer**: 0.5-1.0x ATR beyond the structural level to avoid noise wicks
- **Liquidity-aware**: Stops placed just beyond visible stop-loss clusters

**Minimum Risk/Reward:**
- HTF setups (Type A/C): Minimum 2.5:1 R:R, target 3:1 preferred
- LTF setups (Type B): Minimum 1.5:1 R:R, target 2:1 preferred (smaller position compensates for lower probability)

**Position Sizing:**
- Base: 1% of account per trade
- Multiplied by: signal type weight × score factor × regime adjustment × correlation factor
- Hard cap: 3% per single trade, 5% net per direction

**Edge Calculation:**
- Expected Value = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
- A scoring system is valid only if EV > 0 after slippage and fees
- Target: Minimum EV of 0.5% per trade (≈ 130% annualized at 2 trades/day)

---

## 2. D1 SCORING RUBRIC — 100 POINTS (HTF: 1H/4H/1D)

### 2.1 Complete Rubric Table

| # | Category | Max Points | Formula / Criteria |
|---|---|---|---|
| 1 | CRT Range Quality | 20 | Score from displacement quality (10) + retracement depth (5) + range boundary tests (5) |
| 2 | SMC Confluence | 25 | Order Block alignment (8) + FVG involvement (7) + Market Structure Break (6) + CHoCH/SMS (4) |
| 3 | Flow Confirmation | 15 | Volume quality (5) + Delta alignment (5) + Effort vs Result (5) |
| 4 | Momentum | 15 | Impulse strength (5) + Relative strength vs pair (5) + Divergence check (5) |
| 5 | Institutional Timing | 10 | Killzone alignment (4) + Session quality (3) + Days to expiry (3) |
| 6 | Risk/Reward Quality | 10 | R:R ratio (6) + Structural stop quality (4) |
| 7 | Confluence Bonus | 5 | Multi-factor alignment (count independent factors, up to 5) |
| **—** | **TOTAL** | **100** | — |

### 2.2 Detailed Scoring Logic

#### 2.2.1 CRT Range Quality (20 pts)

```
Displacement Quality (10 pts):
  - Body >= 70% of range + close in top/bottom 20% + volume >= 2x avg = 10 pts
  - Body >= 50% of range + close in top/bottom 30% + volume >= 1.5x avg = 7 pts
  - Body >= 40% of range + close in top/bottom 40% + volume >= 1.2x avg = 4 pts
  - Any other = 0 pts (no quality displacement)

Retracement Depth (5 pts):
  - Retraced exactly 0.618-0.786 Fib zone AND into OB zone = 5 pts
  - Retraced into OB zone only (no Fib precision) = 3 pts
  - Retraced but not into OB = 1 pt
  - No retracement (breakout leg still extending) = 2 pts (potential continuation)

Range Boundary Tests (5 pts):
  - 4+ touches per boundary = 5 pts (structural range)
  - 2-3 touches per boundary = 3 pts (nascent but valid)
  - 1 touch or new range = 1 pt
  - 6+ touches = 0 pts (saturated, about to break — breakout play only, not range play)
```

**Minimum threshold**: 8/20. Below this, the range is not institutionally valid.

#### 2.2.2 SMC Confluence (25 pts)

```
Order Block Alignment (8 pts):
  - Price reacting from a high-quality OB (unmitigated, institutional wick rejection) = 8 pts
  - Price approaching OB from favorable direction = 6 pts
  - Price in OB zone but no clear reaction yet = 4 pts
  - OB mitigated (tested and broken) = 0 pts (no longer valid)

Fair Value Gap Involvement (7 pts):
  - Price directly filling an FVG with structural support = 7 pts
  - Price near an FVG (within 1% of gap) = 5 pts
  - FVG exists but price not near it = 2 pts
  - No FVG in range = 0 pts

Market Structure Break (6 pts):
  - Clean MSB confirmed on HTF (close above/below structure point) = 6 pts
  - MSB forming but not confirmed (near structure point) = 3 pts
  - No MSB in range = 0 pts

CHoCH / SMS (4 pts):
  - Change of Character confirmed (last high/low broken, momentum shifting) = 4 pts
  - CHoCH forming (approaching structure point) = 2 pts
  - No CHoCH = 0 pts
```

**Minimum threshold**: 10/25. SMC is a core filter.

#### 2.2.3 Flow Confirmation (15 pts)

```
Volume Quality (5 pts):
  - Climactic volume on displacement + volume declining on retracement = 5 pts (classic accumulation)
  - Building volume trend (increasing on impulse, decreasing on retrace) = 4 pts
  - Above-average volume on key candle = 3 pts
  - Average volume = 1 pt
  - Below average = 0 pts

Delta Alignment (5 pts):
  - Delta fully aligned with price direction on impulse = 5 pts
  - Delta mostly aligned (>= 70% same direction) = 4 pts
  - Delta mixed (40-70% aligned) = 2 pts
  - Delta opposing price direction = 0 pts (institutional divergence)

Effort vs. Result (5 pts):
  - High effort (volume spike) + high result (significant price move) = 5 pts (genuine)
  - High effort + low result = 0 pts (absorption -- institutions absorbing, distribution/accumulation)
  - Low effort + high result = 2 pts (thin liquidity move, likely to reverse)
  - Both low = 0 pts (noise)
```

**Minimum threshold**: 8/15. Flow must confirm the narrative.

#### 2.2.4 Momentum (15 pts)

```
Impulse Strength (5 pts):
  - Strong impulse (>= 3 consecutive candles in direction, each closing in top/bottom 30%) = 5 pts
  - Moderate impulse (2 consecutive directional candles) = 3 pts
  - Weak impulse (1 directional candle) = 1 pt
  - No impulse = 0 pts

Relative Strength (5 pts):
  - Pair outperforming BTC/ETH by >= 15% over 24h = 5 pts
  - Pair outperforming by 5-15% = 3 pts
  - Pair underperforming but outperforming in current move = 1 pt
  - Pair underperforming both BTC/ETH = 0 pts (avoid catching falling knives)

Divergence Check (5 pts):
  - No divergence (price and momentum aligned) = 5 pts
  - Hidden divergence (momentum higher high/low, price higher high/low) = 4 pts (bullish for continuation)
  - Regular divergence = 0 pts (fatal flaw -- see 2.5 below)
```

**Minimum threshold**: 6/15.

#### 2.2.5 Institutional Timing (10 pts)

```
Killzone Alignment (4 pts):
  - London open (08:00-11:00 UTC) OR NY open (13:30-16:30 UTC) = 4 pts
  - Overlap (both sessions) = 4 pts
  - London close (10:30-12:00 UTC) = 2 pts
  - Asian session = 0 pts (low liquidity, higher spread)

Session Quality (3 pts):
  - High volatility session (macro news overlap, first hour of session) = 3 pts
  - Normal session = 2 pts
  - Low volatility session = 1 pt

Days Factor (3 pts):
  - 7-14 days from monthly option expiry = 3 pts (max pin risk manipulation)
  - 1-3 days from weekly options expiry = 2 pts
  - Mid-month, no expiry = 0 pts
```

#### 2.2.6 Risk/Reward Quality (10 pts)

```
R:R Ratio (6 pts):
  - 3.0:1 or better = 6 pts
  - 2.5:1 to 3.0:1 = 5 pts
  - 2.0:1 to 2.5:1 = 3 pts
  - 1.5:1 to 2.0:1 = 1 pt
  - < 1.5:1 = 0 pts (fatal flaw -- see 2.5 below)

Structural Stop Quality (4 pts):
  - Stop beyond OB + FVG confluence = 4 pts
  - Stop beyond OB OR FVG = 3 pts
  - Stop beyond swing point = 2 pts
  - Stop is arbitrary (ATR-based, no structural anchor) = 0 pts
```

#### 2.2.7 Confluence Bonus (5 pts)

```
Count independent confluence factors that are ALL satisfied:
  Factor 1: CRT range quality >= 14/20
  Factor 2: SMC confluence >= 16/25
  Factor 3: Flow confirmation >= 8/15
  Factor 4: Momentum >= 8/15
  Factor 5: Timing >= 6/10
  Factor 6: R:R >= 2.5:1

Score: 1 pt per satisfied factor, max 5 pts.
```

### 2.3 Tier System (D1)

| Tier | Score Range | Description |
|---|---|---|
| **SNIPER** | 85-100 | Exceptional confluence, all components strong, optimal timing. Take full position. |
| **OPPORTUNITY** | 65-84 | Good confluence, 4+ components satisfied, solid R:R. Take reduced position. |
| **WATCH** | 40-64 | Partial confluence, potential forming. Monitor for development. |
| **IGNORE** | 0-39 | Insufficient confluence or fatal flaw. Do not trade. |

### 2.4 Fatal Flaws (Auto-Disqualify -- Score = 0 regardless of other factors)

1. **Regular divergence** (price and momentum moving in opposite directions on HTF)
2. **R:R < 1.5:1** -- insufficient reward for the risk taken
3. **Structural stop not defined** -- can't define risk, can't trade
4. **Price in low-liquidity session** AND score < 70 (low-liquidity trades require higher conviction)
5. **Opposing MSB on same TF** -- market structure contradicts the direction
6. **Delta strongly opposing price** on the key impulse candle (>= 60% opposing delta)

---

## 3. D2 SCORING RUBRIC — 100 POINTS (LTF: 15M, ALL 529 PAIRS)

### 3.1 Core Difference from D1

D2 scores **entry precision and immediate flow** more heavily than HTF context. HTF structure (from D1) provides a **cross-reference bonus** but is NOT a gate. This is the critical architectural change.

### 3.2 Complete Rubric Table

| # | Category | Max Points | Formula / Criteria |
|---|---|---|---|
| 1 | Entry Precision (LTF OB/FVG Retest) | 25 | Order block retest quality (10) + FVG fill (8) + Wick rejection quality (7) |
| 2 | LTF Structure Break | 20 | MSB on 15M (8) + CHoCH on 15M (7) + Swing point break quality (5) |
| 3 | Immediate Flow (15M) | 20 | Volume on breakout (7) + Delta alignment (7) + Effort vs Result (6) |
| 4 | Nascent Move Confidence | 15 | 5-condition check (see section 4 below) -- scored pass/fail per condition |
| 5 | HTF Context Bonus | 10 | Cross-reference with D1 structure (same direction +5, neutral +2, opposing -5) |
| 6 | Momentum Quality | 10 | Impulse strength on 15M (5) + Acceleration (5) |
| 7 | Timing & Session | 5 | Killzone bonus (3) + Intraday session quality (2) |
| 8 | Confluence Bonus | 5 | Multi-factor alignment (independent factors, up to 5) |
| **--** | **TOTAL** | **100** | -- |

### 3.3 Detailed Scoring Logic

#### 3.3.1 Entry Precision -- LTF OB/FVG Retest (25 pts)

This is the most important D2 component. We want precision entries, not chasing.

```
Order Block Retest (10 pts):
  - Perfect retest (price touches OB boundary + wick rejection + close away) = 10 pts
  - Good retest (price enters OB zone + shows reaction) = 7 pts
  - Approaching OB from above/below = 5 pts
  - No retest (chasing the move) = 2 pts
  - OB already mitigated = 0 pts

FVG Fill (8 pts):
  - Exact fill of unmitigated FVG with wick rejection = 8 pts
  - Price entering FVG zone = 5 pts
  - FVG nearby (within 0.5%) = 3 pts
  - No FVG involvement = 0 pts

Wick Rejection Quality (7 pts):
  - Strong wick (upper/lower wick >= 40% of range) + close in opposite direction = 7 pts
  - Moderate wick (20-40% of range) = 5 pts
  - Small wick (10-20%) = 3 pts
  - No wick / doji = 1 pt (no clear rejection)
```

**Minimum threshold**: 15/25. Entry precision must be clean for any D2 signal.

#### 3.3.2 LTF Structure Break (20 pts)

```
MSB on 15M (8 pts):
  - Clean close above/below last swing point with volume = 8 pts
  - Close near swing point (piercing but not confirming) = 5 pts
  - No break = 0 pts

CHoCH on 15M (7 pts):
  - Last lower high broken (bullish) or last higher low broken (bearish) = 7 pts
  - Approaching CHoCH level = 4 pts
  - No CHoCH forming = 0 pts

Swing Point Break Quality (5 pts):
  - Break of double bottom/top = 5 pts
  - Break of single swing point = 3 pts
  - No structure break = 0 pts
```

#### 3.3.3 Immediate Flow -- 15M (20 pts)

```
Volume on Breakout (7 pts):
  - >= 3x average volume on breakout candle = 7 pts
  - 2-3x average = 5 pts
  - 1.5-2x average = 3 pts
  - Below average = 0 pts

Delta Alignment (7 pts):
  - Delta >= 80% aligned with direction on breakout = 7 pts
  - Delta 60-80% aligned = 5 pts
  - Delta 40-60% (mixed) = 2 pts
  - Delta opposing = 0 pts

Effort vs. Result (6 pts):
  - High effort + high result (genuine move) = 6 pts
  - High effort + low result (absorption) = 2 pts (might still work as accumulation)
  - Low effort + high result = 1 pt (thin market move)
  - Both low = 0 pts
```

#### 3.3.4 Nascent Move Confidence (15 pts) -- See section 4

Scored via the 5-condition Nascent Move Detector (all 5 must pass for Type B classification).

#### 3.3.5 HTF Context Bonus (10 pts)

This is a **bonus, not a gate**. HTF context adjusts the score but does not block D2 signals.

```
Same direction as D1: +5 pts (confirms the move)
D1 neutral (range-bound, no clear direction): +2 pts
Opposing direction to D1: -5 pts (conflict, may still be Type B but lower score)
D1 has no signal for this pair: +3 pts (no opposing context, neutral)
```

**CRITICAL**: Even if this gives a negative net, D2 signals CAN still score high enough for Type B. A -5 penalty on a 75-point setup = 70, which still qualifies. The key is the Type Classifier (A/B/C/D/E) handles conflicts explicitly.

#### 3.3.6 Momentum Quality (10 pts)

```
Impulse Strength on 15M (5 pts):
  - 3+ consecutive directional candles, strong close = 5 pts
  - 2 directional candles = 3 pts
  - 1 directional candle = 1 pt

Acceleration (5 pts):
  - Current candle larger than previous (increasing momentum) = 5 pts
  - Similar size candles = 3 pts
  - Decelerating (candle getting smaller) = 1 pt
```

#### 3.3.7 Timing & Session (5 pts)

```
Killzone Bonus (3 pts):
  - Within London/NY killzone = 3 pts
  - Within 30 min of killzone open/close = 2 pts
  - Outside killzones = 0 pts

Intraday Session (2 pts):
  - High volume hour (session open, news) = 2 pts
  - Normal hour = 1 pt
```

#### 3.3.8 Confluence Bonus (5 pts)

Same as D1: count independent satisfied factors, 1 pt each, max 5.

### 3.4 Tier System (D2)

| Tier | Score Range | Description |
|---|---|---|
| **SNIPER** | 85-100 | Perfect LTF entry + strong flow + HTF aligned. Full position. |
| **OPPORTUNITY** | 65-84 | Good LTF entry + flow confirmation. Reduced position. |
| **WATCH** | 40-64 | Potential entry forming. Monitor. |
| **IGNORE** | 0-39 | No valid entry. |

### 3.5 Fatal Flaws (D2)

1. **No structure break AND no entry precision** -- chasing without structure
2. **Delta strongly opposing on 2+ consecutive candles** -- institutional selling/buying against the direction
3. **Volume < 1.0x average on the key candle** -- no institutional participation
4. **Entry is more than 2% past the OB/FVG zone** -- missed the optimal entry, chasing

---

## 4. NASCENT MOVE DETECTOR — TYPE B DETECTION

### 4.1 Purpose

The Nascent Move Detector identifies **Type B -- LTF Momentum Plays**: moves that are just beginning on the 15M timeframe before the 1H/4H/1D confirm. This is the critical differentiator that prevents missing fast movers.

### 4.2 The 5 Conditions

All 5 conditions must be satisfied for a signal to qualify as Type B:

| # | Condition | Description | Weight |
|---|---|---|---|
| 1 | **15M Structure Break** | Close above/below the most recent swing point with >= 1.5x average volume. This is the first sign that the structure is shifting. | Required (pass/fail) |
| 2 | **OB Interaction** | Price is retesting an impulse order block within 15-30 minutes of the break. The OB must be the one created by the displacement leg itself. | Required (pass/fail) |
| 3 | **Volume Confirmation** | Breakout candle has >= 2x average volume AND delta is >= 60% aligned with direction. This confirms institutional participation, not retail noise. | Required (pass/fail) |
| 4 | **Liquidity Sweep** | A stop-loss cluster (recent swing low/high or round number) was taken out within the last 2 hours before the break. The sweep must be >= 0.5% of price. | Required (pass/fail) |
| 5 | **No Direct Opposing HTF Structure** | The 1H and 4H timeframes have NO direct opposing signal for this pair. A "neutral" HTF (range-bound) is acceptable. A clear opposing structure (e.g., 4H showing bearish MSB while 15M breaks bullish) FAILS this condition. | Required (pass/fail) |

### 4.3 Logic Flow

```
FOR each pair in ALL_529_PAIRS:
    SCAN 15M chart:
        IF Condition 1 (15M MSB) is FALSE --> skip (no nascent move)
        IF Condition 2 (OB retest) is FALSE --> skip (no entry precision)
        IF Condition 3 (Volume + Delta) is FALSE --> skip (no institutional confirmation)
        IF Condition 4 (Liquidity sweep) is FALSE --> lower confidence (score -5, still proceeds)
        IF Condition 5 (No opposing HTF) is FALSE --> conflict detected (score -5, may become Type E)

    IF all 5 conditions pass:
        --> Full Type B confidence (15 pts in D2 scoring)
        --> Classification: Type B -- LTF Momentum Play
    IF conditions 1-3 pass, 4-5 fail:
        --> Partial Type B confidence (8 pts in D2 scoring)
        --> Classification: Type B -- LTF Momentum Play (reduced position)
    IF conditions 1-3 pass, 5 fails (but 4 passes):
        --> Type E -- Conflict/Trap (alert, monitor for resolution)
    IF conditions 1-3 pass, 4 fails (but 5 passes):
        --> Type B with caution (position at 0.35x instead of 0.5x)
```

### 4.4 Position Size Modifier for Nascent Moves

| Condition Score | Position Multiplier | Stop Width |
|---|---|---|
| 5/5 conditions | 0.5x base | 1.0x ATR (tight) |
| 4/5 conditions | 0.35x base | 1.0x ATR (tight) |
| 3/5 conditions | 0.25x base | 0.75x ATR (very tight) |
| < 3/5 | Not a Type B | -- |

**Time-based exit**: Type B trades that haven't resolved within 15 minutes get auto-closed. This is the TTL for nascent moves.

---

## 5. SIGNAL TAXONOMY

### 5.1 Signal Types

| Type | Name | Description | Probability | Position Size | Stop Width | TTL |
|---|---|---|---|---|---|---|
| **A** | HTF Structure Play | D1 SNIPER/OPPORTUNITY (>=70), D2 50-69 (moderate LTF confirmation). Slow, deliberate, high-probability. | 60-70% | 0.75x base | 1.5x ATR | 2h |
| **B** | LTF Momentum Play | D2 score >= 72, Entry Precision >= 18/25, nascent_move detected. D1 NOT approved. Fast breakout/momentum on 15M before HTF confirms. | 45-55% | 0.25-0.5x base | 0.75-1.0x ATR | 15 min |
| **C** | Full Confluence | D1 SNIPER (>=85) + D2 SNIPER (>=85): tight stop, 2h TTL. D1 SNIPER (>=85) + D2 >=70: standard stop, 4h TTL. Both timeframes agree. | 70-80% | 1.0x base | 1.0-1.5x ATR | 2-4h |
| **D** | HTF Early Warning | D1 >= 70 but D2 not aligned. HTF structure shifting, waiting for LTF confirmation. | N/A (no entry) | -- | -- | 1h |
| **E** | Conflict / Trap | D1 and D2 disagree on direction. Potential fakeout. | N/A (no entry) | -- | -- | Alert only |

### 5.2 Signal Type Determination Logic

```
Decision Layer receives:
  d1_score, d1_tier, d1_direction
  d2_score, d2_direction
  nascent_move_detected (bool)

CLASSIFICATION:

IF d1_tier == IGNORE AND d2_tier != IGNORE AND d2_score >= 65 AND nascent_move_detected:
    --> Type B (LTF Momentum Play)

ELIF d1_tier != IGNORE AND d1_score >= 70 AND d2_tier != IGNORE AND d2_score >= 70 AND d1_direction == d2_direction:
    --> Type C (Full Confluence)

ELIF d1_tier != IGNORE AND d1_score >= 70 AND d2_tier != IGNORE AND d2_score >= 50 AND d1_direction == d2_direction:
    --> Type A (HTF Structure Play)

ELIF d1_tier != IGNORE AND d1_score >= 70 AND (d2_tier == IGNORE OR d2_score < 50):
    --> Type D (HTF Early Warning)

ELIF d1_tier != IGNORE AND d2_tier != IGNORE AND d1_direction != d2_direction:
    --> Type E (Conflict / Trap)

ELIF d1_tier == IGNORE AND d2_tier == IGNORE:
    --> No Signal (IGNORE)

ELIF d1_tier == IGNORE AND d2_score < 65:
    --> No Signal (IGNORE)

ELSE:
    --> No Signal (insufficient confluence)
```

### 5.3 Signal Type Behavior Summary

| Aspect | Type A | Type B | Type C | Type D | Type E |
|---|---|---|---|---|---|
| **Tradeable?** | Yes | Yes | Yes | No | No |
| **Position Size** | 0.75x | 0.25-0.5x | 1.0x | -- | -- |
| **Stop Width** | 1.5x ATR | 0.75-1.0x ATR | 1.5x ATR | -- | -- |
| **R:R Minimum** | 2.5:1 | 1.5:1 | 3.0:1 | -- | -- |
| **Time in Trade** | Hours | Minutes | Hours | -- | -- |
| **TTL** | 2 hours | 15 min | 4 hours | 1h watch | Alert |
| **Decay** | Slow (30 min) | Fast (5 min) | Slow (30 min) | -- | -- |
| **Risk Level** | Medium | Higher | Lowest | -- | -- |
| **Trailing Stop?** | Yes (50% to BE) | No (fixed exit) | Yes (50% to BE) | -- | -- |

---

## 6. DECISION MATRIX

### 6.1 Full Decision Matrix

| D1 Status | D1 Score | D2 Status | D2 Score | Nascent? | Signal Type | Action | Position Size | Stop Width | TTL |
|---|---|---|---|---|---|---|---|---|---|
| Approved | >= 85 | Aligned | >= 85 | -- | **Type C** | **EXECUTE** | 1.0x base | 1.0x ATR | 2h |
| Approved | >= 85 | Aligned | >= 70 | -- | **Type C** | **EXECUTE** | 1.0x base | 1.5x ATR | 4h |
| Approved | >= 70 | Aligned | 50-69 | -- | **Type A** | **EXECUTE** | 0.75x base | 1.5x ATR | 2h |
| Not approved | < 40 | Nascent | >= 72 | Yes | **Type B** | **EXECUTE** | 0.5x base | 1.0x ATR | 15 min |
| Not approved | < 40 | Nascent | 72-80 | Yes | **Type B** | **EXECUTE** | 0.35x base | 1.0x ATR | 15 min |
| Watch | 40-64 | Score | >= 65 | No | -- | **WATCH** | -- | -- | 1h |
| Approved | >= 70 | Weak/absent | < 70 | -- | **Type D** | **WATCH** | -- | -- | 1h |
| Valid | >= 40 | Opposite direction | Any | -- | **Type E** | **ALERT** | -- | -- | -- |
| Not approved | < 40 | Opposite direction | Any | -- | **Type E** | **ALERT** | -- | -- | -- |

### 6.2 Decision Logic Pseudocode

```python
def classify_signal(d1, d2):
    # d1 = {tier, score, direction}
    # d2 = {tier, score, direction, nascent_move}

    d1_approved = d1.tier in (SNIPER, OPPORTUNITY) and d1.score >= 65
    d2_approved = d2.tier in (SNIPER, OPPORTUNITY) and d2.score >= 50
    directions_align = d1.direction == d2.direction

    # Type C: Both SNIPER (>= 85) → tighter stop, shorter TTL
    if d1_approved and d2_approved and d1.score >= 85 and d2.score >= 85 and directions_align:
        return SignalType.C, 1.0, 1.0, 2h

    # Type C: D1 SNIPER (>= 85), D2 >= 70
    if d1_approved and d2_approved and d1.score >= 85 and d2.score >= 70 and directions_align:
        return SignalType.C, 1.0, 1.5, 4h

    # Type A: HTF Structure Play — D1 >= 70, D2 50-69 (moderate LTF confirmation)
    if d1_approved and d2.tier != IGNORE and d2.score >= 50 and d2.score < 70 and directions_align:
        return SignalType.A, 0.75, 1.5, 2h

    # Type B: LTF Momentum Play (nascent, D1 not approved)
    if not d1_approved and d2.nascent_move and d2.score >= 72:
        mult = 0.5 if d2.score >= 75 else 0.35
        return SignalType.B, mult, 1.0, 15min

    # Type E: Conflict/Trap — both valid but opposing directions
    if d1_approved and d2_approved and not directions_align:
        return SignalType.E, 0, 0, ALERT

    # Type E: one valid, other valid but opposite direction
    if d1_approved and d2.tier != IGNORE and not directions_align:
        return SignalType.E, 0, 0, ALERT

    # Type D: HTF Early Warning — D1 valid, D2 weak/absent
    if d1_approved and not d2_approved:
        return SignalType.D, 0, 0, 1h  # Watch only

    # No Signal
    return None, 0, 0, 0
```

### 6.3 Conflict Resolution Rules

1. **Type E alerts go to frontend** with "POTENTIAL FAKEOUT" flag -- highlighted in red/orange.
2. **Type E with D1 approved**: The D1 signal continues to be watched on StateStore. If D2 later aligns, it upgrades to Type A or C.
3. **Type E with opposing scores within 10 points**: Higher confidence of a real institutional reversal (CHoCH). Alert priority: HIGH.
4. **Type E with opposing scores > 20 points apart**: One side is clearly wrong. Trust the higher score but mark as "CONFLICT" -- manual review recommended.
5. **D2 Type B that doesn't resolve within TTL**: Auto-classify as "FALSE BREAKOUT" and log for backtest analysis.

---

## 7. 16-STATE MARKET EVOLUTION MATRIX MAPPING

### 7.1 The 16 States

The market_evolution engine tracks 16 states across 4 quadrants and 4 positions:

| Quadrant | States (in order of evolution) |
|---|---|
| **COMPRESSION** (Range) | Dormant -> Consolidation -> Compression -> Coiling |
| **EXPANSION** (Trend) | Awakening -> Expansion -> Institutional Entry -> Acceleration |
| **CHANGE** (Transition) | Transition -> Distribution -> Reversal -> Capitulation |
| **INSTITUTIONAL** (Manipulation) | Trap -> Sweep -> Accumulation -> Markup |

### 7.2 How Market Evolution State Integrates with the Decision Layer

The Market Evolution Matrix is the **interpretive layer**. It does NOT determine whether a signal is tradeable -- the Decision Layer does that. Instead, the Matrix provides **context** for:

1. **Setup quality adjustment**: A Type C signal in "Acceleration" state is different from Type C in "Transition" state.
2. **Risk adjustment**: Signals during "Trap" or "Distribution" states get tighter stops.
3. **Position sizing modifier**: State-based multiplier on the base position.
4. **Frontend display**: Shows Signal Type + Market Evolution State, so the trader understands context.

### 7.3 Mapping: Signal Type x Market Evolution State -> Interpretation

| Signal Type | Dormant | Consolidation | Compression | Coiling | Awakening | Expansion | Inst. Entry | Acceleration | Transition | Distribution | Reversal | Capitulation | Trap | Sweep | Accumulation | Markup |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Type A** | Watch | Low conv. | Setup forming | Valid | Good | Strong | Best | **NO NEW** | Risky | Caution | Avoid | No | No | Wait | Possible | Good |
| **Type B** | No | No | Risky | Possible | Possible | Good | Good | Possible | Caution | No | No | No | No | Possible | Risky | Good |
| **Type C** | Wait | Good | Strong | Excellent | Excellent | Excellent | Perfect | **NO NEW** | Caution | Caution | Possible | Possible | No | Possible | Strong | Best |
| **Type D** | Watch | Watch | Watch | Watch | Watch | Act when LTF aligns | Act | Act | Monitor | Monitor | No | No | No | Alert | Watch | Watch |
| **Type E** | Ignore | Ignore | Ignore | Ignore | Alert | Alert | High alert | High alert | Key moment | Key moment | Key moment | Key moment | Likely trap | Likely sweep | Monitor | Monitor |

### 7.4 Market Evolution State Validation

> **Note**: The exact state names and definitions should be validated against the actual market_evolution engine code (models.py, engine.py, transitions.py) to ensure alignment with the 16-state model in this document.

### 7.5 State-Based Position Sizing Modifiers

| Market Evolution State | Position Multiplier |
|---|---|
| Dormant | 0.5x |
| Consolidation | 0.75x |
| Compression | 0.75x |
| Coiling | 0.85x |
| Awakening | 0.85x |
| Expansion | 1.0x |
| Institutional Entry | 1.0x |
| Acceleration | 0x (NO NEW ENTRIES — take profits only) |
| Transition | 0.5x |
| Distribution | 0.35x |
| Reversal | 0.5x |
| Capitulation | 0.25x (contrarian only) |
| Trap | 0x (no new positions) |
| Sweep | 0x (no new positions) |
| Accumulation | 0.85x |
| Markup | 1.0x |

**Effective position size** = Signal Type multiplier x State multiplier x Score factor x Base (1%)

Example: Type C (1.0x) in Expansion (1.0x) with score 85 -> effective = 1.0 x 1.0 x 0.85 x 1% = 0.85% of account

---

## 8. HTF-LTF TIMING ASYMMETRY: THE CRITICAL PROBLEM

### 8.1 Current Architecture (BROKEN)

```
D1 Scanner (1H/4H/1D, 15s cycle)
  |-- Scans ALL 529 pairs
  |-- Approves ~20-30 pairs as SNIPER/OPPORTUNITY/WATCH
  |-- writes approved pairs to StateStore

D2 Scanner (15M, 5s cycle)
  |-- READS StateStore for approved pairs ONLY
  |-- Scans only D1-approved pairs
  |-- THIS IS THE BOTTLENECK

D3 Fusion
  |-- Reads D1 tiers + D2 scores
  |-- Outputs 3x3 bucket grid
```

### 8.2 What Goes Wrong: The Fast Mover Problem

**Timeline of a missed fast mover:**

| Time | HTF (1H/4H) | LTF (15M) | What Happened |
|---|---|---|---|
| 10:00 | 1H range valid | 15M breaks structure | Fast move begins |
| 10:05 | No change | 15M: OB retest, volume spike | LTF signals Type B |
| 10:15 | 1H still in range | 15M: +2.5% move | D2 should catch this but doesn't -- not D1-approved |
| 10:30 | 1H starting to show stress | 15M: +5% | Still not D1-approved |
| 11:00 | 1H breaks range! | 15M: +6%, cooling | NOW D1 approves. Coin is +6%. We enter late. |

**Result**: We enter a Type A setup at +6% after the move is mostly done. Stop gets hit on pullback. We bought the breakout, sold the breakdown.

### 8.3 Why D1 Approves Late

HTF (1H/4H) structure breaks are **lagging indicators**. By definition, a 1H range break requires:
1. The price to close outside the range on the 1H candle (60 minutes of data)
2. Plus confirmation on the next candle
3. Plus the D1 scan cycle (15 seconds for all 529 pairs)

Minimum latency: ~61 minutes + scan cycle. By then, the LTF has already made the move.

### 8.4 Required Architecture: PARALLEL

```
D1 Scanner (1H/4H/1D, 15s cycle)     <-- SLOW MOVERS (HTF structure plays)
  |-- Scans ALL 529 pairs independently
  |-- Writes D1 scores to StateStore
  |-- Does NOT gate D2

D2 Scanner (15M, 5s cycle)           <-- FAST MOVERS (LTF breakout/momentum)
  |-- Scans ALL 529 pairs independently <-- NO D1 GATING
  |-- Nascent Move Detector for Type B
  |-- Writes D2 scores to StateStore

StateStore
  |-- d1_signals: {pair: {tf: score}}
  |-- d2_signals: {pair: score}
  |-- Both updated independently

Decision Layer
  |-- Reads d1 + d2 independently
  |-- Classifies Signal Type (A/B/C/D/E)
  |-- Applies Decision Matrix
  |-- Maps to 16-state Market Evolution Matrix
  |-- Calculates position size
  |-- Outputs d3_decisions: {pair: decision}
```

### 8.5 Nascent Move Detector: Why It Works

The detector identifies Type B signals by requiring **5 independent confirmations** on the 15M timeframe:

1. **15M structure break** -- the move has structural validity
2. **OB retest** -- price is at an institutional entry zone
3. **Volume + delta confirmation** -- institutions are participating
4. **Liquidity sweep** -- stop-loss cluster taken out, classic institutional move
5. **No opposing HTF structure** -- no structural resistance in the direction of the move

The combination of these 5 factors reduces the false positive rate significantly. While a single LTF breakout has a 60-70% false positive rate, a 5-condition confluence breakout has a < 30% false positive rate -- comparable to a D1-only approach.

### 8.6 Risk Differential: Type B vs. Type A/C

| Metric | Type A/C (HTF) | Type B (LTF) |
|---|---|---|
| Win Rate | 65-75% | 45-55% |
| Avg R:R | 2.5:1 - 3:1 | 1.5:1 - 2:1 |
| Avg Win (R) | 1.875R - 2.25R | 0.675R - 1.1R |
| Avg Loss (R) | 1R | 1R |
| Expected Value per trade | 0.19R - 0.69R | -0.025R to +0.1R |
| Position Size | 0.75-1.0x | 0.25-0.5x |
| EV per unit risk | Highest | Lower (compensated by more opportunities) |
| # Opportunities per day | 3-5 | 15-30 |

**Key insight**: Type B has lower per-trade EV but more opportunities. Over a day, a well-tuned parallel system can capture more total return than D1-only by:
1. Entering moves at the beginning (not the end)
2. Catching moves that D1 never approves
3. Taking quick profits with tight stops (high frequency, small size)

### 8.7 Fast Mover Capture Backtest

After implementation, validate with:
1. **Historical analysis**: For each approved D1 signal, check how many 15M breakouts occurred in the 30-60 minutes BEFORE D1 approved them.
2. **Type B backtest**: Run the Nascent Move Detector on 90 days of historical data. Measure:
   - Win rate for Type B signals
   - False positive rate
   - Average R:R
   - P&L per trade
   - Compare against Type A/C performance
3. **Capture rate**: How many fast-mover moves (>2% in <30min) did we catch vs. miss?

---

## 9. POSITION SIZING ENGINE

### 9.1 Base Formula

```
Effective Position Size = Base Size x Signal Type Multiplier x Score Factor x State Multiplier x Session Factor x Correlation Factor
```

**Caps:**
- Hard cap per trade: **3%** of account
- Hard cap per direction: **5%** of account

### 9.2 Component Values

| Component | Formula |
|---|---|
| **Base Size** | 1.0% of account |
| **Signal Type Multiplier** | Type A: 0.75, Type B: 0.35, Type C: 1.0 |
| **Score Factor** | Score/100 (e.g., score 80 = 0.80) |
| **State Multiplier** | See section 7.5 table |
| **Session Factor** | Killzone: 1.0, Normal: 0.9, Asian: 0.7 |
| **Correlation Factor** | 1.0 if no conflict; 0.5 if 2+ same-direction positions |

### 9.3 Example Calculations

**Example 1: Type C Full Confluence in Expansion**
- Base: 1.0%
- Type C: 1.0
- Score: 85 -> 0.85
- State (Expansion): 1.0
- Session (killzone): 1.0
- Correlation: 1.0
- **Effective: 0.85%** of account

**Example 2: Type B LTF Momentum in Awakening**
- Base: 1.0%
- Type B: 0.35
- Score: 72 -> 0.72
- State (Awakening): 0.85
- Session (killzone): 1.0
- Correlation: 1.0
- **Effective: 0.21%** of account

**Example 3: Type A HTF Structure in Dormant (should be avoided by State factor)**
- Base: 1.0%
- Type A: 0.75
- Score: 78 -> 0.78
- State (Dormant): 0.5
- Session: 1.0
- Correlation: 1.0
- **Effective: 0.29%** -- very small, effectively filtered out

### 9.4 Correlation Filter Logic

```python
def calculate_correlation_factor(active_positions, new_direction):
    same_direction_count = sum(
        1 for p in active_positions
        if p.direction == new_direction
    )

    if same_direction_count >= 4:
        return 0.0  # Hard stop: max 4 same-direction positions
    elif same_direction_count == 3:
        return 0.5
    elif same_direction_count == 2:
        return 0.75
    else:
        return 1.0
```

---

## 10. COMPLETE SCORING PIPELINE

### 10.1 D1 Pipeline (HTF: 1H/4H/1D)

```
For each of 529 pairs:

1. CRT Analysis
   |-- Identify range (if any)
   |-- Score displacement quality
   |-- Score retracement depth
   |-- Score boundary tests
   -> Max: 20 pts, Min: 8 pts

2. SMC Analysis
   |-- Check OB alignment
   |-- Check FVG involvement
   |-- Check MSB
   |-- Check CHoCH
   -> Max: 25 pts, Min: 10 pts

3. Flow Analysis
   |-- Volume quality
   |-- Delta alignment
   |-- Effort vs Result
   -> Max: 15 pts, Min: 5 pts

4. Momentum
   |-- Impulse strength
   |-- Relative strength
   |-- Divergence check
   -> Max: 15 pts, Min: 6 pts

5. Institutional Timing
   |-- Killzone
   |-- Session quality
   |-- Days factor
   -> Max: 10 pts

6. Risk/Reward
   |-- R:R ratio
   |-- Structural stop quality
   -> Max: 10 pts

7. Confluence Bonus
   |-- Count satisfied factors
   -> Max: 5 pts

8. FATAL FLAW CHECK
   |-- Regular divergence? -> DISQUALIFY
   |-- R:R < 1.5:1? -> DISQUALIFY
   |-- No structural stop? -> DISQUALIFY
   |-- Opposing MSB? -> DISQUALIFY
   |-- Delta opposing? -> DISQUALIFY

9. SUM -> Score (0-100)
10. TIER -> SNIPER(85-100) / OPPORTUNITY(65-84) / WATCH(40-64) / IGNORE(0-39)
11. DIRECTION -> LONG / SHORT / NEUTRAL
12. WRITE to StateStore.d1_signals[pair][tf]
```

### 10.2 D2 Pipeline (LTF: 15M, ALL 529 pairs)

```
For each of 529 pairs (INDEPENDENT of D1):

1. Entry Precision
   |-- OB retest quality
   |-- FVG fill
   |-- Wick rejection
   -> Max: 25 pts, Min: 12 pts

2. LTF Structure Break
   |-- MSB on 15M
   |-- CHoCH on 15M
   |-- Swing point break
   -> Max: 20 pts

3. Immediate Flow
   |-- Volume on breakout
   |-- Delta alignment
   |-- Effort vs Result
   -> Max: 20 pts, Min: 5 pts

4. Nascent Move Check (5 conditions)
   |-- 15M structure break
   |-- OB interaction
   |-- Volume + Delta
   |-- Liquidity sweep
   |-- No opposing HTF
   -> Pass = 15 pts, Partial = 8 pts, Fail = 0 pts

5. HTF Context Bonus
   |-- Same direction: +5
   |-- Neutral: +2
   |-- Opposite: -5
   |-- No D1 data: +3
   -> Max: 10 pts, Min: -5 pts

6. Momentum Quality
   |-- Impulse strength
   |-- Acceleration
   -> Max: 10 pts, Min: 0 pts

7. Timing & Session
   |-- Killzone
   |-- Session
   -> Max: 5 pts

8. Confluence Bonus
   |-- Count factors
   -> Max: 5 pts

9. FATAL FLAW CHECK
   |-- No structure + no precision? -> DISQUALIFY
   |-- Delta opposing 2+ candles? -> DISQUALIFY
   |-- Volume < 1.0x avg? -> DISQUALIFY
   |-- Entry > 2% past OB/FVG? -> DISQUALIFY

10. SUM -> Score (0-100)
11. TIER -> SNIPER(85-100) / OPPORTUNITY(65-84) / WATCH(40-64) / IGNORE(0-39)
12. DIRECTION -> LONG / SHORT / NEUTRAL
13. NASCENT_MOVE -> bool (5/5 conditions)
14. WRITE to StateStore.d2_signals[pair]
```

### 10.3 Decision Layer Pipeline

```
For each pair with both D1 and D2 signals:

1. READ D1: score, tier, direction
2. READ D2: score, tier, direction, nascent_move
3. CLASSIFY:
   |-- Type C: D1>=85 (SNIPER), D2>=85 (SNIPER), aligned directions
   |-- Type C: D1>=85 (SNIPER), D2>=70 (OPPORTUNITY+), aligned directions
   |-- Type A: D1>=70 (OPPORTUNITY+), D2>=70 (OPPORTUNITY+), aligned directions
   |-- Type B: D1 not approved, D2>=72, nascent_move detected
   |-- Type D: D1>=70 valid, D2 weak/absent
   |-- Type E: D1+D2 both valid but opposing directions
   |-- Type E: D1 valid but D2 opposite direction
4. READ Market Evolution State (from market_evolution engine)
5. APPLY State-based position multiplier
6. CALCULATE position size: Base x Type Mult x Score Factor x State Mult x Session x Correlation
7. CHECK correlation filter (max 4 same-direction)
8. SET stop width and TTL
9. WRITE to StateStore.d3_decisions[pair]
10. BROADCAST to frontend
```

---

## APPENDIX A: EXPECTED VALUE CALCULATION

**EV is calculated per signal, not from a lookup table.** Per-signal variables (entry precision, slippage, actual R:R achieved, fill quality) make a fixed table misleading.

**Formula:**

```
EV = (Win_Rate × Avg_Win) - (Loss_Rate × Avg_Loss)
```

Where:
- `Win_Rate` = historical or modeled win probability for this signal type (Type A: 60-70%, Type B: 45-55%, Type C: 70-80%)
- `Avg_Win` = average win as a multiple of risk (R) — depends on actual R:R achieved at exit
- `Loss_Rate` = 1.0 - Win_Rate
- `Avg_Loss` = 1.0R (defined by stop)

**Validity rule**: A signal is tradeable only if modeled EV > 0 after slippage (0.1% per trade) and fees. Target minimum modeled EV of 0.5% per trade.

**Backtest calibration**: After collecting 50+ trades per signal type, replace modeled win rates with empirical values. If EV is negative for any signal type, that type's entry threshold or position size should be tightened.

## APPENDIX B: DECAY AND REVALIDATION

| Component | Revalidation Frequency | Decay Rate |
|---|---|---|
| D1 Signal | Every scan cycle (15s) | None -- always freshly calculated |
| D2 Signal | Every scan cycle (5s) | None -- always freshly calculated |
| Type D (Watch) | Every 15 minutes | No decay -- re-evaluate from scratch |
| Type A | Every 5 minutes | Score x 0.94 per 5 min if not re-evaluated |
| Type B | Every 2 minutes | Score x 0.90 per 2 min if not re-evaluated |
| Type C | Every 5 minutes | Score x 0.98 per 5 min if not re-evaluated |
| Type E | Every 10 minutes | No decay -- stay on alert |

**Revalidation rule**: If any signal's score drops below its entry threshold after decay, auto-close/remove the signal.

---

**END OF PHASE 1B -- SCORING FRAMEWORK DOCUMENT**
