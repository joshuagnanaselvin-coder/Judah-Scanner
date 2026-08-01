"""REST bootstrap + Binance WebSocket client + candle builder."""
import asyncio
import json
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Optional
from backend.config import (
    BINANCE_REST_BASE, BINANCE_WS_BASE, BINANCE_INTERVAL_MAP,
    WS_RECONNECT_DELAY_SEC, WS_MAX_STREAMS_PER_CONN,
    BOOTSTRAP_CANDLES, TIMEFRAMES_HTF, ALL_TIMEFRAMES,
)
from backend.schemas import Candle

logger = logging.getLogger("judah.md")

def _binance_interval(tf: str) -> str:
    """Convert internal TF (1H, 4H, 1D, 15M) to Binance REST/WS interval (1h, 4h, 1d, 15m)."""
    return BINANCE_INTERVAL_MAP.get(tf.upper(), tf.lower())

class MarketData:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init = False
        return cls._instance

    def __init__(self):
        if self._init: return
        self._init = True

        self.candles: dict = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws_connected = False
        self.on_candle_close = None
        self.on_candle_update = None
        self._symbols: list = []

    async def bootstrap(self, symbols: list) -> int:
        self._symbols = symbols
        self.session = aiohttp.ClientSession()
        count = 0

        print(f"[marketdata] Bootstrapping {len(symbols)} coins...")

        hf_pairs = [(s, tf) for s in symbols for tf in TIMEFRAMES_HTF]
        total = len(hf_pairs)
        print(f"[marketdata] HTF-only: {total} requests ({len(TIMEFRAMES_HTF)} TFs x {len(symbols)} pairs)")

        # Sequential bootstrap — 1 req/sec to stay safely under Binance IP rate limits.
        # With 1,587 pairs this takes ~26 min but is 100% reliable (proven working).
        # The scanner loop starts immediately after bootstrap — WS fills LTF data live.
        # DO NOT increase concurrency: Binance IP rate-limits simultaneous requests.
        errors = 0
        for idx, (symbol, tf) in enumerate(hf_pairs):
            result = await self._fetch_klines_with_retry(symbol, tf, BOOTSTRAP_CANDLES)
            if result and len(result) >= 50:
                key = f"{symbol}_{tf}"
                self.candles[key] = result
                count += 1
            else:
                errors += 1

            if (idx + 1) % 100 == 0:
                print(f"[marketdata] {idx + 1}/{total} — {count} OK, {errors} failed")

            # 1 req/sec = safe for Binance IP limits (1200 req/min)
            await asyncio.sleep(1.0)

        print(f"[marketdata] Bootstrapped {count}/{total} candle sets ({errors} failed)")
        return count

    async def _fetch_klines(self, symbol, interval, limit):
        """Single-shot fetch (no retry). Used by LTF refresh."""
        binance_tf = _binance_interval(interval)
        url = f"{BINANCE_REST_BASE}/klines?symbol={symbol}&interval={binance_tf}&limit={limit}"
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.debug(f"HTTP {resp.status} for {symbol} {binance_tf}: {text[:100]}")
                    return []
                data = json.loads(text)
                if not isinstance(data, list):
                    return []
                return [Candle(time=k[0], open=float(k[1]), high=float(k[2]),
                               low=float(k[3]), close=float(k[4]), volume=float(k[5]),
                               close_time=k[6], is_closed=True) for k in data]
        except Exception as e:
            logger.debug(f"Fetch error {symbol} {binance_tf}: {e}")
            return []

    async def _fetch_klines_with_retry(self, symbol, interval, limit, max_retries=3):
        """Fetch with retry on rate-limit (429) and server errors (5xx)."""
        for attempt in range(max_retries):
            result = await self._fetch_klines(symbol, interval, limit)
            if result:
                return result
            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 0.5   # 0.5s, 1s, 2s backoff
                await asyncio.sleep(wait)
        return []

    def connect_websocket(self, symbols: list):
        all_streams = []
        for symbol in symbols:
            for tf in ALL_TIMEFRAMES:
                tf_lower = tf.lower()
                all_streams.append(f"{symbol.lower()}@kline_{tf_lower}")

        chunk_size = WS_MAX_STREAMS_PER_CONN
        chunks = [all_streams[i:i + chunk_size]
                  for i in range(0, len(all_streams), chunk_size)]
        self._ws_tasks = [asyncio.create_task(self._ws_connection(i, chunk))
                          for i, chunk in enumerate(chunks)]
        logger.info(f"[ws] Created {len(chunks)} WS connections "
                    f"({len(all_streams)} streams, ~{len(symbols)} pairs x {len(ALL_TIMEFRAMES)} TFs)")

    async def _ws_connection(self, conn_id, streams):
        """Runs one persistent WS connection with auto-reconnect."""
        url = BINANCE_WS_BASE + "/".join(streams)
        delay = WS_RECONNECT_DELAY_SEC
        while True:
            try:
                async with self.session.ws_connect(url, heartbeat=30) as ws:
                    logger.info(f"[ws] Conn {conn_id}: {len(streams)} streams — connected")
                    self.ws_connected = True
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._handle_kline(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                logger.error(f"[ws] Conn {conn_id} error: {e}")
                self.ws_connected = False
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 60)

    def _handle_kline(self, msg):
        if msg.get("e") != "kline": return

        k = msg["k"]
        symbol, tf_raw = msg["s"], k["i"]
        # Binance sends lowercase tf (1d, 4h, 1h) — normalize to match candle keys
        tf = tf_raw.upper()
        key = f"{symbol}_{tf}"
        if key not in self.candles: return

        is_closed = k["x"]
        candle_data = {
            "time": k["t"], "open": float(k["o"]), "high": float(k["h"]),
            "low": float(k["l"]), "close": float(k["c"]),
            "volume": float(k["v"]), "close_time": k["T"],
            "is_closed": is_closed,
        }
        existing = self.candles[key]

        if is_closed:
            existing.append(Candle(**candle_data))
            if len(existing) > BOOTSTRAP_CANDLES + 50:
                del existing[0]
            if self.on_candle_close:
                self.on_candle_close(symbol, tf)
        else:
            if existing:
                last = existing[-1]
                last.close = candle_data["close"]
                last.high = max(last.high, candle_data["high"])
                last.low = min(last.low, candle_data["low"])
                last.volume = candle_data["volume"]
                if self.on_candle_update:
                    self.on_candle_update(symbol, tf)

    def get_candles(self, symbol, tf):
        """Lookup candles with case-insensitive timeframe key."""
        key = f"{symbol}_{tf}"
        if key in self.candles:
            return self.candles[key]
        # Case-insensitive fallback
        tf_upper = tf.upper()
        key2 = f"{symbol}_{tf_upper}"
        if key2 in self.candles:
            return self.candles[key2]
        return []

    async def close(self):
        if self.session:
            await self.session.close()

market_data = MarketData()
