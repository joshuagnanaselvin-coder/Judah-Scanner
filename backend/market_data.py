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
    BOOTSTRAP_CANDLES, TIMEFRAMES_HTF, ALL_TIMEFRAMES
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

        # Build all tasks: flat list of (symbol, tf) pairs
        # Bootstrap HTF first (D1), then LTF (D2) in next batches
        hf_pairs = []
        lf_pairs = []
        for symbol in symbols:
            for tf in TIMEFRAMES_HTF:
                hf_pairs.append((symbol, tf))
            for tf in ALL_TIMEFRAMES:
                if tf not in TIMEFRAMES_HTF:
                    lf_pairs.append((symbol, tf))

        task_pairs = hf_pairs + lf_pairs

        batch_size = 30
        total = len(task_pairs)
        total_batches = (total + batch_size - 1) // batch_size
        print(f"[marketdata] {len(symbols)} pairs x {len(TIMEFRAMES_HTF)} HTF + {len(ALL_TIMEFRAMES) - len(TIMEFRAMES_HTF)} LTF = {total} requests ({total_batches} batches @ {batch_size}/batch)")

        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total)
            batch = task_pairs[start:end]

            # Create tasks for this batch only
            tasks = [self._fetch_klines(sym, tf, BOOTSTRAP_CANDLES) for sym, tf in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Map results back using enumerate (local index within batch)
            for local_idx, result in enumerate(results):
                global_idx = start + local_idx
                symbol, tf = task_pairs[global_idx]
                if isinstance(result, Exception):
                    continue
                if result and len(result) >= 50:
                    key = f"{symbol}_{tf}"
                    self.candles[key] = result
                    count += 1

            if (batch_idx + 1) % 10 == 0:
                print(f"[marketdata] Batch {batch_idx + 1}/{total_batches} — {count} candle sets so far")

            if batch_idx < total_batches - 1:
                await asyncio.sleep(1)

        print(f"[marketdata] Bootstrapped {count} candle sets")
        return count

    async def _fetch_klines(self, symbol, interval, limit):
        binance_tf = _binance_interval(interval)
        url = f"{BINANCE_REST_BASE}/klines?symbol={symbol}&interval={binance_tf}&limit={limit}"
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.warning(f"HTTP {resp.status} for {symbol} {interval}: {text[:200]}")
                    return []
                data = json.loads(text)
                if not isinstance(data, list):
                    logger.warning(f"Unexpected response for {symbol} {interval}: {text[:200]}")
                    return []
                return [Candle(time=k[0], open=float(k[1]), high=float(k[2]),
                               low=float(k[3]), close=float(k[4]), volume=float(k[5]),
                               close_time=k[6], is_closed=True) for k in data]
        except Exception as e:
            logger.warning(f"Fetch error {symbol} {interval}: {e}")
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
