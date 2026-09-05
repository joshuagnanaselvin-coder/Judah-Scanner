"""Judah Scanner — Binance REST API client and trade import.

Handles Spot + Futures trade fetching, decryption of stored API keys,
and importing trades into the journal with deduplication by orderId.
"""
from __future__ import annotations

import aiohttp
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from .binance_schema import _decrypt, _get_cipher, get_connection_by_id, save_connection, get_connections, delete_connection
from .journal_schema import create_trade

logger = logging.getLogger("judah.binance")

BINANCE_REST_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Rate-limit guard: Binance allows 1200 req/min
_MIN_REQUEST_INTERVAL = 0.06  # ~16 req/sec, well under 1200/min


class BinanceClient:
    """Async client for Binance Spot + Futures REST APIs."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._last_request = 0.0

    async def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < _MIN_REQUEST_INTERVAL:
            await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)

    async def _request(
        self,
        session: aiohttp.ClientSession,
        base: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make an authenticated GET request to Binance."""
        import hashlib, hmac

        self._last_request = time.monotonic()
        query = ""
        if params:
            query = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
            # Sign with HMAC-SHA256
            signature = hmac.new(
                self._api_secret.encode(), query.encode(), hashlib.sha256
            ).hexdigest()
            query = f"{query}&signature={signature}"

        url = f"{base}{path}?{query}" if query else f"{base}{path}"

        async with session.get(
            url,
            headers={"X-MBX-APIKEY": self._api_key},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Binance API error {resp.status}: {text}")
            return await resp.json()

    # ── Spot ──

    async def fetch_spot_trades(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> list[dict[str, Any]]:
        """Fetch all Spot trades for a symbol between two timestamps."""
        trades: list[dict[str, Any]] = []
        from_id: int | None = None

        while True:
            params: dict[str, Any] = {
                "symbol": symbol,
                "startTime": start_ts,
                "endTime": end_ts,
                "limit": 1000,
            }
            if from_id is not None:
                params["fromId"] = from_id

            await self._rate_limit()
            batch = await self._request(session, BINANCE_REST_BASE, "/api/v3/myTrades", params)
            if not batch:
                break
            trades.extend(batch)
            if len(batch) < 1000:
                break
            from_id = batch[-1]["id"] + 1

        return trades

    # ── Futures ──

    async def fetch_futures_trades(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        start_ts: int,
        end_ts: int,
    ) -> list[dict[str, Any]]:
        """Fetch all Futures USDT trades for a symbol between two timestamps."""
        trades: list[dict[str, Any]] = []
        from_id: int | None = None

        while True:
            params: dict[str, Any] = {
                "symbol": symbol,
                "startTime": start_ts,
                "endTime": end_ts,
                "limit": 1000,
            }
            if from_id is not None:
                params["fromId"] = from_id

            await self._rate_limit()
            batch = await self._request(session, BINANCE_FUTURES_BASE, "/fapi/v1/userTrades", params)
            if not batch:
                break
            trades.extend(batch)
            if len(batch) < 1000:
                break
            from_id = batch[-1]["id"] + 1

        return trades

    # ── Helper: get all Spot symbols ──

    async def get_spot_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        """Return all active Spot trading symbols."""
        await self._rate_limit()
        data = await self._request(session, BINANCE_REST_BASE, "/api/v3/exchangeInfo")
        symbols = []
        for s in data.get("symbols", []):
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT":
                symbols.append(s["symbol"])
        return symbols

    # ── Helper: get all Futures symbols ──

    async def get_futures_symbols(self, session: aiohttp.ClientSession) -> list[str]:
        """Return all active Futures USDT symbols."""
        await self._rate_limit()
        data = await self._request(session, BINANCE_FUTURES_BASE, "/fapi/v1/exchangeInfo")
        symbols = []
        for s in data.get("symbols", []):
            if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL":
                symbols.append(s["symbol"])
        return symbols


# ── Import logic ──

async def _get_existing_order_ids(user_id: str, conn_ids: list[int]) -> set[str]:
    """Get set of (conn_id, orderId) pairs already in journal."""
    existing: set[str] = set()
    try:
        from .db import _PooledConn
        placeholders = ",".join("?" for _ in conn_ids) if conn_ids else "''"
        async with _PooledConn() as conn:
            async with conn.execute(
                f"SELECT binance_conn_id, binance_order_id FROM trades "
                f"WHERE user_id = ? AND binance_conn_id IN ({placeholders}) "
                f"AND binance_order_id IS NOT NULL",
                (user_id, *conn_ids),
            ) as cur:
                async for row in cur:
                    existing.add(f"{row[0]}:{row[1]}")
    except Exception:
        logger.exception("[binance] Failed to load existing order IDs")
    return existing


async def _import_trades_for_connection(
    client: BinanceClient,
    user_id: str,
    conn_id: int,
    start_ts: int,
    end_ts: int,
    existing_keys: set[str],
) -> tuple[int, int]:
    """Import trades for one connection. Returns (imported, skipped)."""
    imported = 0
    skipped = 0

    async with aiohttp.ClientSession() as session:
        # Fetch Spot + Futures trades
        spot_symbols = []
        futures_symbols = []
        try:
            spot_symbols = await client.get_spot_symbols(session)
        except Exception as e:
            logger.warning("[binance] Failed to fetch spot symbols: %s", e)
        try:
            futures_symbols = await client.get_futures_symbols(session)
        except Exception as e:
            logger.warning("[binance] Failed to fetch futures symbols: %s", e)

        # For each symbol, fetch trades
        all_symbols = list(set(spot_symbols + futures_symbols))
        for symbol in all_symbols:
            try:
                spot_trades = await client.fetch_spot_trades(session, symbol, start_ts, end_ts)
                for t in spot_trades:
                    key = f"{conn_id}:{t.get('orderId', t.get('id', ''))}"
                    if key in existing_keys:
                        skipped += 1
                        continue
                    await _create_journal_trade(user_id, conn_id, t, "SPOT")
                    existing_keys.add(key)
                    imported += 1
            except Exception:
                logger.debug("[binance] No spot trades for %s", symbol)

        for symbol in futures_symbols:
            try:
                fut_trades = await client.fetch_futures_trades(session, symbol, start_ts, end_ts)
                for t in fut_trades:
                    key = f"{conn_id}:{t.get('orderId', t.get('id', ''))}"
                    if key in existing_keys:
                        skipped += 1
                        continue
                    await _create_journal_trade(user_id, conn_id, t, "FUTURES")
                    existing_keys.add(key)
                    imported += 1
            except Exception:
                logger.debug("[binance] No futures trades for %s", symbol)

    return imported, skipped


def _map_binance_direction(side: str) -> str:
    return "LONG" if side == "BUY" else "SHORT"


async def _create_journal_trade(
    user_id: str,
    conn_id: int,
    trade: dict[str, Any],
    market_type: str,
) -> int | None:
    """Map a Binance trade dict to journal trade and insert."""
    import logging
    logger = logging.getLogger("judah.binance")

    try:
        side = trade.get("side", "")
        price = float(trade.get("price", 0))
        qty = float(trade.get("qty", 0))
        quote_qty = float(trade.get("quoteQty", 0))
        commission = float(trade.get("commission", 0))
        commission_asset = trade.get("commissionAsset", "")
        order_id = str(trade.get("orderId", trade.get("id", "")))
        trade_time = trade.get("time", 0)
        opened_at = (
            datetime.fromtimestamp(trade_time / 1000, tz=timezone.utc).isoformat()
            if trade_time
            else datetime.now(timezone.utc).isoformat()
        )
        is_buyer = trade.get("isBuyer", False)
        # For Spot: buyer = BUY = LONG open, seller = SELL = LONG close
        # For Futures: depends on side

        # Derive direction
        direction = _map_binance_direction(side) if market_type == "FUTURES" else ("LONG" if is_buyer else "SHORT")

        # Calculate profit/loss for closed trades
        # Binance trades are individual fills — we store each as a journal entry
        # For Futures, the PnL is in 'realizedPnl' field
        realized_pnl = float(trade.get("realizedPnl", 0))
        pnl_amount = realized_pnl if market_type == "FUTURES" else 0
        pnl_pct = 0.0

        # Fee as position_size approximation
        position_size = quote_qty if quote_qty > 0 else price * qty

        # Determine outcome
        outcome = "OPEN"
        if market_type == "FUTURES" and realized_pnl != 0:
            outcome = "WIN" if realized_pnl > 0 else "LOSS"
        elif market_type == "SPOT":
            # Spot trades are always buys or sells — hard to determine close
            outcome = "OPEN"

        symbol = trade.get("symbol", "")

        notes_parts = [
            f"Imported from {market_type} Binance",
            f"Side: {side}",
            f"Qty: {qty}",
        ]
        if commission:
            notes_parts.append(f"Fee: {commission} {commission_asset}")
        notes = " | ".join(notes_parts)

        trade_data = {
            "user_id": user_id,
            "symbol": symbol,
            "direction": direction,
            "signal_type": "NONE",
            "entry_price": price,
            "exit_price": None,
            "sl_price": None,
            "rr": None,
            "position_size": position_size,
            "leverage": None,
            "outcome": outcome,
            "pnl_pct": pnl_pct,
            "pnl_amount": pnl_amount,
            "notes": notes,
            "opened_at": opened_at,
            "binance_order_id": order_id,
            "binance_market_type": market_type,
            "tags": ["binance-import"],
        }
        trade_id = await create_trade(data=trade_data)
        return trade_id
    except Exception:
        logger.exception("[binance] Failed to create journal trade for %s", trade.get("id"))
        return None


async def import_binance_trades(
    user_id: str,
    conn_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Import trades from Binance for a given connection.

    Args:
        user_id: User identifier
        conn_id: Binance connection ID
        start_date: ISO date string (default: 2026-09-01)
        end_date: ISO date string (default: today)

    Returns:
        {"imported": int, "skipped": int, "error": str | None}
    """
    # Default date range: from 2026-09-01 to today
    if not start_date:
        start_date = "2026-09-01"
    if not end_date:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    # Get connection with decrypted keys
    conn_data = await get_connection_by_id(conn_id, user_id)
    if not conn_data:
        return {"imported": 0, "skipped": 0, "error": "Connection not found"}

    api_key = conn_data.get("api_key", "")
    api_secret = conn_data.get("api_secret", "")
    if not api_key or not api_secret:
        return {"imported": 0, "skipped": 0, "error": "API keys not available (set JUDAH_SECRET_KEY)"}

    client = BinanceClient(api_key, api_secret)

    # Load existing order IDs for dedup
    existing_keys = await _get_existing_order_ids(user_id, [conn_id])

    try:
        imported, skipped = await _import_trades_for_connection(
            client, user_id, conn_id, start_ts, end_ts, existing_keys
        )
        return {"imported": imported, "skipped": skipped, "error": None}
    except Exception as e:
        logger.exception("[binance] Import failed for connection %s", conn_id)
        return {"imported": 0, "skipped": 0, "error": str(e)}


async def test_binance_connection(api_key: str, api_secret: str) -> dict[str, Any]:
    """Test Binance API keys by fetching account info."""
    client = BinanceClient(api_key, api_secret)
    try:
        async with aiohttp.ClientSession() as session:
            await client._rate_limit()
            data = await client._request(session, BINANCE_REST_BASE, "/api/v3/account")
            # Return basic account info (sanitized)
            return {
                "valid": True,
                "account_type": data.get("accountType", "SPOT"),
                "can_trade": data.get("canTrade", False),
            }
    except Exception as e:
        return {"valid": False, "error": str(e)}

