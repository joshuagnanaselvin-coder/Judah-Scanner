# D3 Convergence Fix — Implementation Plan

## User Requirement

> "I want to see ALL D2 signals in D3/frontend. D3 needs to find the D1 score and show it in the D3 layer. This way if something is broken in D1 or D2, I can see it. Don't reduce frontend output — use signal types to control actionability, not visibility."

**Core principle**: D3 is the *visibility layer*. Every D2 signal must appear in D3 output. Signal type classification (A/B/C/D/E/F) controls trade actionability (position multiplier, execution), NOT whether the signal is shown.

---

## Current Problem Summary

The audit identified 5 interconnected issues that make D1/D2/D3 "unprofessional":

| # | Problem | Root Cause |
|---|---------|------------|
| 1 | D2 overwrites D1's snapshot_id | Race condition in `set_snapshot_info()` |
| 2 | D1 builds snapshot twice per cycle | Two SnapshotBuilder calls in `_run_batch_scan()` → `_scan_batch()` |
| 3 | `set_snapshot_info()` has no lock protection | Missing `async with self._lock` |
| 4 | D1 never writes to EvidenceStore | D1 pipeline missing evidence emission |
| 5 | Convergence gate not implemented | Alignment levels computed but never gating decisions |
| 6 | Type F blocks visibility | `classify_signal_type()` catch-all + D3 visibility depends on type |

**Finding 6 is the user's primary concern**: the current code makes D3 output depend on signal type classification. The fix is to decouple visibility from classification.

---

## Change 1 — Decouple Visibility from Signal Type (D3 Always Shows All D2 Signals)

**File**: `backend/engines/signal_fusion.py`
**Function**: `_fuse_coin()` and `_check_and_fuse()`

### Current behavior
```python
# signal_fusion.py _fuse_coin() line 367
if sig_type is None:
    return None  # Coin disappears from D3 entirely
```

`classify_signal_type()` always returns a value (Type F catch-all at line 148), so `sig_type` is never None in practice. But Type F signals get `position_mult: 0.0`, `action: "WATCH"`, and the frontend may filter/hide them based on actionability.

### New behavior

**In `_fuse_coin()`**: Remove the `sig_type is None` early-return. Always generate a package for every D2 signal. The signal type still controls actionability fields, but visibility is unconditional.

```python
# REMOVE this block entirely:
# if sig_type is None:
#     logger.debug(f"[fusion] {coin}: no signal type ... — skipping")
#     return None
```

**Keep signal type classification** — it's still needed for:
- `position_mult` (A=0.75, B=0.35, C=1.0, D=0.0, E=0.0, F=0.0)
- `stop_mult` (sl width)
- `ttl_min` (how long the signal is relevant)
- Trade plan routing (Type B gets different ATR multipliers)
- `action` field (EXECUTE vs WATCH vs ALERT)

**But broadcast ALL packages** regardless of type. The frontend will show:
- Type A/B/C: Green/Yellow/Blue cards with `action: "EXECUTE"` — actionable
- Type D/F: Orange/Purple cards with `action: "WATCH"` — visible but not tradeable
- Type E: Red cards with `action: "ALERT"` — conflict warnings, visible

### Frontend contract change
The frontend `SIGNALS_BATCH` message will now always contain every D2 signal. The frontend should:
1. Show ALL signals in the list (no filter on `action`)
2. Visually distinguish by `signal_type_color` and `signal_type_icon` (already implemented)
3. Disable trade button for `position_mult == 0.0` (already the behavior for D/E/F types)
4. Sort by `d2_score` descending so the strongest signals appear first

### Package fields always populated (even for Type F)
```python
package = {
    "signal_type": sig_type or "F",       # Always present
    "signal_type_name": "...",              # Always present
    "signal_type_color": "#a855f7",         # Always present (purple for F)
    "signal_type_icon": "🟣",               # Always present
    "action": "WATCH",                      # F/D types → WATCH
    "position_mult": 0.0,                   # F/D/E types → 0 (non-executable)
    "d1_tier": d1_tier,                     # Always present — CRITICAL for visibility
    "d1_score": d1_score,                   # Always present — CRITICAL for visibility
    "d2_tier": d2_tier_name,                # Always present
    "d2_score": d2_score,                   # Always present
    # ... all other fields always populated
}
```

### D1 score visibility in D3
The user specifically wants D1 scores visible in D3. Currently `_fuse_coin()` already computes `d1_score` and `d1_tier` (lines 343-350). These are already in the package dict (lines 647-648). **No code change needed here** — the data is already there. The frontend just needs to render it.

**Frontend rendering**: For each card, show:
```
D1: SNIPER 72  |  D2: OPPORTUNITY 55  |  Type C 🟢 EXECUTE
```
or for Type F:
```
D1: REJECTED 0  |  D2: WATCH 38  |  Type F 🟣 WATCH
```

---

## Change 2 — Fix Snapshot Race (D1/D2/D3 Use Consistent snapshot_id)

**File**: `backend/scanner.py`, `backend/engines/ltf_engine.py`, `backend/state_store.py`

### Problem
D1 builds snapshot at line 407 → `_scan_batch()` builds another at line 188 → D2 builds its own at ltf_engine.py line 123. Each overwrites `state_store.last_snapshot_id`. D3 reads whatever was written last.

### Fix 2a — Deduplicate D1 snapshot (scanner.py)

In `_run_batch_scan()`, remove the first SnapshotBuilder call (line 407). The one in `_scan_batch(full_cycle=True)` at line 188 is the correct one — it's used by the revalidation logic that follows.

```python
# REMOVE lines 407-409 from _run_batch_scan():
# snap = SnapshotBuilder(market_data).build(self.symbols, TIMEFRAMES_HTF)
# state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)
# logger.info(f"[scan] [{self.cycle_id}] Full cycle: snapshot {snap.snapshot_id[:8]}")

# KEEP the SnapshotBuilder in _scan_batch() at line 188 — that's the one
# actually used for candle quality checks and revalidation.
```

The log line `Full cycle: snapshot {id}` can be moved to `_scan_batch()` after the SnapshotBuilder call.

### Fix 2b — Lock-protect set_snapshot_info (state_store.py)

```python
# BEFORE (no lock):
def set_snapshot_info(self, snapshot_id: str, snapshot_ts: float):
    self.last_snapshot_id = snapshot_id
    self.last_snapshot_ts = snapshot_ts

# AFTER (with lock):
async def set_snapshot_info(self, snapshot_id: str, snapshot_ts: float):
    async with self._lock:
        self.last_snapshot_id = snapshot_id
        self.last_snapshot_ts = snapshot_ts
```

**Caller changes**: All callers must be updated to `await state_store.set_snapshot_info(...)`:
- `scanner.py` line 189: `await state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)`
- `scanner.py` line 408 (removed as part of Fix 2a)
- `ltf_engine.py` line 124: `await state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)`

### Fix 2c — D2 should not overwrite D1's snapshot_id

D1 and D2 need separate snapshot tracking. Options:

**Option A (recommended)**: Add a separate field for D2 snapshots
```python
# In state_store.py, add:
self.last_d2_snapshot_id: str = ""
self.last_d2_snapshot_ts: float = 0.0

async def set_d2_snapshot_info(self, snapshot_id: str, snapshot_ts: float):
    async with self._lock:
        self.last_d2_snapshot_id = snapshot_id
        self.last_d2_snapshot_ts = snapshot_ts
```

D3 then reads:
```python
d1_snap_id = state_store.last_snapshot_id      # D1's snapshot
d2_snap_id = state_store.last_d2_snapshot_id    # D2's snapshot
```

D3 uses `d1_snap_id` to look up D1 evidence, and `d2_snap_id` to look up D2 evidence.

**Option B**: D2 doesn't write snapshot_id at all — D3 only needs D1's snapshot for evidence lookup. D2 uses its own SnapshotBuilder internally for candle quality checks but doesn't publish the ID.

**Recommendation**: Option A. It's cleaner and preserves the provenance chain for both dimensions.

---

## Change 3 — D1 Writes Evidence to EvidenceStore

**File**: `backend/engines/engine.py` (D1's pipeline), `backend/engines/signal_builder.py`

### Problem
D1 never writes EvidenceRecords. The EvidenceStore only has D2 evidence. D3's alignment evaluation and provenance chain are incomplete.

### Fix

Add evidence logging to D1's signal builder (`signal_builder.py`), similar to how D2 does it in `ltf_pipeline.py` `_log_ltf_evidence_async()`.

**In `signal_builder.py`**, after building a signal, emit EvidenceRecords for:
1. CRT pattern detected (MSB break, CHOCH, Range Break)
2. SMC structure (OB, FVG, MSB confirmed)
3. Flow trigger (VWAP reclaim, sweep, volume spike)
4. Confluence factors (MTF agreement)

```python
def _log_d1_evidence(symbol: str, timeframe: str, signal: dict,
                     crt: dict, smc: dict, flow: dict):
    """Fire-and-forget D1 evidence logging."""
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(_log_d1_evidence_async(symbol, timeframe, signal,
                                                 crt, smc, flow))
    except RuntimeError:
        pass

async def _log_d1_evidence_async(symbol, timeframe, signal, crt, smc, flow):
    from backend.evidence_store import evidence_store, next_evidence_id
    from backend.evidence_record import EvidenceCategory, EvidenceStrength, EvidenceRecord
    from backend.state_store import state_store
    from datetime import datetime, timezone

    snap_id = state_store.last_snapshot_id
    now = datetime.now(timezone.utc).timestamp()
    direction = signal.get("direction", "NEUTRAL")
    records = []

    # CRT evidence
    if crt.get("scenario") and crt["scenario"] != "RANGE":
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.MSB_BREAK,
            symbol=symbol, timeframe=timeframe,
            price=signal.get("entry", 0),
            strength=EvidenceStrength.STRONG if crt.get("crt_score", 0) >= 20 else EvidenceStrength.MODERATE,
            direction=direction,
            confidence=crt.get("crt_score", 0) / 25.0,
            candle_time=now, detected_at=now,
            source="d1_engine.crt", snapshot_id=snap_id,
            details={"scenario": crt.get("scenario"), "crt_score": crt.get("crt_score", 0)},
        ))

    # SMC evidence — OB
    ob = smc.get("ob")
    if ob and ob.get("high", 0) > 0:
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.ORDER_BLOCK,
            symbol=symbol, timeframe=timeframe,
            price=(ob.get("high", 0) + ob.get("low", 0)) / 2,
            strength=EvidenceStrength.STRONG if ob.get("strength", 0) >= 3 else EvidenceStrength.MODERATE,
            direction=direction,
            confidence=min(ob.get("strength", 0) / 5, 1.0),
            candle_time=now, detected_at=now,
            source="d1_engine.smc", snapshot_id=snap_id,
            details={"ob_type": ob.get("type", ""), "strength": ob.get("strength", 0)},
        ))

    # SMC evidence — FVG
    fvg = smc.get("fvg")
    if fvg and fvg.get("top", 0) > 0:
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.FAIR_VALUE_GAP,
            symbol=symbol, timeframe=timeframe,
            price=(fvg.get("top", 0) + fvg.get("bottom", 0)) / 2,
            strength=EvidenceStrength.STRONG if fvg.get("proximity", 999) <= 1.0 else EvidenceStrength.MODERATE,
            direction=direction,
            confidence=0.7,
            candle_time=now, detected_at=now,
            source="d1_engine.smc", snapshot_id=snap_id,
            details={"fvg_type": fvg.get("type", ""), "proximity": fvg.get("proximity", 999)},
        ))

    # Flow evidence
    if flow.get("boost", 0) > 10:
        records.append(EvidenceRecord(
            evidence_id=next_evidence_id(symbol),
            category=EvidenceCategory.VOLUME_PROFILE,
            symbol=symbol, timeframe=timeframe,
            price=signal.get("entry", 0),
            strength=EvidenceStrength.MODERATE,
            direction=direction,
            confidence=min(flow.get("boost", 0) / 25, 1.0),
            candle_time=now, detected_at=now,
            source="d1_engine.flow", snapshot_id=snap_id,
            details={"boost": flow.get("boost", 0), "triggers": [t.get("name") for t in flow.get("triggers", [])[:3]]},
        ))

    if records:
        for rec in records:
            await evidence_store.append(rec)
        logger.debug(f"[d1_evidence] Logged {len(records)} evidence records for {symbol} {timeframe}")
```

**Call from `signal_builder.py`** after signal construction:
```python
# At the end of build_signal(), after the signal dict is complete:
if signal:
    _log_d1_evidence(symbol, timeframe, signal, crt, smc, flow)
```

### Evidence write frequency
D1 runs every 15 seconds. Each signal produces ~3-4 EvidenceRecords. With ~20-30 D1 signals per cycle, this is ~60-120 records per cycle × 4 cycles/minute = ~240-480 records/minute. Well within the EvidenceStore caps (2000 total, 50 per coin).

---

## Change 4 — Implement Convergence Gate (Alignment Levels Filter Tradeability, Not Visibility)

**File**: `backend/engines/signal_fusion.py`
**Function**: `_fuse_coin()`

### Current behavior
Alignment is computed but never gates the signal. Every signal gets `tradeable: True/False` but still broadcasts.

### New behavior
The convergence gate affects the `action` field and `position_mult`, NOT visibility:

```python
# After alignment_result is computed (currently line 555-565):

# Convergence gate: adjust actionability based on alignment
if alignment_result.level == AlignmentLevel.STRONG_ALIGNMENT:
    # Full position size — both dimensions agree
    position_mult = TYPE_POSITION_MULT.get(sig_type, 0.0)
    action = type_info.get("action", "EXECUTE")
elif alignment_result.level == AlignmentLevel.PARTIAL_ALIGNMENT:
    # Reduced position size — some disagreement
    base_mult = TYPE_POSITION_MULT.get(sig_type, 0.0)
    position_mult = base_mult * 0.5  # Half size
    action = "EXECUTE" if base_mult > 0 else "WATCH"
elif alignment_result.level == AlignmentLevel.CONFLICT:
    # D1 and D2 disagree — watch only, never execute
    position_mult = 0.0
    action = "ALERT"  # Visible but flagged as conflict
elif alignment_result.level == AlignmentLevel.INSUFFICIENT_EVIDENCE:
    # Not enough data — watch only
    position_mult = 0.0
    action = "WATCH"
elif alignment_result.level == AlignmentLevel.DEGRADED:
    # Poor data quality — watch only
    position_mult = 0.0
    action = "WATCH"
```

**Key**: The signal STILL appears in the broadcast. It just has `position_mult: 0.0` and a different `action` field. The frontend shows it but disables trading.

### Package field updates
```python
package["position_mult"] = position_mult  # convergence-adjusted
package["action"] = action                 # convergence-adjusted
package["alignment_level"] = alignment_level  # already present
package["tradeable"] = position_mult > 0 and risk_decision.verdict.value == "APPROVED"
```

---

## Change 5 — D3 Per-Signal Change Detection

**File**: `backend/engines/signal_fusion.py`
**Function**: `_check_and_fuse()`

### Current behavior
D3 iterates ALL D2 signals every cycle, calls `_fuse_coin()` for each, broadcasts the full batch regardless of changes.

### New behavior
Track previous package hash per coin. Only re-fuse if something changed.

```python
class FusionEngine:
    def __init__(self):
        # ... existing fields ...
        self._prev_package_hashes: dict[str, str] = {}

async def _check_and_fuse(self):
    # ... existing change detection for d1_changed / d2_changed ...

    d2_all = state_store.get_all_d2_signals()
    d2_coins = set(d2_all.keys())

    results = []
    type_e_alerts = []
    changed_coins = []

    for coin in d2_coins:
        pkg = await self._fuse_coin(coin, type_e_alerts)
        if not pkg:
            continue

        # Per-signal change detection
        pkg_hash = _package_hash(pkg)
        if self._prev_package_hashes.get(coin) == pkg_hash:
            continue  # No change — skip this coin

        self._prev_package_hashes[coin] = pkg_hash
        changed_coins.append(coin)
        results.append(pkg)

    # Prune hashes for coins that no longer have D2 signals
    for coin in list(self._prev_package_hashes.keys()):
        if coin not in d2_coins:
            del self._prev_package_hashes[coin]

    if results:
        logger.info(f"[fusion] Broadcasting {len(results)}/{len(d2_coins)} changed signals "
                     f"(skipped {len(d2_coins) - len(changed_coins)} unchanged)")

    await broadcast({"type": "SIGNALS_BATCH", "signals": results})
```

```python
def _package_hash(pkg: dict) -> str:
    """Compute a stable hash of the package fields that matter for display."""
    import hashlib
    key_fields = (
        f"{pkg.get('signal_type','')}"
        f"{pkg.get('d1_tier','')}{pkg.get('d1_score',0):.0f}"
        f"{pkg.get('d2_tier','')}{pkg.get('d2_score',0):.0f}"
        f"{pkg.get('action','')}{pkg.get('position_mult',0):.2f}"
        f"{pkg.get('alignment_level','')}"
        f"{pkg.get('entry',0):.4f}{pkg.get('sl',0):.4f}"
        f"{pkg.get('rr1',0):.2f}{pkg.get('expected_value_pct',0):.2f}"
    )
    return hashlib.md5(key_fields.encode()).hexdigest()[:12]
```

### Impact
- Normal cycles (no changes): 0 broadcasts, 0 WebSocket messages
- D2 update cycles: only the changed coins broadcast (~5-15 coins instead of 25-40)
- D1 update cycles: all D2 signals may need re-fusion (D1 tier changes affect alignment)

---

## Change 6 — Fix D2 Overwrite of D1 Evidence (Separate Snapshot Tracking)

**File**: `backend/engines/ltf_engine.py`, `backend/state_store.py`

### Current behavior
D2 calls `state_store.set_snapshot_info()` which overwrites D1's snapshot_id. D3 reads D2's snapshot_id when looking up evidence.

### Fix
As described in Fix 2c, add `set_d2_snapshot_info()` to StateStore:

```python
# In state_store.py:
self.last_d2_snapshot_id: str = ""
self.last_d2_snapshot_ts: float = 0.0

async def set_d2_snapshot_info(self, snapshot_id: str, snapshot_ts: float):
    async with self._lock:
        self.last_d2_snapshot_id = snapshot_id
        self.last_d2_snapshot_ts = snapshot_ts
```

In `ltf_engine.py` line 124:
```python
# BEFORE:
state_store.set_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)

# AFTER:
await state_store.set_d2_snapshot_info(snap.snapshot_id, snap.snapshot_timestamp)
```

In `signal_fusion.py` `_fuse_coin()` line 570:
```python
# BEFORE:
snap_id = state_store.last_snapshot_id  # D2's ID (wrong for D1 evidence)
all_evidence = evidence.get_for_snapshot_sync(snap_id)

# AFTER:
d1_snap_id = state_store.last_snapshot_id
d2_snap_id = state_store.last_d2_snapshot_id

# D1 evidence from D1's snapshot
all_d1_evidence = evidence.get_for_snapshot_sync(d1_snap_id) if d1_snap_id else {}
# D2 evidence from D2's snapshot
all_d2_evidence = evidence.get_for_snapshot_sync(d2_snap_id) if d2_snap_id else {}

d1_evidence_ids = [
    r.evidence_id for by_cat in all_d1_evidence.get(coin, {}).values() for r in by_cat
    if r.timeframe in ("1H", "4H", "1D")
]
d2_evidence_ids = [
    r.evidence_id for by_cat in all_d2_evidence.get(coin, {}).values() for r in by_cat
    if r.timeframe == "15M"
]
```

---

## Change 7 — D3 Shows D1 Score Prominently in Each Signal

**File**: `backend/engines/signal_fusion.py` (package dict), frontend display

### Current behavior
D1 score is in the package (`d1_score`, `d1_tier`) but the frontend may not display it prominently.

### Fix — Backend (already done)
The package already contains:
```python
"d1_tier": d1_tier,           # e.g. "SNIPER"
"d1_score": round(d1_score, 1),  # e.g. 72.0
"d2_tier": d2_tier_name,       # e.g. "OPPORTUNITY"
"d2_score": round(d2_score, 1),  # e.g. 55.0
```

### Fix — Frontend display contract
Each signal card should render:
```
┌─────────────────────────────────────┐
│ BTCUSDT  15M                    🟢 C │
│                                         │
│ D1: SNIPER ████████████ 72            │
│ D2: OPPORTUNITY ████████░ 55          │
│                                         │
│ Dir: BULLISH  |  RR: 2.8:1            │
│ Alignment: STRONG ████████████ 0.85    │
│ Action: EXECUTE  |  Position: 100%     │
└─────────────────────────────────────┘
```

For Type F (non-actionable):
```
┌─────────────────────────────────────┐
│ KAVAUSDT  15M                    🟣 F │
│                                         │
│ D1: REJECTED ░░░░░░░░░░░ 0            │
│ D2: WATCH ████░░░░░░░░ 38             │
│                                         │
│ Dir: BEARISH  |  RR: 1.8:1            │
│ Alignment: INSUFFICIENT ░░░░░░░░░ 0.1  │
│ Action: WATCH  |  Position: 0%         │
└─────────────────────────────────────┘
```

The key insight: **D1: REJECTED with D2: WATCH is VALID data** — it means D2 found a LTF momentum setup that D1's HTF doesn't confirm. This is a Type B opportunity. The user can SEE this and evaluate it manually. If D1 were actually broken (e.g., always showing REJECTED 0 for every coin), the user would see it immediately because every coin would show `D1: REJECTED ░░░░░░░░░░░ 0`.

---

## Change 8 — Remove Deprecated asyncio.get_event_loop()

**File**: `backend/engines/signal_fusion.py` line 174

```python
# BEFORE:
loop = asyncio.get_event_loop()

# AFTER:
loop = asyncio.get_running_loop()
```

---

## Change 9 — Fix Duplicate Timestamp Reset in clear()

**File**: `backend/state_store.py` lines 331-335

```python
# BEFORE:
self.last_d1_scan = 0.0
self.last_d2_scan = 0.0
self.last_d3_fusion = 0.0
self.last_d1_scan = 0.0    # duplicate
self.last_d2_scan = 0.0    # duplicate

# AFTER:
self.last_d1_scan = 0.0
self.last_d2_scan = 0.0
self.last_d3_fusion = 0.0
self.last_regime_update = 0.0
```

---

## Change 10 — Fix D1 TTL Configuration Confusion

**File**: `backend/config.py`

```python
# BEFORE:
D1_TTL_SECONDS = 120  # Unused, confusing

# AFTER:
# Remove D1_TTL_SECONDS (120s) — it conflicts with SIGNAL_TTL_MINUTES (240min)
# and is not referenced anywhere in the codebase.
# D1 signals use SIGNAL_TTL_MINUTES (240 min) via signal_store._clean_expired().
```

No code references `D1_TTL_SECONDS`. Safe to remove.

---

## Implementation Order

| Priority | Change | Files | Risk |
|----------|--------|-------|------|
| 1 | Fix 2a: Deduplicate D1 snapshot | scanner.py | Low — removes dead code |
| 1 | Fix 2b: Lock set_snapshot_info | state_store.py | Low — adds lock |
| 1 | Fix 2c: Separate D2 snapshot tracking | state_store.py, ltf_engine.py, signal_fusion.py | Medium — new field |
| 2 | Change 1: Decouple visibility from signal type | signal_fusion.py | Medium — changes broadcast behavior |
| 3 | Change 6: Fix D2 evidence routing | signal_fusion.py | Medium — evidence IDs now correct |
| 4 | Change 3: D1 writes evidence | engine.py / signal_builder.py | Medium — new async writes |
| 4 | Change 4: Convergence gate adjusts actionability | signal_fusion.py | Medium — changes position_mult logic |
| 5 | Change 5: Per-signal change detection | signal_fusion.py | Medium — new state tracking |
| 6 | Change 7: Frontend D1 score display | Frontend | Low — display only |
| 7 | Change 8: Fix get_event_loop | signal_fusion.py | Low — one-line fix |
| 7 | Change 9: Fix duplicate reset | state_store.py | Low — cleanup |
| 7 | Change 10: Remove dead config | config.py | Low — cleanup |

### Recommended execution order:
1. Fix 2a + 2b + 2c (snapshot race — unblocks everything else)
2. Change 1 (visibility decoupling — user's primary ask)
3. Change 6 (evidence routing — makes alignment meaningful)
4. Change 3 (D1 evidence writes — completes the evidence layer)
5. Change 4 (convergence gate — makes alignment actionable)
6. Change 5 (change detection — performance optimization)
7. Changes 7-10 (hygiene)

### Testing strategy
After each change:
1. Start scanner, verify it boots without errors
2. Check `/api/debug-fusion` — verify D1 count, D2 count, decision count, overlap
3. Check `/api/health/detail` — verify D1/D2/D3 timestamps are live
4. Connect frontend — verify ALL D2 signals appear as cards
5. Verify D1 score is visible on each card
6. Check logs for `[fusion]` entries — verify alignment levels are computed
7. Verify Type F cards appear but have `position_mult: 0.0` and `action: "WATCH"`

### Rollback plan
Each change is independently reversible:
- Changes 1-5: revert the specific function edits
- Change 5: remove `_prev_package_hashes` dict and `_package_hash()` function
- Changes 7-10: trivial reverts

---

## Expected Outcome

After all changes:

1. **All D2 signals visible in D3**: User sees every coin D2 scans, with D1 score overlaid. If D1 breaks (all REJECTED/0), user sees it immediately.

2. **Correct snapshot tracking**: D1 and D2 have separate snapshot IDs. D3 reads the correct snapshot for each dimension's evidence. AlignmentEngine has full D1 + D2 evidence.

3. **Convergence gate is meaningful**: STRONG_ALIGNMENT → full position, PARTIAL → half size, CONFLICT/INSUFFICIENT → 0 position. But ALL signals are visible.

4. **D3 broadcasts only on change**: Normal cycles produce no WebSocket traffic. Only changed signals broadcast, reducing frontend noise.

5. **EvidenceStore has both D1 and D2 evidence**: AlignmentEngine can compare structural evidence from both dimensions, producing meaningful alignment scores and rationales.

6. **Frontend shows professional output**: Every card has D1 tier/score, D2 tier/score, alignment level, action, position size, EV. No missing data. No phantom signals. Clear visual distinction between tradeable (A/B/C) and watch-only (D/E/F) signals.

---

## Files Modified

| File | Changes |
|------|---------|
| `backend/engines/signal_fusion.py` | Changes 1, 4, 5, 6, 8 |
| `backend/scanner.py` | Fix 2a |
| `backend/engines/ltf_engine.py` | Fix 2c |
| `backend/state_store.py` | Fix 2b, 2c, 9 |
| `backend/engines/signal_builder.py` | Change 3 |
| `backend/config.py` | Change 10 |
| Frontend (index.html / static JS) | Change 7 |
