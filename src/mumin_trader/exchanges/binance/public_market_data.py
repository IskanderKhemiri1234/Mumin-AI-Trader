from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


BASE_URL = "https://data-api.binance.vision"
ALLOWED_SYMBOLS = frozenset({
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
})


@dataclass(frozen=True, slots=True)
class PublicMarketSnapshot:
    symbol: str
    bid: Decimal
    ask: Decimal
    last_closed_price: Decimal
    candle_close_time_ms: int
    received_at_ms: int
    candle_open: Decimal
    candle_high: Decimal
    candle_low: Decimal
    candle_volume: Decimal
    bid_depth_qty: Decimal
    ask_depth_qty: Decimal
    macro_hourly_closes: tuple[Decimal, ...]
    macro_hourly_close_times_ms: tuple[int, ...]
    source: str = "BINANCE_PUBLIC_MARKET_DATA"


class PublicMarketData:
    """Unauthenticated Binance Spot market-data reader; contains no order methods."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(8.0),
            headers={"Accept": "application/json"},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def snapshot(self, symbol: str) -> PublicMarketSnapshot:
        symbol = symbol.upper()
        if symbol not in ALLOWED_SYMBOLS:
            raise ValueError(f"Symbol is not in the paper-trading allowlist: {symbol}")

        book, candles, hourly_candles, depth, server = await asyncio.gather(
            self._get("/api/v3/ticker/bookTicker", {"symbol": symbol}),
            self._get("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 30}),
            self._get("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 22}),
            self._get("/api/v3/depth", {"symbol": symbol, "limit": 20}),
            self._get("/api/v3/time", {}),
        )
        received_at_ms = int(server["serverTime"])
        bid = Decimal(str(book["bidPrice"]))
        ask = Decimal(str(book["askPrice"]))
        if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid:
            raise ValueError(f"Invalid public order-book quote for {symbol}")

        closed = [row for row in candles if len(row) > 6 and int(row[6]) < received_at_ms]
        if not closed:
            raise ValueError(f"No fully closed one-minute candle for {symbol}")
        candle = closed[-1]
        candle_close_time_ms = int(candle[6])
        if received_at_ms - candle_close_time_ms > 180_000:
            raise ValueError(f"Closed candle is stale for {symbol}")
        open_price, high_price, low_price, close_price, volume = (
            Decimal(str(candle[index])) for index in (1, 2, 3, 4, 5)
        )
        if (not all(value.is_finite() for value in (open_price, high_price, low_price, close_price, volume))
                or low_price <= 0 or high_price < low_price or volume < 0):
            raise ValueError(f"Invalid closed-candle OHLCV for {symbol}")
        closed_hourly = [
            row for row in hourly_candles
            if len(row) > 6 and int(row[6]) < received_at_ms
        ]
        if len(closed_hourly) < 21:
            raise ValueError(f"Insufficient fully closed hourly candles for macro trend: {symbol}")
        latest_hourly_close_ms = int(closed_hourly[-1][6])
        if received_at_ms - latest_hourly_close_ms > 7_200_000:
            raise ValueError(f"Hourly macro-trend data is stale for {symbol}")
        macro_hourly_closes = tuple(Decimal(str(row[4])) for row in closed_hourly[-21:])
        if any(not value.is_finite() or value <= 0 for value in macro_hourly_closes):
            raise ValueError(f"Invalid hourly macro-trend close for {symbol}")

        bid_depth_qty = sum((Decimal(str(level[1])) for level in depth.get("bids", [])[:10]), Decimal("0"))
        ask_depth_qty = sum((Decimal(str(level[1])) for level in depth.get("asks", [])[:10]), Decimal("0"))
        if bid_depth_qty <= 0 or ask_depth_qty <= 0:
            raise ValueError(f"Insufficient public depth for {symbol}")

        return PublicMarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last_closed_price=close_price,
            candle_close_time_ms=candle_close_time_ms,
            received_at_ms=received_at_ms,
            candle_open=open_price,
            candle_high=high_price,
            candle_low=low_price,
            candle_volume=volume,
            bid_depth_qty=bid_depth_qty,
            ask_depth_qty=ask_depth_qty,
            macro_hourly_closes=macro_hourly_closes,
            macro_hourly_close_times_ms=tuple(int(row[6]) for row in closed_hourly[-21:]),
        )

    async def _snapshot_with_retry(
        self, symbol: str, attempts: int = 3
    ) -> PublicMarketSnapshot:
        """Retry transient public-feed failures without substituting synthetic data."""
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self.snapshot(symbol)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        assert last_error is not None
        raise last_error

    async def snapshots(self, symbols: tuple[str, ...] = tuple(sorted(ALLOWED_SYMBOLS))) -> tuple[dict[str, PublicMarketSnapshot], dict[str, str]]:
        results = await asyncio.gather(
            *(self._snapshot_with_retry(symbol) for symbol in symbols),
            return_exceptions=True,
        )
        good: dict[str, PublicMarketSnapshot] = {}
        errors: dict[str, str] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, BaseException):
                errors[symbol] = f"{type(result).__name__}: {result}"
            else:
                good[symbol] = result
        return good, errors


def simulated_fill_price(
    side: str,
    bid: Decimal,
    ask: Decimal,
    slippage_bps: Decimal = Decimal("2"),
) -> Decimal:
    """Conservative paper fill: buy at ask plus slippage; sell at bid minus slippage."""
    slippage = Decimal(slippage_bps) / Decimal("10000")
    if side == "BUY":
        return Decimal(ask) * (Decimal("1") + slippage)
    if side == "SELL":
        return Decimal(bid) * (Decimal("1") - slippage)
    raise ValueError("side must be BUY or SELL")
