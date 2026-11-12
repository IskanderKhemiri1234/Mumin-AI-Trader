from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketCandidate:
    symbol: str
    bid: Decimal
    ask: Decimal
    spread_bps: Decimal
    quote_volume: Decimal
    change_percent: Decimal


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def screen_tickers(
    tickers: list[dict[str, Any]],
    quote_asset: str = "USDT",
    min_quote_volume: Decimal = Decimal("0"),
) -> list[dict[str, Any]]:
    """Liquidity-only filter; it is not a buy/sell signal."""
    quote_asset = quote_asset.upper()
    selected = []
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        volume = _decimal(item.get("quoteVolume", "0"))
        if symbol.endswith(quote_asset) and volume is not None and volume >= min_quote_volume:
            selected.append(item)
    return sorted(selected, key=lambda row: _decimal(row.get("quoteVolume", 0)) or Decimal(0), reverse=True)


class MarketScanner:
    """Joins 24h ticker liquidity and best bid/ask observations."""

    def __init__(
        self,
        quote_asset: str = "USDT",
        min_quote_volume: Decimal = Decimal("1000000"),
        max_spread_bps: Decimal = Decimal("20"),
        allowed_symbols: set[str] | None = None,
    ) -> None:
        self.quote_asset = quote_asset.upper()
        self.min_quote_volume = min_quote_volume
        self.max_spread_bps = max_spread_bps
        self.allowed_symbols = {s.upper() for s in allowed_symbols} if allowed_symbols else None
        self._book: dict[str, tuple[Decimal, Decimal]] = {}
        self._volume: dict[str, tuple[Decimal, Decimal]] = {}

    def ingest(self, event: dict[str, Any]) -> MarketCandidate | None:
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
            self._volume[symbol] = (volume, change or Decimal(0))
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            self._book[symbol] = (bid, ask)
        if symbol not in self._volume or symbol not in self._book:
            return None
        quote_volume, change_percent = self._volume[symbol]
        bid, ask = self._book[symbol]
        if quote_volume < self.min_quote_volume or bid <= 0:
            return None
        spread_bps = ((ask - bid) / ((ask + bid) / 2)) * Decimal("10000")
        if spread_bps > self.max_spread_bps:
            return None
        return MarketCandidate(symbol, bid, ask, spread_bps, quote_volume, change_percent)

    @staticmethod
    def rank(candidates: list[MarketCandidate]) -> list[MarketCandidate]:
        """Rank by liquidity with a spread penalty; not a trade signal."""
        return sorted(candidates, key=lambda c: c.quote_volume / (Decimal(1) + c.spread_bps), reverse=True)
