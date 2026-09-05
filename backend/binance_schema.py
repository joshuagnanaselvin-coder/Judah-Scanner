"""Judah Scanner — Binance API connection management.

Stores encrypted API key pairs per user (max 10 connections).
Uses Fernet symmetric encryption — key from env var JUDAH_SECRET_KEY.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from .db import _PooledConn

logger = logging.getLogger("judah.binance")

_BINANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS binance_connections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT    NOT NULL DEFAULT 'default',
    label               TEXT    NOT NULL DEFAULT 'Binance',
    api_key_encrypted   TEXT    NOT NULL,
    api_secret_encrypted TEXT   NOT NULL,
    is_active           INTEGER DEFAULT 1,
    created_at          TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_bc_user ON binance_connections(user_id);
"""


def _get_cipher() -> Fernet | None:
    """Get Fernet cipher from env var. Returns None if key not configured."""
    key = os.environ.get("JUDAH_SECRET_KEY", "")
    if not key:
        return None
    try:
        # Pad key to 32 bytes if needed (Fernet requires base64-encoded 32 bytes)
        key_b64 = base64.urlsafe_b64encode(key.encode()[:32].ljust(32, b"\x00"))
        return Fernet(key_b64)
    except Exception:
        return None


def _encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns empty string if no cipher available."""
    cipher = _get_cipher()
    if not cipher:
        return ""
    return cipher.encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    """Decrypt a string. Returns empty string on failure."""
    cipher = _get_cipher()
    if not cipher or not ciphertext:
        return ""
    try:
        return cipher.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("[binance] Failed to decrypt — invalid token")
        return ""


async def init_binance_schema() -> None:
    """Create binance_connections table if it doesn't exist."""
    try:
        async with _PooledConn() as conn:
            await conn.executescript(_BINANCE_SCHEMA)
        logger.info("[binance] Schema initialized")
    except Exception:
        logger.exception("[binance] Schema init failed")


async def save_connection(user_id: str, label: str, api_key: str, api_secret: str) -> int | None:
    """Save a new Binance API connection (encrypted). Returns new connection id."""
    try:
        async with _PooledConn() as conn:
            # Enforce max 10 connections per user
            async with conn.execute(
                "SELECT COUNT(*) FROM binance_connections WHERE user_id = ? AND is_active = 1",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
            if row and row[0] >= 10:
                raise ValueError("Maximum 10 API connections allowed")

            enc_key = _encrypt(api_key)
            enc_secret = _encrypt(api_secret)
            async with conn.execute(
                "INSERT INTO binance_connections (user_id, label, api_key_encrypted, api_secret_encrypted) "
                "VALUES (?,?,?,?)",
                (user_id, label, enc_key, enc_secret),
            ) as cur:
                await conn.commit()
                return cur.lastrowid
    except Exception:
        logger.exception("[binance] Failed to save connection")
        raise


async def get_connections(user_id: str) -> list[dict[str, Any]]:
    """Get all connections for a user (without decrypted keys)."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT id, user_id, label, is_active, created_at "
                "FROM binance_connections WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception("[binance] Failed to get connections")
        return []


async def get_connection_by_id(conn_id: int, user_id: str) -> dict[str, Any] | None:
    """Get a single connection with decrypted keys (for API calls)."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "SELECT id, user_id, label, api_key_encrypted, api_secret_encrypted, is_active, created_at "
                "FROM binance_connections WHERE id = ? AND user_id = ?",
                (conn_id, user_id),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["api_key"] = _decrypt(d.pop("api_key_encrypted", ""))
        d["api_secret"] = _decrypt(d.pop("api_secret_encrypted", ""))
        return d
    except Exception:
        logger.exception("[binance] Failed to get connection %s", conn_id)
        return None


async def delete_connection(conn_id: int, user_id: str) -> bool:
    """Delete a connection by id. Returns True if existed."""
    try:
        async with _PooledConn() as conn:
            async with conn.execute(
                "DELETE FROM binance_connections WHERE id = ? AND user_id = ?",
                (conn_id, user_id),
            ) as cur:
                await conn.commit()
                return cur.rowcount > 0
    except Exception:
        logger.exception("[binance] Failed to delete connection %s", conn_id)
        return False
