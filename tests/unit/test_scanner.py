from decimal import Decimal

from mumin_trader.market_scanner.scanner import (
    MarketCandidate,
    MarketScanner,
    _closed_ohlcv,
    screen_tickers,
)


def test_scanner_filters_quote_and_volume():
    rows = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000"},
        {"symbol": "ETHBTC", "quoteVolume": "999999"},
        {"symbol": "ETHUSDT", "quoteVolume": "500"},
    ]
    result = screen_tickers(rows, "USDT", Decimal("700"))
    assert [x["symbol"] for x in result] == ["BTCUSDT"]


def test_closed_ohlcv_excludes_live_candle_and_uses_quote_volume():
    candles = [
        [0, "10", "12", "9", "11", "3"],
        [1, "11", "13", "10", "12", "4"],
        [2, "12", "99", "1", "90", "100"],
    ]
    parsed = _closed_ohlcv(candles)
    assert parsed.closes == [Decimal("11"), Decimal("12")]
    assert parsed.highs == [Decimal("12"), Decimal("13")]
    assert parsed.lows == [Decimal("9"), Decimal("10")]
    assert parsed.volumes == [Decimal("33"), Decimal("48")]


def test_scanner_cannot_expand_beyond_fixed_core_universe():
    scanner = MarketScanner(allowed_symbols={"BTCUSDT", "DOGEUSDT"})
    assert scanner.allowed_symbols == {"BTCUSDT"}
    assert scanner.ingest({
        "symbol": "DOGEUSDT", "quoteVolume": "9000000",
        "bidPrice": "1", "askPrice": "1.001",
    }) is None


def test_rank_prefers_approved_and_then_net_edge():
    scanner = MarketScanner()
    low_edge = MarketCandidate(
        "BTCUSDT", Decimal("100"), Decimal("100.01"), Decimal("1"),
        Decimal("9000000"), Decimal("0"), signal="BUY_CANDIDATE",
        strategy_score=90, net_edge_bps=Decimal("15"),
    )
    high_edge = MarketCandidate(
        "ETHUSDT", Decimal("100"), Decimal("100.01"), Decimal("1"),
        Decimal("1000000"), Decimal("0"), signal="BUY_CANDIDATE",
        strategy_score=90, net_edge_bps=Decimal("25"),
    )
    rejected = MarketCandidate(
        "BNBUSDT", Decimal("100"), Decimal("100.01"), Decimal("1"),
        Decimal("9000000"), Decimal("0"), signal="HOLD",
        strategy_score=100, net_edge_bps=Decimal("100"),
    )
    ranked = scanner.rank([low_edge, rejected, high_edge])
    assert [item.symbol for item in ranked] == ["ETHUSDT", "BTCUSDT", "BNBUSDT"]


def test_closed_ohlcv_retains_timestamp_of_last_closed_candle():
    candles = [
        [1000, "10", "12", "9", "11", "3", 1999, "33", 1, "0", "0", "0"],
        [2000, "11", "13", "10", "12", "4", 2999, "48", 1, "0", "0", "0"],
        [3000, "12", "99", "1", "90", "100", 3999, "9000", 1, "0", "0", "0"],
    ]
    parsed = _closed_ohlcv(candles)
    assert parsed.last_closed_open_time_ms == 2000


def test_scanner_accepts_the_15m_entry_timeframe():
    scanner = MarketScanner(allowed_symbols={"BTCUSDT"}, interval="15m")
    assert scanner.interval == "15m"
