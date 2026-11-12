from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
import websockets

from mumin_trader.exchanges.binance.public_market_data import ALLOWED_SYMBOLS
from mumin_trader.strategy.smart_sniper import order_book_imbalance, volume_surge_ratio

REST_BASE_URL = "https://testnet.binance.vision/api"
STREAM_BASE_URL = "wss://stream.testnet.binance.vision/stream"
SOURCE = "BINANCE_TESTNET_WEBSOCKET"


@dataclass(frozen=True, slots=True)
class TestnetMarketTick:
    symbol: str
    price: Decimal
    bid: Decimal
    ask: Decimal
    volume_ratio: Decimal
    book_imbalance: Decimal
    event_time_ms: int
    source: str = SOURCE


@dataclass(slots=True)
class _SymbolState:
    symbol: str
    price: Decimal = Decimal("0")
    bid: Decimal = Decimal("0")
    ask: Decimal = Decimal("0")
    bid_levels: dict[Decimal, Decimal] = field(default_factory=dict)
    ask_levels: dict[Decimal, Decimal] = field(default_factory=dict)
    closed_volumes: deque[Decimal] = field(default_factory=lambda: deque(maxlen=21))
    volume_ratio: Decimal = Decimal("0")
    last_market_event_ms: int = 0

    @property
    def imbalance(self) -> Decimal:
        bid_value = sum(
            (price * quantity for price, quantity in self.bid_levels.items()),
            Decimal("0"),
        )
        ask_value = sum(
            (price * quantity for price, quantity in self.ask_levels.items()),
            Decimal("0"),
        )
        if bid_value == 0 and self.bid > 0:
            bid_value = self.bid
        if ask_value == 0 and self.ask > 0:
            ask_value = self.ask
        return order_book_imbalance(bid_value, ask_value)

    def snapshot(self, event_time_ms: int) -> TestnetMarketTick | None:
        if self.price <= 0 or self.bid <= 0 or self.ask < self.bid:
            return None
        if event_time_ms - self.last_market_event_ms > 10_000:
            return None
        return TestnetMarketTick(
            symbol=self.symbol,
            price=self.price,
            bid=self.bid,
            ask=self.ask,
            volume_ratio=self.volume_ratio,
            book_imbalance=self.imbalance,
            event_time_ms=event_time_ms,
        )


class BinanceSpotTestnetStream:
    """Unauthenticated Spot Testnet market-data stream; no account or order methods."""

    def __init__(
        self,
        symbols: tuple[str, ...] = tuple(sorted(ALLOWED_SYMBOLS)),
        client: httpx.AsyncClient | None = None,
        reconnect_min_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
    ) -> None:
        normalized = tuple(symbol.upper() for symbol in symbols)
        if not normalized or any(symbol not in ALLOWED_SYMBOLS for symbol in normalized):
            raise ValueError("Testnet symbols must be in the configured Spot allowlist.")
        self.symbols = normalized
        self._client = client or httpx.AsyncClient(
            base_url=REST_BASE_URL,
            timeout=httpx.Timeout(8.0),
            headers={"Accept": "application/json"},
        )
        self._owns_client = client is None
        self.reconnect_min_seconds = reconnect_min_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self._states = {symbol: _SymbolState(symbol) for symbol in normalized}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def bootstrap(self) -> None:
        """Seed price, quote, depth and closed-volume baseline before WS events."""
        async def seed(symbol: str) -> None:
            book, depth, candles, ticker = await asyncio.gather(
                self._get("/v3/ticker/bookTicker", {"symbol": symbol}),
                self._get("/v3/depth", {"symbol": symbol, "limit": 20}),
                self._get("/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 21}),
                self._get("/v3/ticker/price", {"symbol": symbol}),
            )
            state = self._states[symbol]
            state.bid = Decimal(str(book["bidPrice"]))
            state.ask = Decimal(str(book["askPrice"]))
            state.price = Decimal(str(ticker["price"]))
            state.bid_levels = {
                Decimal(str(price)): Decimal(str(quantity))
                for price, quantity in depth.get("bids", [])
                if Decimal(str(quantity)) > 0
            }
            state.ask_levels = {
                Decimal(str(price)): Decimal(str(quantity))
                for price, quantity in depth.get("asks", [])
                if Decimal(str(quantity)) > 0
            }
            server_time = int(await self._get("/v3/time", {})["serverTime"]) if False else int(time.time() * 1000)
            state.closed_volumes.clear()
            for row in candles:
                if len(row) > 6 and int(row[6]) < server_time:
                    state.closed_volumes.append(Decimal(str(row[5])))
            state.volume_ratio = volume_surge_ratio(list(state.closed_volumes))
            state.last_market_event_ms = server_time
            if state.price <= 0 or state.bid <= 0 or state.ask < state.bid:
                raise ValueError(f"Invalid Testnet REST bootstrap snapshot for {symbol}")

        results = await asyncio.gather(
            *(seed(symbol) for symbol in self.symbols),
            return_exceptions=True,
        )
        failures = [
            f"{symbol}: {result}"
            for symbol, result in zip(self.symbols, results)
            if isinstance(result, BaseException)
        ]
        if failures:
            raise RuntimeError("Testnet REST bootstrap incomplete: " + "; ".join(failures))

    def _handle_message(self, envelope: dict[str, Any]) -> TestnetMarketTick | None:
        data = envelope.get("data", envelope)
        if not isinstance(data, dict):
            return None
        event_type = data.get("e")
        symbol = str(data.get("s", "")).upper()
        if symbol not in self._states:
            return None
        state = self._states[symbol]
        now_ms = int(data.get("E") or data.get("T") or time.time() * 1000)

        if event_type == "trade":
            state.price = Decimal(str(data["p"]))
            state.last_market_event_ms = now_ms
            return state.snapshot(now_ms)

        if "b" in data and "a" in data and "s" in data and event_type is None:
            state.bid = Decimal(str(data["b"]))
            state.ask = Decimal(str(data["a"]))
            state.last_market_event_ms = now_ms
            return None

        if "lastUpdateId" in data and "bids" in data and "asks" in data:
            state.bid_levels = {
                Decimal(str(price)): Decimal(str(quantity))
                for price, quantity in data["bids"]
                if Decimal(str(quantity)) > 0
            }
            state.ask_levels = {
                Decimal(str(price)): Decimal(str(quantity))
                for price, quantity in data["asks"]
                if Decimal(str(quantity)) > 0
            }
            state.last_market_event_ms = now_ms
            return None

        if event_type == "kline":
            candle = data.get("k", {})
            if candle.get("x") is True:
                state.closed_volumes.append(Decimal(str(candle["v"])))
                state.volume_ratio = volume_surge_ratio(list(state.closed_volumes))
            return None

        return None

    async def stream(self) -> AsyncIterator[TestnetMarketTick]:
        """Reconnect forever; reconnects are preceded by a fresh REST bootstrap."""
        stream_names = []
        for symbol in self.symbols:
            key = symbol.lower()
            stream_names.extend((
                f"{key}@trade",
                f"{key}@bookTicker",
                f"{key}@depth20@100ms",
                f"{key}@kline_1m",
            ))
        url = f"{STREAM_BASE_URL}?streams={quote('/'.join(stream_names), safe='@/')}"
        delay = self.reconnect_min_seconds

        while True:
            try:
                await self.bootstrap()
                async with websockets.connect(
                    url,
                    open_timeout=10,
                    close_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=1000,
                ) as socket:
                    delay = self.reconnect_min_seconds
                    async for raw_message in socket:
                        envelope = json.loads(raw_message)
                        if not isinstance(envelope, dict):
                            continue
                        tick = self._handle_message(envelope)
                        if tick is not None:
                            yield tick
            except asyncio.CancelledError:
                raise
            except (OSError, httpx.HTTPError, ValueError, RuntimeError, websockets.WebSocketException) as exc:
                print(
                    f"TESTNET STREAM DISCONNECTED: {type(exc).__name__}: {exc}; "
                    f"reconnecting in {delay:.1f}s",
                    flush=True,
                )
                await asyncio.sleep(delay)
                delay = min(self.reconnect_max_seconds, delay * 2)
