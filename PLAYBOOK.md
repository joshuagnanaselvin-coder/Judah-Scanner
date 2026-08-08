# Trading Playbook

How to trade Judah Scanner signals in live markets.

## Signal Decision Matrix

### Type C — Full Confluence (Trade Immediately)
- **Criteria**: D1 SNIPER + D2 SNIPER + same direction
- **Action**: Enter at limit near D2 Entry level
- **Position**: Full size (1.0x)
- **Stop**: 1.5x wider than standard (based on D2 SL + spread buffer)
- **Targets**: TP1 at 1.5R, TP2 at 2.5R, trail remainder
- **Validation**: Highest conviction setup. Does not happen often (~1-3% of signals).
- **Worst case**: D3 revalidates as Type D if D2 degrades — tighten stop to breakeven.

### Type A — HTF Structure (Enter on Pullback)
- **Criteria**: D1 SNIPER/OPPORTUNITY + D2 >= 50 + aligned direction
- **Action**: Enter on first LTF pullback to OB/FVG after D2 confirms
- **Position**: 0.75x
- **Stop**: 1.5x wider, below nearest LTF structure
- **Targets**: TP1 at HTF OB/previous structure, TP2 at range target
- **Validation**: Check that D1 is fresh (< 30 min old). Stale D1 SNIPERs degrade fast.
- **Worst case**: If D1 degrades to WATCH, D3 reclassifies to Type D — exit half position.

### Type B — LTF Momentum (Aggressive Entry)
- **Criteria**: D1 not approved + D2 >= 72 + nascent move + EP >= 16
- **Action**: Enter immediately on D2 trigger. Tight stop.
- **Position**: 0.35x (reduced — no HTF backing)
- **Stop**: 1.0x (standard, at LTF structure)
- **Targets**: TP1 at 1.0R, TP2 at 1.5R, close all on D1 update
- **Validation**: This is a scalping/momentum trade. Hold max 15 minutes.
- **Worst case**: If D1 updates and approves → may upgrade to Type A. If D1 updates and opposes → exit immediately.

### Type D — HTF Early Warning (Watch Only)
- **Criteria**: D1 >= 65 + D2 not aligned
- **Action**: NO ENTRY. Watch for D2 to catch up.
- **Validation**: Set price alert at D2 Entry level. If D2 reclassifies and aligns, next cycle will produce Type A.
- **Worst case**: If D2 opposes strongly, may become Type E → definitely avoid.

### Type E — Conflict/Trap (Avoid)
- **Criteria**: Both D1+D2 valid (>= 65) but opposing directions
- **Action**: DO NOT TRADE. This is a market indecision or potential fakeout.
- **Validation**: Log the conflict. If it persists 2+ cycles, the market is in a chop zone — reduce scanning frequency.
- **Worst case**: One side will win. Don't guess which. Wait for alignment.

## Entry Execution

### Limit Order Strategy
1. Place limit at D2 Entry level (or mid-FVG for FVG entries)
2. If not filled within 3 minutes, widen to market order
3. Never chase — if entry is already 0.5% past Entry, skip the trade

### Stop Loss Placement
- **Type C/A**: Stop below/above nearest LTF OB + 0.1% buffer
- **Type B**: Stop at the SL level provided by D2 (tightest valid level)
- Never move stop further away after entry
- Move to breakeven at TP1 hit

### Take Profit Strategy
- Take 50% at TP1, let 50% run to TP2
- Trail remaining with a 2-bar swing stop after TP1
- Close all remaining if the signal is reclassified below "Execute" action

## Risk Management

### Daily Limits
- Maximum 3 concurrent positions
- Maximum 1 Type B per session (these are aggressive scalps)
- Stop trading for the day if 2 consecutive losses
- Reset daily limits at UTC 00:00

### Position Sizing Formula
```
Actual Position = Base Capital × Type Position Mult
```

Where Type Position Mult is: C=1.0, A=0.75, B=0.35, D/E=0.0

### Maximum Drawdown
- If portfolio drops 5% in a day, stop trading
- If portfolio drops 10% in a week, reassess strategy
- Type E signals in clusters (> 3 in 1 hour) = market is chopping — stop all new entries for 30 min

## Market Evolution Rules

### Trade Only in These States
- **TREND**: Best environment. Trade Type A and C with confidence.
- **RE_ENTRY**: Acceptable for Type A (entry on pullback). Avoid Type B.
- **REVERSAL**: Only trade Type C if confidence >= 85%. Otherwise, avoid all entries.
- **DORMANT**: Avoid all entries. Wait for state change.

### Evolution Velocity
- **Improving**: Market structure strengthening — increase position size by 10%
- **Stable**: Normal conditions — use standard sizing
- **Degrading**: Structure weakening — reduce position size by 25% or skip

## Best Practices

1. **Always check Market Evolution first** — even a Type C in a REVERSAL state is risky
2. **Freshness matters** — only trade signals born in the last 30 minutes
3. **HTF alignment is king** — never override a Type D/E with "it feels right"
4. **Journal everything** — use the performance tracker to log entries, exits, and outcomes
5. **Binance Futures only** — this scanner is designed for futures markets with leverage
6. **Killzone trading** — best signals fire during London (08:00-12:00 UTC) and NY (13:00-17:00 UTC) sessions
7. **Weekend caution** — lower volume = more fakeouts. Reduce position sizes by 50% on weekends
