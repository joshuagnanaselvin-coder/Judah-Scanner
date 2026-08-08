# Frontend-Backend Integration Fix Plan

## Problem Summary

The backend is fully functional but the frontend receives no data because of
data-shape mismatches between what the backend sends and what the frontend expects.
Six issues were identified — sorted by severity.

## Issues & Fixes

### P0 — CRITICAL: `data.signals` is a dict, frontend expects an array

**Root cause:** Backend sends `dict[str, dict]` keyed by coin name. Frontend calls
`.sort()`, `.filter()`, `.map()` on it — all throw `TypeError`.

**Files to change:**
1. `backend/ws_hub.py:37` — wrap in `list(store.get_all_decisions().values())`
2. `backend/main.py:132` — same wrap in `/api/fusion` endpoint
3. `backend/main.py:117` — verify `/api/signals` returns list

**Estimated impact:** Fixes all signal rendering. Without this, nothing works.

---

### P1 — HIGH: Stats field mismatch `d3_fusion` vs `d3_decisions`

**Root cause:** `StateStore.get_stats()` returns key `d3_decisions`. Frontend reads
`stats.d3_fusion`. Same mismatch in `/api/health`.

**Files to change:**
1. `backend/state_store.py:183-191` — rename key to `d3_fusion`
2. `backend/main.py:244-266` — rename key in `/api/health` response

**Estimated impact:** Fusion counter in header shows correct count.

---

### P2 — HIGH: Missing `last_d3_fusion` timestamp

**Root cause:** StateStore tracks `last_d1_scan` and `last_d2_scan` but no
`last_d3_fusion`. Frontend activity bar for D3 always shows idle.

**Files to change:**
1. `backend/state_store.py` — add `last_d3_fusion: float = 0.0` field
2. `backend/engines/signal_fusion.py` — call `state_store.set_timestamp("last_d3_fusion")`
   when fusion completes a cycle

**Estimated impact:** D3 activity indicator shows scanning/done status.

---

### P3 — MEDIUM: Missing `alignment` object in signal package

**Root cause:** Frontend expects `s.alignment.alignment_score` and
`s.alignment.components.{direction_agreement,htf_ob_alignment,htf_zone_alignment,htf_liquidity_proximity}`.
Backend never includes an `alignment` key.

**Files to change:**
1. `backend/engines/signal_fusion.py` — after computing D1/D2, build alignment dict with
   direction agreement, HTF OB alignment, zone alignment, and liquidity proximity checks.

**Estimated impact:** Alignment strip shows real data instead of all red X marks.

---

### P4 — MEDIUM: `alignment_score` not passed to MarketEvolutionEngine

**Root cause:** `me_evaluate()` called without `alignment_score` parameter, so it
defaults to 0. This artificially deflates `evolutionConfidence`.

**Files to change:**
1. `backend/engines/signal_fusion.py:430-435` — pass computed `alignment_score`

**Estimated impact:** Confidence scores are more accurate.

---

### P5 — LOW: D1 structure missing `premium_discount` and `session`

**Root cause:** Frontend reads `d1s.premium_discount` and `d1s.session` from
`d1_structure`. Backend only populates these in `d2_structure`.

**Files to change:**
1. `backend/engines/signal_fusion.py:303-337` — add `premium_discount` and
   `session` to D1 structure dict

**Estimated impact:** D1 PD and Session tags display correctly.

---

## Implementation Order

1. P0 — Fix data shape (dict → list)
2. P1 — Fix stats key names
3. P2 — Add `last_d3_fusion` timestamp
4. P3 — Add alignment object to signal package
5. P4 — Pass alignment_score to ME engine
6. P5 — Add missing D1 structure fields

## Verification Steps

After all fixes:
1. Start backend: `cd backend && python -m main`
2. Open browser to `http://localhost:8000`
3. Verify WebSocket connects (green "Live" pill)
4. Verify signal cards appear within 30 seconds
5. Verify header counters show non-zero counts
6. Verify D3 activity bar shows "Scanning" then "Done"
7. Verify alignment strip shows mixed check/X marks
8. Check browser console for no errors
