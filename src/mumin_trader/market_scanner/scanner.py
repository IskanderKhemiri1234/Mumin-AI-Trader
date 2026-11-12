from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient

ZERO = Decimal("0")
BPS = Decimal("10000")


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class MarketCandidate:
    symbol: str
    bid: Decimal
    ask: Decimal
    spread_bps: Decimal
    quote_volume: Decimal
    change_percent: Decimal
    last_price: Decimal = ZERO
    signal: str = "HOLD"
    rsi: Decimal | None = None
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    expected_move_bps: Decimal = ZERO


def screen_tickers(
    tickers: list[dict[str, Any]],
    quote_asset: str = "USDT",
    min_quote_volume: Decimal = ZERO,
) -> list[dict[str, Any]]:
    """Liquidity-only filter; this function does not create a trade signal."""
    quote_asset = quote_asset.upper()
    selected = []
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        volume = _decimal(item.get("quoteVolume", "0"))
        if (
            symbol.endswith(quote_asset)
            and volume is not None
            and volume >= min_quote_volume
        ):
            selected.append(item)
    return sorted(
        selected,
        key=lambda row: _decimal(row.get("quoteVolume", 0)) or ZERO,
        reverse=True,
    )


def _ema(values: list[Decimal], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError("Not enough candles for EMA.")
    multiplier = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], ZERO) / Decimal(period)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def _rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, ZERO) for change in changes[-period:]]
    losses = [max(-change, ZERO) for change in changes[-period:]]
    average_gain = sum(gains, ZERO) / Decimal(period)
    average_loss = sum(losses, ZERO) / Decimal(period)
    if average_loss == ZERO:
        return Decimal("100") if average_gain > ZERO else Decimal("50")
    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal(1) + relative_strength))


class MarketScanner:
    """Spot universe scanner using public Binance market data.

    Signals are deliberately conservative research candidates, not a claim of
    predictive accuracy. The caller must still apply fees, slippage and risk gates.
    """

    def __init__(
        self,
        quote_asset: str = "USDT",
        min_quote_volume: Decimal = Decimal("1000000"),
        max_spread_bps: Decimal = Decimal("12"),
        allowed_symbols: set[str] | None = None,
        interval: str = "5m",
        candle_limit: int = 100,
        max_symbols: int = 30,
        min_expected_move_bps: Decimal = Decimal("35"),
    ) -> None:
        self.quote_asset = quote_asset.upper()
        self.min_quote_volume = Decimal(min_quote_volume)
        self.max_spread_bps = Decimal(max_spread_bps)
        self.allowed_symbols = {s.upper() for s in allowed_symbols} if allowed_symbols else None
        self.interval = interval
        self.candle_limit = max(60, min(int(candle_limit), 500))
        self.max_symbols = max(1, min(int(max_symbols), 100))
        self.min_expected_move_bps = Decimal(min_expected_move_bps)
        self._book: dict[str, tuple[Decimal, Decimal]] = {}
        self._volume: dict[str, tuple[Decimal, Decimal]] = {}

    def ingest(self, event: dict[str, Any]) -> MarketCandidate | None:
        """Accept a combined ticker/bookTicker-like event for stream integrations."""
        symbol = str(event.get("s", event.get("symbol", ""))).upper()
        if not symbol or not symbol.endswith(self.quote_asset):
            return None
        if self.allowed_symbols is not None and symbol not in self.allowed_symbols:
            return None
        volume = _decimal(event.get("q", event.get("quoteVolume")))
        change = _decimal(event.get("P", event.get("priceChangePercent", "0")))
        bid = _decimal(event.get("b", event.get("bidPrice")))
        ask = _decimal(event.get("a", event.get("askPrice")))
        if volume is not None:
            self._volume[symbol] = (volume, change or ZERO)
        if bid is not None and ask is not None and bid > ZERO and ask >= bid:
            self._book[symbol] = (bid, ask)
        if symbol not in self._volume or symbol not in self._book:
            return None
        quote_volume, change_percent = self._volume[symbol]
        bid, ask = self._book[symbol]
        if quote_volume < self.min_quote_volume:
            return None
        spread = ((ask - bid) / ((ask + bid) / Decimal(2))) * BPS
        if spread > self.max_spread_bps:
            return None
        return MarketCandidate(
            symbol=symbol,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            quote_volume=quote_volume,
            change_percent=change_percent,
            last_price=(bid + ask) / Decimal(2),
        )

    async def scan(self, client: BinanceSpotRESTClient) -> list[MarketCandidate]:
        """Fetch the active Spot universe, rank liquid pairs, then score closed candles."""
        exchange_info = await client.exchange_info()
        tickers = await client.ticker_24h()
        if not isinstance(tickers, list):
            raise RuntimeError("Binance 24h ticker endpoint returned an unexpected payload.")

        trading_symbols = {
            str(item.get("symbol", "")).upper()
            for item in exchange_info.get("symbols", [])
            if item.get("status") == "TRADING"
            and item.get("isSpotTradingAllowed", True)
            and item.get("quoteAsset", "").upper() == self.quote_asset
        }
        rows = [
            row for row in tickers
            if str(row.get("symbol", "")).upper() in trading_symbols
            and (self.allowed_symbols is None or str(row.get("symbol", "")).upper() in self.allowed_symbols)
        ]
        rows = screen_tickers(rows, self.quote_asset, self.min_quote_volume)[: self.max_symbols]
        candidates: list[MarketCandidate] = []

        for row in rows:
            symbol = str(row["symbol"]).upper()
            bid = _decimal(row.get("bidPrice"))
            ask = _decimal(row.get("askPrice"))
            last = _decimal(row.get("lastPrice"))
            volume = _decimal(row.get("quoteVolume"))
            change = _decimal(row.get("priceChangePercent", "0")) or ZERO
            if bid is None or ask is None or last is None or volume is None:
                continue
            if bid <= ZERO or ask < bid or last <= ZERO:
                continue
            spread = ((ask - bid) / ((ask + bid) / Decimal(2))) * BPS
            if spread > self.max_spread_bps:
                continue

            candles = await client.klines(symbol, interval=self.interval, limit=self.candle_limit)
            closes = [
                value for candle in candles[:-1]
                if len(candle) > 4 and (value := _decimal(candle[4])) is not None and value > ZERO
            ]
            if len(closes) < 30:
                continue
            fast = _ema(closes, 9)
            slow = _ema(closes, 21)
            rsi = _rsi(closes, 14)
            momentum_bps = ((closes[-1] / closes[-6]) - Decimal(1)) * BPS if len(closes) >= 6 else ZERO

            # A candidate requires trend alignment, positive short momentum and
            # RSI below an overbought threshold. This is a filter, not a guarantee.
            signal = "HOLD"
            if fast > slow and momentum_bps > Decimal("5") and rsi is not None and Decimal("45") <= rsi <= Decimal("68"):
                signal = "BUY_CANDIDATE"
            candidates.append(
                MarketCandidate(
                    symbol=symbol,
                    bid=bid,
                    ask=ask,
                    spread_bps=spread,
                    quote_volume=volume,
                    change_percent=change,
                    last_price=last,
                    signal=signal,
                    rsi=rsi,
                    ema_fast=fast,
                    ema_slow=slow,
                    expected_move_bps=max(momentum_bps, ZERO),
                )
            )
        return self.rank(candidates)

    @staticmethod
    def rank(candidates: list[MarketCandidate]) -> list[MarketCandidate]:
        """Prioritize eligible signals, liquidity and low spread."""
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.signal == "BUY_CANDIDATE",
                candidate.quote_volume / (Decimal(1) + candidate.spread_bps),
            ),
            reverse=True,
        )
