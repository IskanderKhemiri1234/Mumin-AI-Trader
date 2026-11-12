import json
from decimal import Decimal

from mumin_trader.exchanges.binance.websocket_client import BinanceWebSocketClient
from mumin_trader.market_scanner.scanner import MarketScanner


def test_websocket_decodes_combined_stream_payload():
    event = {"stream": "btcusdt@bookTicker", "data": {"s": "BTCUSDT", "b": "60000", "a": "60001"}}
    assert BinanceWebSocketClient.decode_message(json.dumps(event)) == event["data"]


def test_scanner_requires_both_liquidity_and_book_data():
    scanner = MarketScanner(min_quote_volume=Decimal("100000"), max_spread_bps=Decimal("5"))
    assert scanner.ingest({"s": "BTCUSDT", "q": "200000", "P": "1.2"}) is None
    candidate = scanner.ingest({"s": "BTCUSDT", "b": "60000", "a": "60001"})
    assert candidate is not None and candidate.symbol == "BTCUSDT"
    assert candidate.spread_bps < Decimal("5")


def test_scanner_rejects_wide_spread():
    scanner = MarketScanner(min_quote_volume=Decimal("1"), max_spread_bps=Decimal("2"))
    scanner.ingest({"s": "ETHUSDT", "q": "100000", "P": "0.2"})
    assert scanner.ingest({"s": "ETHUSDT", "b": "100", "a": "101"}) is None
