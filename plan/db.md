# Judah Scanner — Persistence & Analytics Plan

## Problem Statement

The scanner runs 3D pipeline (D1 HTF → D2 LTF → D3 Fusion) over 529 crypto pairs.
All trading intelligence — signal outcomes, state transitions, Bayesian calibration —
lives **exclusively in process RAM**. A restart, deploy, or crash destroys everything.

Current JSON files (`data/state_store.json`, `data/d2_signals.json`) are **dead artifacts**.
No running code reads or writes them. They're leftovers from an older persistence attempt.

---

## What Gets Lost on Restart (Priority Order)

| # | Data | Current Location | Volume | Value |
|---|---|---|---|---|
| 1 | Signal outcomes (WIN/LOSS/TIMEOUT) | `performance_tracker.completed` (RAM, 1000-entry ring) | ~1K/day | **Critical** — trading journal |
| 2 | Bayesian calibration (alpha/beta per state+type) | `confidence.py:_bayes_tracker` (RAM, 500 entries) | ~500 total | **Critical** — learned edge calibration |
| 3 | State transitions (evolution history) | `market_evolution/history.py` (RAM, 20/coin) | ~5K/day | **High** — evolution story |
| 4 | Evidence records (OB/FVG/MSB/liq) | `evidence_store._records` (RAM, 2000 ring) | ~2K active | **Medium** — institutional evidence trail |
| 5 | D1 tier snapshots (historical) | `state_store.d1_tiers` (RAM, not logged) | ~15K/day | **Medium** — tier history per coin |
| 6 | Pipeline timing (P50/P95/P99) | `performance_monitor._stages` (RAM) | ~300/day | **Low** — operational debug |
| 7 | Full signal payloads | WebSocket broadcast (ephemeral) | ~1K/day | **Low** — richer than outcomes |

---

## Decision: SQLite (Not PostgreSQL)

| Factor | SQLite | PostgreSQL |
|---|---|---|
| Write rate needed | <1 row/sec | Same |
| RAM footprint | ~30 MB | ~150 MB |
| Setup complexity | Zero (one file) | Service + config |
| Backup | `cp data.db` | pg_dump |
| Query power needed | Joins, time-range, aggregates | Same, no benefit |
| Async Python | `aiosqlite` | `asyncpg` |
| VPS headroom | 8GB box at 560MB used | 8GB box at 700MB+ used |

**Verdict: SQLite with WAL mode.** The 8GB VPS is wildly over-provisioned for this workload.
PostgreSQL adds operational complexity for zero benefit at <1 row/sec write rate.

---

## Database Schema

```sql
-- Table 1: Signal Outcomes (the trading journal)
-- Every signal that reaches a terminal state gets a row here.
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT,
    direction       TEXT,
    tier            TEXT,           -- SNIPER/OPPORTUNITY/WATCH/WEAK/REJECTED
    signal_type     TEXT,           -- A/B/C/D/E
    d1_tier         TEXT,
    d1_score        REAL,
    d2_tier         TEXT,
    d2_score        REAL,
    entry_price     REAL,
    sl_price        REAL,
    tp_price        REAL,
    rr              REAL,
    session         TEXT,
    scenario        TEXT,
    outcome         TEXT,           -- WIN / LOSS / TIMEOUT
    pnl_pct         REAL,
    opened_at       TEXT,
    closed_at       TEXT,
    engine          TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outcomes_symbol ON signal_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_outcomes_outcome ON signal_outcomes(outcome);
CREATE INDEX IF NOT EXISTS idx_outcomes_signal_type ON signal_outcomes(signal_type);
CREATE INDEX IF NOT EXISTS idx_outcomes_created ON signal_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_outcomes_tier ON signal_outcomes(tier);

-- Table 2: Bayesian Calibration (the learning memory)
-- Alpha/beta per (state_name, signal_type) pair.
CREATE TABLE IF NOT EXISTS bayes_calibration (
    key         TEXT PRIMARY KEY,   -- "Expansion:Type_A"
    alpha       REAL NOT NULL,
    beta        REAL NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Table 3: State Transitions (evolution history)
-- Every market evolution state change per coin.
CREATE TABLE IF NOT EXISTS state_transitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    coin                TEXT NOT NULL,
    ts                  REAL NOT NULL,       -- unix timestamp
    state               TEXT NOT NULL,
    spiral              TEXT,
    direction           TEXT,
    d1_score            REAL,
    d2_score            REAL,
    momentum_velocity    REAL,
    evolution           TEXT
);

CREATE INDEX IF NOT EXISTS idx_transitions_coin ON state_transitions(coin);
CREATE INDEX IF NOT EXISTS idx_transitions_ts ON state_transitions(ts);

-- Table 4: Decisions (D3 fusion log)
-- Every D3 fusion decision per coin per cycle.
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    ts              TEXT NOT NULL,
    signal_type     TEXT,           -- A/B/C/D/E
    action          TEXT,           -- EXECUTE/WATCH/ALERT
    position_mult   REAL,
    stop_mult       REAL,
    ev_pct          REAL,
    confidence      INTEGER,
    d1_tier         TEXT,
    d1_score        REAL,
    d2_tier         TEXT,
    d2_score        REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_coin ON decisions(coin);
CREATE INDEX IF NOT EXISTS idx_decisions_signal_type ON decisions(signal_type);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
```

---

## Implementation Phases

### Phase 1: Database Module (foundation)
- Create `backend/db.py` — SQLite connection management, schema creation, async wrapper
- Delete dead JSON files (`data/state_store.json`, `data/d2_signals.json`)
- Add `aiosqlite` to requirements

### Phase 2: Wire signal_outcomes (highest value)
- Modify `performance_tracker.py` — add DB write alongside in-memory ring buffer
- `record()` already captures everything needed: symbol, engine, direction, tier, rr, session, outcome, scenario
- ~20 lines of change

### Phase 3: Wire bayes_calibration (saves learned edge)
- Modify `confidence.py:record_outcome()` — upsert alpha/beta into DB
- On startup, load existing calibration back into `_bayes_tracker`
- ~20 lines of change

### Phase 4: Wire state_transitions (evolution history)
- Modify `market_evolution/history.py:CoinHistory.record()` — INSERT each transition
- ~15 lines of change

### Phase 5: Wire decisions (D3 fusion log)
- Modify `signal_fusion.py:_fuse_coin()` — INSERT decision after classification
- ~15 lines of change

### Phase 6: REST API endpoints (query the DB)
- Add `/api/analytics/outcomes` — signal outcomes with filters
- Add `/api/analytics/stats` — aggregated win rate by type/tier/session
- Add `/api/analytics/evolution?coin=BTCUSDT` — state history for one coin
- Add `/api/analytics/bayes` — current Bayesian calibration table
- New file: `backend/analytics.py`

### Phase 7: Frontend analytics page
- New tab in frontend: "Analytics"
- Charts: win rate by signal type, tier distribution, evolution timeline
- Reuse existing chart infrastructure from `audit.py`

### Phase 8: Retention & maintenance
- Add VACUUM cron (quarterly)
- Add 90-day retention policy on `state_transitions` and `decisions`
- Keep `signal_outcomes` and `bayes_calibration` indefinitely

---

## File Changes Summary

| File | Action | What |
|---|---|---|
| `backend/db.py` | **CREATE** | SQLite module (connection, schema, async helpers) |
| `backend/performance_tracker.py` | **MODIFY** | Add DB write in `record()` |
| `backend/market_evolution/confidence.py` | **MODIFY** | Add DB upsert in `record_outcome()` + load on import |
| `backend/market_evolution/history.py` | **MODIFY** | Add DB INSERT in `CoinHistory.record()` |
| `backend/engines/signal_fusion.py` | **MODIFY** | Add DB INSERT in `_fuse_coin()` |
| `backend/analytics.py` | **CREATE** | REST API endpoints for analytics queries |
| `backend/main.py` | **MODIFY** | Register analytics router, fix log rotation |
| `frontend/app.js` | **MODIFY** | Add analytics tab + chart rendering |
| `requirements.txt` | **MODIFY** | Add `aiosqlite` |
| `data/state_store.json` | **DELETE** | Dead artifact |
| `data/d2_signals.json` | **DELETE** | Dead artifact |
| `plan/` | **CREATE** | This file |
| `plan/db.md` | **CREATE** | This file |

---

## Rollout Order

```
Phase 1 (db.py)         → standalone, no side effects
Phase 2 (outcomes)      → adds DB writes, in-memory ring still works
Phase 3 (bayes)         → saves calibration across restarts
Phase 4 (transitions)   → saves evolution history
Phase 5 (decisions)     → saves D3 fusion log
Phase 6 (API)           → read-only, no risk to scanner
Phase 7 (frontend)      → new tab, no changes to existing views
Phase 8 (retention)     → cron-based cleanup
```

Each phase is independently deployable. None modifies the core scanning logic.

---

## Estimated Effort

| Phase | Lines changed | Risk |
|---|---|---|
| 1 | ~80 | None (new file) |
| 2 | ~20 | Low (additive write) |
| 3 | ~20 | Low (additive write + load) |
| 4 | ~15 | Low (additive write) |
| 5 | ~15 | Low (additive write) |
| 6 | ~100 | None (read-only API) |
| 7 | ~150 | Low (new tab) |
| 8 | ~30 | None (cron) |
| **Total** | **~430** | **Minimal** |
