from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class MockQuote:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal
    source: str = "MOCK_SYNTHETIC_NOT_MARKET_DATA"


class MockExchange:
    """Deterministic, offline market fixture. No credentials, sockets or HTTP calls."""

    def __init__(self) -> None:
        self._quotes = {
            "BTCUSDT": MockQuote(
                symbol="BTCUSDT",
                bid=Decimal("60000.00"),
                ask=Decimal("60006.00"),
                last=Decimal("60003.00"),
            )
        }

    def quote(self, symbol: str) -> MockQuote:
        try:
            return self._quotes[symbol.upper()]
        except KeyError as exc:
            raise ValueError(f"No deterministic mock quote configured for {symbol}.") from exc

    def set_quote(self, quote: MockQuote) -> None:
        if quote.bid <= 0 or quote.ask < quote.bid or quote.last <= 0:
            raise ValueError("Mock quote prices are invalid.")
        self._quotes[quote.symbol.upper()] = quote
