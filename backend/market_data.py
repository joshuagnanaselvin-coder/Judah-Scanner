"""REST bootstrap + Binance WebSocket client + candle builder."""
import asyncio
import collections
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
        self._lock = asyncio.Lock()
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

        # Download ALL timeframes (including 15M for D2) — WS only provides
        # live ticks, so historical 15M candles MUST come from REST bootstrap.
        all_pairs = [(s, tf) for s in symbols for tf in ALL_TIMEFRAMES]
        total = len(all_pairs)
        print(f"[marketdata] All-TF bootstrap: {total} requests "
              f"({len(ALL_TIMEFRAMES)} TFs x {len(symbols)} pairs)")

        # === Adaptive batch-wise concurrent bootstrap ===
        # Binance IP limit: 1200 req/min (weight=1 for klines).
        # 50 concurrent + 2.5s delay = ~1000/min safely under limit.
        BATCH_SIZE = 50
        BASE_DELAY = 2.5
        MAX_DELAY = 5.0
        DELAY_STEP = 1.0

        batch_delay = BASE_DELAY
        errors = 0

        batches = [all_pairs[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
        total_batches = len(batches)

        for batch_idx, batch in enumerate(batches):
            tasks = [self._fetch_klines_with_retry(sym, tf, BOOTSTRAP_CANDLES)
                     for sym, tf in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_fails = 0
            for (sym, tf), result in zip(batch, results):
                if isinstance(result, Exception):
                    errors += 1
                    batch_fails += 1
                    continue
                if result and len(result) >= 25:
                    key = f"{sym}_{tf}"
                    self.candles[key] = collections.deque(result, maxlen=BOOTSTRAP_CANDLES + 50)
                    count += 1
                else:
                    errors += 1
                    batch_fails += 1

            done = min((batch_idx + 1) * BATCH_SIZE, total)
            pct = done / total * 100
            print(f"[marketdata] {done}/{total} ({pct:.0f}%) — {count} OK, {errors} failed | "
                  f"delay={batch_delay:.1f}s", flush=True)

            # Adaptive: if >50% of batch failed, we're being rate-limited
            if batch_fails > len(batch) * 0.5:
                batch_delay = min(batch_delay + DELAY_STEP, MAX_DELAY)
            elif batch_fails == 0 and batch_delay > BASE_DELAY:
                # Recover: ease back toward normal delay
                batch_delay = max(batch_delay - DELAY_STEP * 0.5, BASE_DELAY)

            if batch_idx < total_batches - 1:
                await asyncio.sleep(batch_delay)

        print(f"[marketdata] Bootstrapped {count}/{total} candle sets ({errors} failed) "
              f"| delay={batch_delay:.1f}s", flush=True)
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
                if resp.status == 429:
                    logger.warning(f"Rate limited (429) for {symbol} {binance_tf} — backing off")
                    return "RATE_LIMITED"
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

    async def _fetch_klines_with_retry(self, symbol, interval, limit, max_retries=4):
        """Fetch with retry on rate-limit (429) and server errors (5xx).

        Adds jitter to backoff to avoid thundering herd when many requests
        fail simultaneously (e.g., burst of 40 concurrent requests).
        """
        import random
        for attempt in range(max_retries):
            result = await self._fetch_klines(symbol, interval, limit)
            if result and result != "RATE_LIMITED":
                return result
            if attempt < max_retries - 1:
                # 429 gets a longer backoff; other errors get shorter
                if result == "RATE_LIMITED":
                    wait = (2 ** attempt) * 1.0 + random.uniform(0, 0.5)  # 1s, 2s, 4s
                else:
                    wait = (2 ** attempt) * 0.5 + random.uniform(0, 0.5)  # 0.5s, 1s, 2s
                logger.debug(f"Retry {attempt+1}/{max_retries} for {symbol} {interval} in {wait:.1f}s")
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
                            await self._handle_kline(json.loads(msg.data))
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception as e:
                logger.error(f"[ws] Conn {conn_id} error: {e}")
                self.ws_connected = False
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 60)

    async def _handle_kline(self, msg):
        """Thread-safe kline handler — mutations go through the lock."""
        if msg.get("e") != "kline": return

        k = msg["k"]
        symbol, tf_raw = msg["s"], k["i"]
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

        async with self._lock:
            existing = self.candles[key]

            if is_closed:
                existing.append(Candle(**candle_data))
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
        """Lookup candles — returns a list (slice-safe).

        Internal storage is a deque for O(1) append; we convert to tuple
        here so callers can safely use slice syntax ([-30:], [-60:], etc.)
        without hitting TypeError on deque.
        """
        key = f"{symbol}_{tf}"
        if key in self.candles:
            return tuple(self.candles[key])
        tf_upper = tf.upper()
        key2 = f"{symbol}_{tf_upper}"
        if key2 in self.candles:
            return tuple(self.candles[key2])
        return []

    async def close(self):
        if self.session:
            await self.session.close()

market_data = MarketData()
