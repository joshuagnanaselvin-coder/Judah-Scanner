"""Judah Scanner — SQLite persistence layer.

Stores signal outcomes, Bayesian calibration, state transitions,
and D3 decisions that would otherwise be lost on restart.

Uses aiosqlite (async SQLite with WAL mode) with a connection pool
to avoid thread-start issues on Windows.
"""
import asyncio
import logging
import os
import threading
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("judah.db")

# ── Paths ──────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DB_PATH = os.path.join(_DATA_DIR, "judah.db")

# ── Connection Pool ────────────────────────────────────────────
# aiosqlite on Windows can fail with "threads can only be started once"
# when connections are opened from different event-loop invocations.
# Fix: pool connections per-thread and reuse them across calls.

_POOL_SIZE = 4
_pool: asyncio.LifoQueue | None = None
_pool_lock: asyncio.Lock | None = None
_pool_thread_id: int | None = None


async def _ensure_pool() -> None:
    """Create the connection pool if it doesn't exist for this thread."""
    global _pool, _pool_lock, _pool_thread_id

    current = threading.get_ident()
    if _pool_thread_id == current and _pool is not None:
        return

    _pool_thread_id = current
    _pool_lock = asyncio.Lock()
    _pool = asyncio.LifoQueue(maxsize=_POOL_SIZE)
    for _ in range(_POOL_SIZE):
        conn = await aiosqlite.connect(_DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await _pool.put(conn)


async def _get_conn() -> aiosqlite.Connection:
    """Acquire a connection from the pool."""
    await _ensure_pool()
    try:
        return await asyncio.wait_for(_pool.get(), timeout=10.0)
    except asyncio.TimeoutError:
        # Pool exhausted — fall back to a one-shot connection
        conn = await aiosqlite.connect(_DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        return conn


async def _release_conn(conn: aiosqlite.Connection) -> None:
    """Return a connection to the pool (or close it if pool is full)."""
    if _pool is not None and not _pool.full():
        await _pool.put(conn)
    else:
        try:
            await conn.close()
        except Exception:
            pass


class _PooledConn:
    """Async context manager for pooled connections."""

    def __init__(self):
        self.conn: aiosqlite.Connection | None = None

    async def __aenter__(self):
        self.conn = await _get_conn()
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.conn is not None:
            await _release_conn(self.conn)
            self.conn = None


# ── Schema ─────────────────────────────────────────────────────

_SCHEMA = """
-- Table 1: Signal Outcomes (trading journal)
-- Every signal that reaches a terminal state (WIN/LOSS/TIMEOUT).
CREATE TABLE IF NOT EXISTS signal_outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       TEXT,
    symbol          TEXT NOT NULL,
    timeframe       TEXT,
    direction       TEXT,
    tier            TEXT,
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

CREATE INDEX IF NOT EXISTS idx_outcomes_symbol   ON signal_outcomes(symbol);
CREATE INDEX IF NOT EXISTS idx_outcomes_outcome  ON signal_outcomes(outcome);
CREATE INDEX IF NOT EXISTS idx_outcomes_sig_type ON signal_outcomes(signal_type);
CREATE INDEX IF NOT EXISTS idx_outcomes_tier     ON signal_outcomes(tier);
CREATE INDEX IF NOT EXISTS idx_outcomes_created  ON signal_outcomes(created_at);

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
    ts                  REAL NOT NULL,
    state               TEXT NOT NULL,
    spiral              TEXT,
    direction           TEXT,
    d1_score            REAL,
    d2_score            REAL,
    momentum_velocity    REAL,
    evolution           TEXT
);

CREATE INDEX IF NOT EXISTS idx_trans_coin ON state_transitions(coin);
CREATE INDEX IF NOT EXISTS idx_trans_ts   ON state_transitions(ts);

-- Table 4: Decisions (D3 fusion log)
-- Every D3 fusion decision per coin per cycle.
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    ts              TEXT NOT NULL,
    signal_type     TEXT,           -- A/B/C/D/E
    action          TEXT,           -- EXECUTE / WATCH / ALERT
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

CREATE INDEX IF NOT EXISTS idx_dec_coin     ON decisions(coin);
CREATE INDEX IF NOT EXISTS idx_dec_sig_type ON decisions(signal_type);
CREATE INDEX IF NOT EXISTS idx_dec_ts       ON decisions(ts);
"""


# ── Init ────────────────────────────────────────────────────────

async def init_schema() -> None:
    """Create all tables if they don't exist. Safe to call multiple times."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    async with _PooledConn() as conn:
        await conn.executescript(_SCHEMA)
    logger.info("[db] Schema initialized at %s", _DB_PATH)


async def _write_lock():
    """Acquire the global write lock (no-op if pool not initialized yet)."""
    if _pool_lock is not None:
        await _pool_lock.acquire()


def _write_unlock():
    """Release the global write lock."""
    if _pool_lock is not None:
        _pool_lock.release()


# ── Signal Outcomes ─────────────────────────────────────────────

async def insert_outcome(row: dict) -> int | None:
    """Insert a signal outcome record. Returns the new row id."""
    try:
        async with _PooledConn() as conn:
            cur = await conn.execute(
                """
                INSERT INTO signal_outcomes
                    (signal_id, symbol, timeframe, direction, tier, signal_type,
                     d1_tier, d1_score, d2_tier, d2_score,
                     entry_price, sl_price, tp_price, rr,
                     session, scenario, outcome, pnl_pct,
                     opened_at, closed_at, engine)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row.get("signal_id"),
                    row.get("symbol"),
                    row.get("timeframe"),
                    row.get("direction"),
                    row.get("tier"),
                    row.get("signal_type"),
                    row.get("d1_tier"),
                    row.get("d1_score"),
                    row.get("d2_tier"),
                    row.get("d2_score"),
                    row.get("entry_price"),
                    row.get("sl_price"),
                    row.get("tp_price"),
                    row.get("rr"),
                    row.get("session"),
                    row.get("scenario"),
                    row.get("outcome"),
                    row.get("pnl_pct"),
                    row.get("opened_at"),
                    row.get("closed_at"),
                    row.get("engine"),
                ),
            )
            await conn.commit()
            return cur.lastrowid
    except Exception:
        logger.exception("[db] Failed to insert outcome for %s", row.get("symbol"))
        return None


# ── Bayesian Calibration ───────────────────────────────────────

async def upsert_bayes(key: str, alpha: float, beta: float) -> None:
    """Upsert a Bayesian calibration entry."""
    try:
        async with _PooledConn() as conn:
            await conn.execute(
                """
                INSERT INTO bayes_calibration (key, alpha, beta, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    alpha = excluded.alpha,
                    beta = excluded.beta,
                    updated_at = excluded.updated_at
                """,
                (key, alpha, beta, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()
    except Exception:
        logger.exception("[db] Failed to upsert bayes key=%s", key)


async def load_all_bayes() -> dict[str, dict]:
    """Load all Bayesian entries from DB into a dict keyed by "State:Type"."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute("SELECT key, alpha, beta FROM bayes_calibration") as cur:
                rows = await cur.fetchall()
        return {row["key"]: {"alpha": row["alpha"], "beta": row["beta"]} for row in rows}
    except Exception:
        logger.exception("[db] Failed to load bayes calibration")
        return {}


# ── State Transitions ──────────────────────────────────────────

async def insert_transition(row: dict) -> int | None:
    """Insert a state transition record."""
    try:
        await _write_lock()
        try:
            async with _PooledConn() as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO state_transitions
                        (coin, ts, state, spiral, direction,
                         d1_score, d2_score, momentum_velocity, evolution)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row.get("coin"),
                        row.get("ts"),
                        row.get("state"),
                        row.get("spiral"),
                        row.get("direction"),
                        row.get("d1_score"),
                        row.get("d2_score"),
                        row.get("momentum_velocity"),
                        row.get("evolution"),
                    ),
                )
                await conn.commit()
                return cur.lastrowid
        finally:
            _write_unlock()
    except Exception:
        logger.exception("[db] Failed to insert transition for %s", row.get("coin"))
        return None


# ── Decisions ──────────────────────────────────────────────────

async def insert_decision(row: dict) -> int | None:
    """Insert a D3 fusion decision record."""
    try:
        await _write_lock()
        try:
            async with _PooledConn() as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO decisions
                        (coin, ts, signal_type, action, position_mult, stop_mult,
                         ev_pct, confidence, d1_tier, d1_score, d2_tier, d2_score)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        row.get("coin"),
                        row.get("ts"),
                        row.get("signal_type"),
                        row.get("action"),
                        row.get("position_mult"),
                        row.get("stop_mult"),
                        row.get("ev_pct"),
                        row.get("confidence"),
                        row.get("d1_tier"),
                        row.get("d1_score"),
                        row.get("d2_tier"),
                        row.get("d2_score"),
                    ),
                )
                await conn.commit()
                return cur.lastrowid
        finally:
            _write_unlock()
    except Exception:
        logger.exception("[db] Failed to insert decision for %s", row.get("coin"))
        return None


# ── Retention ──────────────────────────────────────────────────

async def prune_old(older_than_days: int = 14) -> dict:
    """Delete records older than `older_than_days` from hot tables.

    Prunes state_transitions, decisions, and signal_outcomes.
    Keeps bayes_calibration indefinitely.
    Runs VACUUM to reclaim disk space.
    Also trims oversized log files.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    outcomes_cutoff = datetime.now(timezone.utc).timestamp() - (7 * 86400)  # 7 days for outcomes
    outcomes_iso = datetime.fromtimestamp(outcomes_cutoff, tz=timezone.utc).isoformat()
    results = {}
    log_bytes_freed = 0

    try:
        async with _PooledConn() as conn:
            cur = await conn.execute(
                "DELETE FROM state_transitions WHERE ts < ?", (cutoff,)
            )
            results["state_transitions_deleted"] = cur.rowcount

            cur = await conn.execute(
                "DELETE FROM decisions WHERE created_at < ?", (cutoff_iso,)
            )
            results["decisions_deleted"] = cur.rowcount

            cur = await conn.execute(
                "DELETE FROM signal_outcomes WHERE created_at < ?", (outcomes_iso,)
            )
            results["outcomes_deleted"] = cur.rowcount

            await conn.execute("VACUUM")
            await conn.commit()

        # Trim log files if they exceed 50 MB
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        if os.path.isdir(log_dir):
            max_log_size = 50 * 1024 * 1024  # 50 MB
            for fname in os.listdir(log_dir):
                fpath = os.path.join(log_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                try:
                    fsize = os.path.getsize(fpath)
                    if fsize > max_log_size:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            lines = f.readlines()
                        # Keep last 2000 lines
                        trimmed = lines[-2000:]
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.writelines(trimmed)
                        log_bytes_freed = fsize - os.path.getsize(fpath)
                        results[f"log_{fname}_trimmed"] = f"freed {log_bytes_freed / 1024 / 1024:.1f}MB"
                except Exception:
                    pass
    except Exception:
        logger.exception("[db] Prune failed")

    total_freed = sum(v for v in results.values() if isinstance(v, int))
    results["total_rows_deleted"] = total_freed
    return results


# ── Analytics Queries (read-only, used by API) ──────────────────

async def get_outcome_stats() -> dict:
    """Aggregate stats from signal_outcomes for the analytics API."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT COUNT(*) as n, "
                "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins, "
                "SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END) as losses, "
                "SUM(CASE WHEN outcome='TIMEOUT' THEN 1 ELSE 0 END) as timeouts "
                "FROM signal_outcomes"
            ) as cur:
                row = await cur.fetchone()

            async with conn.execute(
                "SELECT signal_type, COUNT(*) as n, "
                "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins "
                "FROM signal_outcomes WHERE signal_type IS NOT NULL "
                "GROUP BY signal_type"
            ) as cur:
                by_type = {r["signal_type"]: {"n": r["n"], "wins": r["wins"],
                                              "win_rate": round(r["wins"]/r["n"]*100, 1) if r["n"] else 0}
                           async for r in cur}

            async with conn.execute(
                "SELECT tier, COUNT(*) as n, "
                "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins "
                "FROM signal_outcomes WHERE tier IS NOT NULL "
                "GROUP BY tier"
            ) as cur:
                by_tier = {r["tier"]: {"n": r["n"], "wins": r["wins"],
                                       "win_rate": round(r["wins"]/r["n"]*100, 1) if r["n"] else 0}
                           async for r in cur}

            async with conn.execute(
                "SELECT session, COUNT(*) as n, "
                "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins "
                "FROM signal_outcomes WHERE session IS NOT NULL AND session != '' "
                "GROUP BY session"
            ) as cur:
                by_session = {r["session"]: {"n": r["n"], "wins": r["wins"],
                                             "win_rate": round(r["wins"]/r["n"]*100, 1) if r["n"] else 0}
                              async for r in cur}

            async with conn.execute(
                "SELECT date(created_at) as day, COUNT(*) as n, "
                "SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins "
                "FROM signal_outcomes WHERE created_at >= date('now', '-30 days') "
                "GROUP BY day ORDER BY day"
            ) as cur:
                daily = [
                    {"day": r["day"], "n": r["n"],
                     "win_rate": round(r["wins"]/r["n"]*100, 1) if r["n"] else 0}
                    async for r in cur
                ]

        return {
            "total": row["n"] if row else 0,
            "wins": row["wins"] if row else 0,
            "losses": row["losses"] if row else 0,
            "timeouts": row["timeouts"] if row else 0,
            "win_rate": round(row["wins"] / row["n"] * 100, 1) if row and row["n"] else 0,
            "by_type": by_type,
            "by_tier": by_tier,
            "by_session": by_session,
            "daily_30d": daily,
        }
    except Exception:
        logger.exception("[db] get_outcome_stats failed")
        return {}


async def get_evolution_history(coin: str, limit: int = 50) -> list[dict]:
    """Get state transitions for a specific coin."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT coin, ts, state, spiral, direction, "
                "d1_score, d2_score, momentum_velocity, evolution "
                "FROM state_transitions WHERE coin = ? "
                "ORDER BY ts DESC LIMIT ?",
                (coin.upper(), limit),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("[db] get_evolution_history failed for %s", coin)
        return []


async def get_bayes_table() -> dict:
    """Get all Bayesian calibration entries with derived win rates."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT key, alpha, beta, updated_at FROM bayes_calibration"
            ) as cur:
                rows = await cur.fetchall()
        return {
            r["key"]: {
                "alpha": r["alpha"],
                "beta": r["beta"],
                "win_rate": round(r["alpha"] / (r["alpha"] + r["beta"]) * 100, 1),
                "samples": int(r["alpha"] + r["beta"] - 2),
                "updated_at": r["updated_at"],
            }
            for r in rows
        }
    except Exception:
        logger.exception("[db] get_bayes_table failed")
        return {}


async def get_recent_decisions(limit: int = 100) -> list[dict]:
    """Get recent D3 fusion decisions."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT coin, ts, signal_type, action, position_mult, "
                "stop_mult, ev_pct, confidence, d1_tier, d1_score, d2_tier, d2_score "
                "FROM decisions ORDER BY ts DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("[db] get_recent_decisions failed")
        return []


async def get_db_stats() -> dict:
    """Return row counts and DB file size."""
    try:
        async with _PooledConn() as conn:
            counts = {}
            for table in ("signal_outcomes", "bayes_calibration",
                          "state_transitions", "decisions"):
                async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
                    row = await cur.fetchone()
                    counts[table] = row[0]

        size_bytes = os.path.getsize(_DB_PATH) if os.path.exists(_DB_PATH) else 0
        return {
            "db_path": _DB_PATH,
            "size_mb": round(size_bytes / 1024 / 1024, 2),
            "row_counts": counts,
        }
    except Exception:
        logger.exception("[db] get_db_stats failed")
        return {}
