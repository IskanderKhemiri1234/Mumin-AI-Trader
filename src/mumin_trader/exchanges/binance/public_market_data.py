from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


BASE_URL = "https://data-api.binance.vision"
ALLOWED_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "BNBUSDT"})


@dataclass(frozen=True, slots=True)
class PublicMarketSnapshot:
    symbol: str
    bid: Decimal
    ask: Decimal
    last_closed_price: Decimal
    candle_close_time_ms: int
    received_at_ms: int
    quote_volume_ratio: Decimal | None = None
    trade_count_ratio: Decimal | None = None
    higher_timeframe_closes: dict[str, tuple[Decimal, ...]] | None = None
    atr_percent: Decimal | None = None
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

    @staticmethod
    def _median(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        ordered = sorted(values)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / Decimal("2")

    async def snapshot(self, symbol: str) -> PublicMarketSnapshot:
        symbol = symbol.upper()
        if symbol not in ALLOWED_SYMBOLS:
            raise ValueError(f"Symbol is not in the paper-trading allowlist: {symbol}")

        book, candles, five_minute, fifteen_minute, server = await asyncio.gather(
            self._get("/api/v3/ticker/bookTicker", {"symbol": symbol}),
            self._get("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 30}),
            self._get("/api/v3/klines", {"symbol": symbol, "interval": "5m", "limit": 45}),
            self._get("/api/v3/klines", {"symbol": symbol, "interval": "15m", "limit": 45}),
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
        close_price = Decimal(str(candle[4]))
        if not close_price.is_finite() or close_price <= 0:
            raise ValueError(f"Invalid closed-candle price for {symbol}")

        # Compare the latest closed candle with the median of the preceding
        # 20 closed candles. Insufficient history deliberately leaves ratios
        # unset so the Anti-Loss Shield can fail closed.
        quote_volume_ratio = None
        trade_count_ratio = None
        baseline = closed[-21:-1]
        if len(baseline) == 20 and all(len(row) > 8 for row in [*baseline, candle]):
            baseline_quote = [
                Decimal(str(row[7])) for row in baseline
                if Decimal(str(row[7])).is_finite() and Decimal(str(row[7])) > 0
            ]
            baseline_trades = [
                Decimal(str(row[8])) for row in baseline
                if Decimal(str(row[8])).is_finite() and Decimal(str(row[8])) > 0
            ]
            median_quote = self._median(baseline_quote)
            median_trades = self._median(baseline_trades)
            current_quote = Decimal(str(candle[7]))
            current_trades = Decimal(str(candle[8]))
            if median_quote and median_quote > 0 and current_quote > 0:
                quote_volume_ratio = current_quote / median_quote
            if median_trades and median_trades > 0 and current_trades > 0:
                trade_count_ratio = current_trades / median_trades

        def closed_closes(rows: list[Any]) -> tuple[Decimal, ...]:
            return tuple(
                Decimal(str(row[4])) for row in rows
                if len(row) > 6 and int(row[6]) < received_at_ms
                and Decimal(str(row[4])).is_finite() and Decimal(str(row[4])) > 0
            )

        one_minute_closed = [row for row in closed if len(row) > 8]
        true_ranges: list[Decimal] = []
        for index in range(1, len(one_minute_closed)):
            current = one_minute_closed[index]
            previous_close = Decimal(str(one_minute_closed[index - 1][4]))
            high = Decimal(str(current[2]))
            low = Decimal(str(current[3]))
            true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        atr_percent = (
            sum(true_ranges[-14:], Decimal("0")) / Decimal(len(true_ranges[-14:]))
            / close_price * Decimal("100")
            if true_ranges else None
        )

        return PublicMarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last_closed_price=close_price,
            candle_close_time_ms=candle_close_time_ms,
            received_at_ms=received_at_ms,
            quote_volume_ratio=quote_volume_ratio,
            trade_count_ratio=trade_count_ratio,
            higher_timeframe_closes={
                "higher_1": closed_closes(five_minute),
                "higher_2": closed_closes(fifteen_minute),
            },
            atr_percent=atr_percent,
        )

    async def snapshots(self, symbols: tuple[str, ...] = tuple(sorted(ALLOWED_SYMBOLS))) -> tuple[dict[str, PublicMarketSnapshot], dict[str, str]]:
        results = await asyncio.gather(
            *(self.snapshot(symbol) for symbol in symbols),
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
