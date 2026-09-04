"""Judah Scanner — Trade Journal schema and CRUD operations.

Additive-only tables for the manual trade journal feature.
Uses the same aiosqlite connection pool from db.py.
Follows the naming and style conventions of backend/db.py.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from .db import _PooledConn, _DB_PATH, _write_lock, _write_unlock

logger = logging.getLogger("judah.journal")

# Directory for uploaded journal images
_IMAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "journal_images",
)
os.makedirs(_IMAGE_DIR, exist_ok=True)

# ── Schema ──────────────────────────────────────────────────────

_TRADES_SCHEMA = """
-- Journal Table 1: Trades (manual trade entries)
-- Supports full lifecycle: OPEN → WIN / LOSS / BREAK_EVEN / TIMEOUT
-- Judah-specific fields (Phase 4 extension) — additive only
CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT    DEFAULT 'default',
    symbol           TEXT    NOT NULL,
    direction        TEXT    NOT NULL CHECK(direction IN ('LONG','SHORT')),
    signal_type      TEXT    DEFAULT 'NONE' CHECK(signal_type IN ('A','B','C','D','E','F','NONE')),
    entry_price      REAL    NOT NULL,
    exit_price       REAL,
    sl_price         REAL,
    tp_price         REAL,
    rr               REAL,
    position_size    REAL,
    leverage         REAL,
    confidence_score REAL,
    market_state     TEXT,
    -- Judah context fields
    spiral           TEXT,
    market_evolution TEXT,
    d1_zone          TEXT,
    d2_zone          TEXT,
    dealing_range_4h TEXT,
    dealing_range_15m TEXT,
    liquidity_type   TEXT,
    liquidity_event  TEXT,
    sweep            INTEGER DEFAULT 0,
    bos              TEXT,
    fib              REAL,
    planned_entry    REAL,
    actual_entry     REAL,
    exit_reason      TEXT,
    mfe              REAL,
    mae              REAL,
    session          TEXT,
    r_multiple       REAL,
    holding_time     INTEGER,
    max_drawdown     REAL,
    -- Outcome fields
    outcome          TEXT    DEFAULT 'OPEN' CHECK(outcome IN ('WIN','LOSS','BREAK_EVEN','TIMEOUT','OPEN')),
    pnl_pct          REAL,
    pnl_amount       REAL,
    notes            TEXT,
    opened_at        TEXT,
    closed_at        TEXT,
    created_at       TEXT    DEFAULT (datetime('now')),
    updated_at       TEXT    DEFAULT (datetime('now')),
    is_deleted       INTEGER DEFAULT 0
);

-- Journal Table 2: Trade Notes (free-text + screenshot per trade)
CREATE TABLE IF NOT EXISTS trade_notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id      INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    note_text     TEXT    NOT NULL,
    screenshot_url TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);

-- Journal Table 3: Trade Tags (flexible tagging for filtering)
CREATE TABLE IF NOT EXISTS trade_tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id   INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    tag        TEXT    NOT NULL,
    created_at TEXT    DEFAULT (datetime('now')),
    UNIQUE(trade_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol    ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_outcome   ON trades(outcome);
CREATE INDEX IF NOT EXISTS idx_trades_direction ON trades(direction);
CREATE INDEX IF NOT EXISTS idx_trades_user_id   ON trades(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_opened    ON trades(opened_at);
CREATE INDEX IF NOT EXISTS idx_trades_created   ON trades(created_at);
CREATE INDEX IF NOT EXISTS idx_trade_notes_trade ON trade_notes(trade_id);
CREATE INDEX IF NOT EXISTS idx_trade_tags_trade  ON trade_tags(trade_id);
CREATE INDEX IF NOT EXISTS idx_trade_tags_tag   ON trade_tags(tag);
"""


# ── Init ─────────────────────────────────────────────────────────

async def init_journal_schema() -> None:
    """Create journal tables if they don't exist. Safe to call multiple times."""
    try:
        async with _PooledConn() as conn:
            await conn.executescript(_TRADES_SCHEMA)
            # Migrate: add columns that were added after initial VPS deploy
            for col in ("spiral", "market_evolution", "d1_zone", "d2_zone",
                        "dealing_range_4h", "dealing_range_15m",
                        "liquidity_type", "liquidity_event", "sweep", "bos", "fib",
                        "planned_entry", "actual_entry", "exit_reason",
                        "mfe", "mae", "session", "r_multiple", "holding_time", "max_drawdown",
                        "confidence_score", "position_size", "leverage",
                        "pnl_pct", "pnl_amount", "is_deleted"):
                try:
                    await conn.execute(f"ALTER TABLE trades ADD COLUMN {col} TEXT")
                except Exception:
                    pass  # column already exists
        logger.info("[journal] Schema initialized")
    except Exception:
        logger.exception("[journal] Schema init failed")


# ── Trades CRUD ─────────────────────────────────────────────────

async def create_trade(data: dict[str, Any], user_id: str = "default") -> int | None:
    """Insert a new trade. Returns the new trade id or raises."""
    await _write_lock()
    try:
        try:
            async with _PooledConn() as conn:
                now = datetime.now(timezone.utc).isoformat()
                cur = await conn.execute(
                    """
                    INSERT INTO trades
                        (user_id, symbol, direction, signal_type,
                         entry_price, exit_price, sl_price, tp_price, rr,
                         position_size, leverage, confidence_score, market_state,
                         spiral, market_evolution, d1_zone, d2_zone,
                         dealing_range_4h, dealing_range_15m,
                         liquidity_type, liquidity_event, sweep, bos, fib,
                         planned_entry, actual_entry, exit_reason,
                         mfe, mae, session, r_multiple, holding_time, max_drawdown,
                         outcome, pnl_pct, pnl_amount, notes, opened_at, closed_at,
                         created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        user_id,
                        data.get("symbol", "").upper(),
                        data.get("direction", "LONG"),
                        data.get("signal_type", "NONE"),
                        data.get("entry_price"),
                        data.get("exit_price"),
                        data.get("sl_price"),
                        data.get("tp_price"),
                        data.get("rr"),
                        data.get("position_size"),
                        data.get("leverage"),
                        data.get("confidence_score"),
                        data.get("market_state"),
                        data.get("spiral"),
                        data.get("market_evolution"),
                        data.get("d1_zone"),
                        data.get("d2_zone"),
                        data.get("dealing_range_4h"),
                        data.get("dealing_range_15m"),
                        data.get("liquidity_type"),
                        data.get("liquidity_event"),
                        1 if data.get("sweep") else 0,
                        data.get("bos"),
                        data.get("fib"),
                        data.get("planned_entry"),
                        data.get("actual_entry"),
                        data.get("exit_reason"),
                        data.get("mfe"),
                        data.get("mae"),
                        data.get("session"),
                        data.get("r_multiple"),
                        data.get("holding_time"),
                        data.get("max_drawdown"),
                        data.get("outcome", "OPEN"),
                        data.get("pnl_pct"),
                        data.get("pnl_amount"),
                        data.get("notes"),
                        data.get("opened_at", now),
                        data.get("closed_at"),
                        now,
                        now,
                    ),
                )
                await conn.commit()
                trade_id = cur.lastrowid
                # Attach tags if provided
                tags = data.get("tags", [])
                if tags:
                    await conn.executemany(
                        "INSERT OR IGNORE INTO trade_tags (trade_id, tag) VALUES (?, ?)",
                        [(trade_id, t.strip().lower()) for t in tags if t.strip()],
                    )
                    await conn.commit()
                return trade_id
        except Exception:
            logger.exception("[journal] Failed to create trade for %s", data.get("symbol"))
            raise
    finally:
        _write_unlock()
    await _write_lock()
    try:
        fields: list[str] = []
        values: list[Any] = []
        allowed = {
            "symbol", "direction", "signal_type", "entry_price", "exit_price",
            "sl_price", "tp_price", "rr", "position_size", "leverage",
            "confidence_score", "market_state", "outcome", "pnl_pct",
            "pnl_amount", "notes", "opened_at", "closed_at",
            "spiral", "market_evolution", "d1_zone", "d2_zone",
            "dealing_range_4h", "dealing_range_15m",
            "liquidity_type", "liquidity_event", "sweep", "bos", "fib",
            "planned_entry", "actual_entry", "exit_reason",
            "mfe", "mae", "session", "r_multiple", "holding_time", "max_drawdown",
        }
        for key in allowed:
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data[key])
        if not fields:
            _write_unlock()
            return False

        fields.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.extend([user_id, trade_id])

        async with _PooledConn() as conn:
            await conn.execute(
                f"UPDATE trades SET {', '.join(fields)} "
                f"WHERE id = ? AND user_id = ? AND is_deleted = 0",
                values,
            )
            # Replace tags if provided
            if "tags" in data:
                await conn.execute(
                    "DELETE FROM trade_tags WHERE trade_id = ?", (trade_id,)
                )
                tags = data["tags"]
                if tags:
                    await conn.executemany(
                        "INSERT OR IGNORE INTO trade_tags (trade_id, tag) VALUES (?, ?)",
                        [(trade_id, t.strip().lower()) for t in tags if t.strip()],
                    )
            await conn.commit()
            _write_unlock()
            return True
    except Exception:
        _write_unlock()
        logger.exception("[journal] Failed to update trade %s", trade_id)
        return False


async def delete_trade(trade_id: int, user_id: str = "default") -> bool:
    """Soft-delete a trade, verifying ownership. Returns True if the row existed."""
    await _write_lock()
    try:
        async with _PooledConn() as conn:
            await conn.execute(
                "UPDATE trades SET is_deleted = 1, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (datetime.now(timezone.utc).isoformat(), trade_id, user_id),
            )
            await conn.commit()
            _write_unlock()
            return True
    except Exception:
        _write_unlock()
        logger.exception("[journal] Failed to delete trade %s", trade_id)
        return False


async def get_trade(trade_id: int, user_id: str = "default") -> dict[str, Any] | None:
    """Get a single trade by id (with ownership check), plus notes and tags."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                """SELECT id, user_id, symbol, direction, signal_type,
                          entry_price, exit_price, sl_price, tp_price, rr,
                          position_size, leverage, confidence_score, market_state,
                          spiral, market_evolution, d1_zone, d2_zone,
                          dealing_range_4h, dealing_range_15m,
                          liquidity_type, liquidity_event, sweep, bos, fib,
                          planned_entry, actual_entry, exit_reason,
                          mfe, mae, session, r_multiple, holding_time, max_drawdown,
                          outcome, pnl_pct, pnl_amount, notes,
                          opened_at, closed_at, created_at, updated_at
                   FROM trades WHERE id = ? AND user_id = ? AND is_deleted = 0""",
                (trade_id, user_id),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            trade: dict[str, Any] = dict(row)

            # Tags
            async with conn.execute(
                "SELECT tag FROM trade_tags WHERE trade_id = ? ORDER BY tag",
                (trade_id,),
            ) as cur:
                trade["tags"] = [r["tag"] for r in await cur.fetchall()]

            # Notes
            async with conn.execute(
                "SELECT id, note_text, screenshot_url, created_at "
                "FROM trade_notes WHERE trade_id = ? ORDER BY created_at",
                (trade_id,),
            ) as cur:
                trade["notes_list"] = [dict(r) for r in await cur.fetchall()]

            return trade
    except Exception:
        logger.exception("[journal] Failed to get trade %s", trade_id)
        return None


async def list_trades(
    symbol: str = "",
    direction: str = "",
    outcome: str = "",
    signal_type: str = "",
    tag: str = "",
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "created_at",
    sort_dir: str = "DESC",
    user_id: str = "default",
) -> list[dict[str, Any]]:
    """List trades for a specific user with optional filters, pagination, and sorting."""
    allowed_sort = {"created_at", "opened_at", "closed_at", "symbol", "pnl_pct", "outcome"}
    if sort_by not in allowed_sort:
        sort_by = "created_at"
    if sort_dir.upper() not in ("ASC", "DESC"):
        sort_dir = "DESC"

    where = ["is_deleted = 0", "user_id = ?"]
    params: list[Any] = [user_id]

    if symbol:
        where.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    if direction:
        where.append("direction = ?")
        params.append(direction)
    if outcome:
        where.append("outcome = ?")
        params.append(outcome)
    if signal_type:
        where.append("signal_type = ?")
        params.append(signal_type)
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM trade_tags tt "
            "WHERE tt.trade_id = trades.id AND tt.tag = ?)"
        )
        params.append(tag.strip().lower())

    sql = (
        "SELECT id, symbol, direction, signal_type, entry_price, exit_price, "
        "sl_price, tp_price, rr, position_size, leverage, confidence_score, "
        "market_state, spiral, market_evolution, d1_zone, d2_zone, "
        "dealing_range_4h, dealing_range_15m, "
        "liquidity_type, liquidity_event, sweep, bos, fib, "
        "planned_entry, actual_entry, exit_reason, "
        "mfe, mae, session, r_multiple, holding_time, max_drawdown, "
        "outcome, pnl_pct, pnl_amount, opened_at, closed_at, "
        "created_at, updated_at "
        "FROM trades "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    try:
        async with _PooledConn() as conn:
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("[journal] list_trades failed")
        return []


async def get_trade_count(
    symbol: str = "",
    direction: str = "",
    outcome: str = "",
    signal_type: str = "",
    tag: str = "",
    user_id: str = "default",
) -> int:
    """Count matching trades for a specific user (for pagination)."""
    where = ["is_deleted = 0", "user_id = ?"]
    params: list[Any] = [user_id]

    if symbol:
        where.append("UPPER(symbol) = ?")
        params.append(symbol.upper())
    if direction:
        where.append("direction = ?")
        params.append(direction)
    if outcome:
        where.append("outcome = ?")
        params.append(outcome)
    if signal_type:
        where.append("signal_type = ?")
        params.append(signal_type)
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM trade_tags tt "
            "WHERE tt.trade_id = trades.id AND tt.tag = ?)"
        )
        params.append(tag.strip().lower())

    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) FROM trades WHERE {' AND '.join(where)}",
                params,
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0
    except Exception:
        logger.exception("[journal] get_trade_count failed")
        return 0


# ── Notes CRUD ──────────────────────────────────────────────────

async def add_trade_note(
    trade_id: int, note_text: str, screenshot_url: str = ""
) -> int | None:
    """Add a note to a trade. Returns the new note id."""
    try:
        async with _PooledConn() as conn:
            cur = await conn.execute(
                "INSERT INTO trade_notes (trade_id, note_text, screenshot_url) "
                "VALUES (?,?,?)",
                (trade_id, note_text, screenshot_url),
            )
            await conn.commit()
            return cur.lastrowid
    except Exception:
        logger.exception("[journal] Failed to add note to trade %s", trade_id)
        return None


async def delete_trade_note(note_id: int) -> bool:
    """Delete a trade note by id."""
    try:
        async with _PooledConn() as conn:
            await conn.execute("DELETE FROM trade_notes WHERE id = ?", (note_id,))
            await conn.commit()
            return True
    except Exception:
        logger.exception("[journal] Failed to delete note %s", note_id)
        return False


# ── Tags ────────────────────────────────────────────────────────

async def get_all_tags(user_id: str = "default") -> list[str]:
    """Get all unique tags for a user's trades, sorted."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT DISTINCT tag FROM trade_tags tt "
                "JOIN trades t ON t.id = tt.trade_id "
                "WHERE t.user_id = ? AND t.is_deleted = 0 "
                "ORDER BY tag",
                (user_id,),
            ) as cur:
                return [r["tag"] for r in await cur.fetchall()]
    except Exception:
        logger.exception("[journal] get_all_tags failed")
        return []


# ── Statistics ──────────────────────────────────────────────────

async def prune_old_trades(days: int = 30) -> int:
    """Delete trades older than `days` days. Returns number of rows deleted."""
    try:
        async with _PooledConn() as conn:
            cutoff_dt = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            cutoff_iso = (datetime.now(timezone.utc) - __import__('datetime').timedelta(days=days)).isoformat()
            async with conn.execute(
                "DELETE FROM trades WHERE opened_at < ?",
                (cutoff_iso,),
            ) as cur:
                deleted = cur.rowcount
            await conn.commit()
            if deleted:
                logger.info(f"[journal] Pruned {deleted} trades older than {days} days")
            return deleted
    except Exception:
        logger.exception("[journal] prune_old_trades failed")
        return 0


async def export_trades_csv(user_id: str = "default", date: str = "") -> str | None:
    """Export trades as CSV string. Optional date filter (YYYY-MM-DD)."""
    try:
        where = ["is_deleted = 0", "user_id = ?"]
        params: list[Any] = [user_id]
        if date:
            where.append("DATE(opened_at) = ?")
            params.append(date)
        sql = (
            "SELECT id, symbol, direction, signal_type, entry_price, exit_price, "
            "sl_price, tp_price, rr, position_size, leverage, outcome, "
            "pnl_pct, pnl_amount, opened_at, closed_at, notes "
            "FROM trades "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY opened_at DESC"
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["#", "Coin", "Direction", "Type", "Entry", "Exit",
                          "SL", "TP", "RR", "Size", "Leverage",
                          "Outcome", "PnL%", "PnL$", "Opened", "Closed", "Notes"])
        async with _PooledConn() as conn:
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        if not rows:
            return None
        for i, r in enumerate(rows, 1):
            writer.writerow([
                i, r["symbol"], r["direction"], r["signal_type"],
                r["entry_price"], r["exit_price"], r["sl_price"], r["tp_price"],
                r["rr"], r["position_size"], r["leverage"],
                r["outcome"], r["pnl_pct"], r["pnl_amount"],
                (r["opened_at"] or "")[:19], (r["closed_at"] or "")[:19],
                (r["notes"] or "")[:200],
            ])
        return buf.getvalue()
    except Exception:
        logger.exception("[journal] export_trades_csv failed")
        return None


async def save_journal_image(base64_data: str, filename: str) -> str | None:
    """Save a base64 image to the journal_images directory. Returns public URL."""
    try:
        if not base64_data:
            return None
        # Strip data URL prefix if present (e.g. "data:image/png;base64,...")
        if "," in base64_data and base64_data.split(",")[0].startswith("data:"):
            base64_data = base64_data.split(",", 1)[1]
        import base64
        img_bytes = base64.b64decode(base64_data)
        safe_name = filename or f"trade_{uuid.uuid4().hex[:8]}.png"
        out_path = os.path.join(_IMAGE_DIR, safe_name)
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        return f"/static/journal_images/{safe_name}"
    except Exception:
        logger.exception("[journal] save_journal_image failed")
        return None


# ── Statistics ──────────────────────────────────────────────────

async def get_journal_stats(user_id: str = "default") -> dict[str, Any]:
    """Aggregate statistics from the journal for a specific user."""
    try:
        async with _PooledConn() as conn:
            # Totals
            async with conn.execute(
                "SELECT COUNT(*) as total FROM trades WHERE is_deleted = 0 AND user_id = ?",
                (user_id,),
            ) as cur:
                totals = dict(await cur.fetchone())

            # Outcomes breakdown
            async with conn.execute(
                """SELECT outcome, COUNT(*) as n,
                          SUM(pnl_pct) as total_pnl_pct,
                          AVG(pnl_pct) as avg_pnl_pct,
                          SUM(pnl_amount) as total_pnl
                   FROM trades
                   WHERE is_deleted = 0 AND user_id = ? AND outcome != 'OPEN'
                   GROUP BY outcome""",
                (user_id,),
            ) as cur:
                outcomes = {r["outcome"]: dict(r) for r in await cur.fetchall()}

            wins = outcomes.get("WIN", {}).get("n", 0)
            losses = outcomes.get("LOSS", {}).get("n", 0)
            break_even = outcomes.get("BREAK_EVEN", {}).get("n", 0)
            closed = wins + losses + break_even
            win_rate = round(wins / closed * 100, 1) if closed else 0

            # Average RR on closed trades
            async with conn.execute(
                "SELECT AVG(rr) as avg_rr FROM trades "
                "WHERE is_deleted = 0 AND rr IS NOT NULL AND rr > 0 "
                "AND outcome != 'OPEN'"
            ) as cur:
                avg_rr_row = await cur.fetchone()
            avg_rr = round(avg_rr_row["avg_rr"], 2) if avg_rr_row and avg_rr_row["avg_rr"] else 0

            # By direction
            async with conn.execute(
                """SELECT direction, COUNT(*) as n,
                          SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins
                   FROM trades WHERE is_deleted = 0 AND user_id = ? AND outcome != 'OPEN'
                   GROUP BY direction""",
                (user_id,),
            ) as cur:
                by_direction = {r["direction"]: dict(r) async for r in cur}

            # By signal type
            async with conn.execute(
                """SELECT signal_type, COUNT(*) as n,
                          SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins
                   FROM trades WHERE is_deleted = 0 AND user_id = ? AND outcome != 'OPEN'
                   GROUP BY signal_type""",
                (user_id,),
            ) as cur:
                by_signal_type = {r["signal_type"]: dict(r) async for r in cur}

            # By tag (joins with trades to filter non-deleted and user)
            async with conn.execute(
                """SELECT tt.tag, COUNT(*) as n,
                          SUM(CASE WHEN t.outcome='WIN' THEN 1 ELSE 0 END) as wins,
                          AVG(t.pnl_pct) as avg_pnl_pct
                   FROM trade_tags tt
                   JOIN trades t ON t.id = tt.trade_id
                   WHERE t.is_deleted = 0 AND t.user_id = ? AND t.outcome != 'OPEN'
                   GROUP BY tt.tag""",
                (user_id,),
            ) as cur:
                by_tag = {r["tag"]: dict(r) async for r in cur}

            # Expectancy: (win_rate% * avg_win) - (loss_rate% * avg_loss)
            avg_win = outcomes.get("WIN", {}).get("avg_pnl_pct", 0) or 0
            avg_loss = outcomes.get("LOSS", {}).get("avg_pnl_pct", 0) or 0
            win_rate_d = wins / closed if closed else 0
            expectancy = round(
                win_rate_d * avg_win + (1 - win_rate_d) * avg_loss, 4
            ) if closed else 0.0

            # Profit factor: gross profit / gross loss
            gross_profit = outcomes.get("WIN", {}).get("total_pnl_pct", 0) or 0
            gross_loss = abs(outcomes.get("LOSS", {}).get("total_pnl_pct", 0) or 0)
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss else 0.0

            # Max consecutive wins/losses (simple)
            async with conn.execute(
                """SELECT outcome FROM trades
                   WHERE is_deleted = 0 AND user_id = ? AND outcome IN ('WIN','LOSS')
                   ORDER BY closed_at ASC""",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
            max_consec_wins = 0
            max_consec_losses = 0
            cur_wins = 0
            cur_losses = 0
            for r in rows:
                if r["outcome"] == "WIN":
                    cur_wins += 1
                    cur_losses = 0
                    max_consec_wins = max(max_consec_wins, cur_wins)
                else:
                    cur_losses += 1
                    cur_wins = 0
                    max_consec_losses = max(max_consec_losses, cur_losses)

        return {
            "total_trades": totals.get("total", 0),
            "closed_trades": closed,
            "open_trades": totals.get("total", 0) - closed,
            "wins": wins,
            "losses": losses,
            "break_even": break_even,
            "win_rate": win_rate,
            "avg_rr": avg_rr,
            "expectancy_pct": expectancy,
            "profit_factor": profit_factor,
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
            "by_direction": by_direction,
            "by_signal_type": by_signal_type,
            "by_tag": by_tag,
            "outcomes": outcomes,
        }
    except Exception:
        logger.exception("[journal] get_journal_stats failed")
        return {}


async def get_equity_curve(limit: int = 200, user_id: str = "default") -> list[dict[str, Any]]:
    """Get cumulative PnL over time for a specific user's equity curve chart."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                """SELECT closed_at, SUM(pnl_pct) OVER (ORDER BY closed_at) as equity,
                          pnl_pct, symbol, outcome
                   FROM trades
                   WHERE is_deleted = 0 AND user_id = ? AND outcome != 'OPEN'
                     AND closed_at IS NOT NULL
                   ORDER BY closed_at ASC
                   LIMIT ?""",
                (user_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("[journal] get_equity_curve failed")
        return []
