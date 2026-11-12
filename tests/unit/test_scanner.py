from decimal import Decimal

from mumin_trader.market_scanner.scanner import screen_tickers


def test_scanner_filters_quote_and_volume():
    rows = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000"},
        {"symbol": "ETHBTC", "quoteVolume": "999999"},
        {"symbol": "ETHUSDT", "quoteVolume": "500"},
    ]
    result = screen_tickers(rows, "USDT", Decimal("700"))
    assert [x["symbol"] for x in result] == ["BTCUSDT"]
