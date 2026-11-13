import sqlite3
from decimal import Decimal

from scripts.run_continuous_paper import (
    SYMBOLS,
    ContinuousPaperBot,
)


def test_continuous_paper_loop_persists_multi_symbol_portfolio(tmp_path):
    database = tmp_path / "continuous.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("50.00"))

    for _ in range(40):
        result = bot.step()

    report = bot.save_hourly_report()
    reopened = ContinuousPaperBot(str(database), initial_cash=Decimal("50.00"))
    with sqlite3.connect(database) as db:
        tick_count = db.execute("SELECT COUNT(*) FROM bot_equity").fetchone()[0]
        reports = db.execute("SELECT COUNT(*) FROM bot_reports").fetchone()[0]
        fills = db.execute("SELECT COUNT(*) FROM bot_fills").fetchone()[0]
        market_ticks = db.execute("SELECT COUNT(*) FROM bot_market_ticks").fetchone()[0]
        symbols = {
            row[0] for row in db.execute("SELECT DISTINCT symbol FROM bot_market_ticks")
        }
        snapshots = db.execute(
            "SELECT COUNT(*) FROM bot_portfolio_snapshots"
        ).fetchone()[0]
        sources = {
            row[0] for row in db.execute("SELECT DISTINCT data_source FROM bot_fills")
        }

    assert result.tick == 40
    assert tick_count == 40
    assert reports == 1
    assert fills >= 2
    assert market_ticks == 40 * len(SYMBOLS)
    assert symbols == set(SYMBOLS)
    assert snapshots == 40
    assert sources <= {"MOCK_SYNTHETIC"}
    assert report["equity"] > 0
    assert report["cash"] >= 0
    assert reopened.report()["tick"] == 40
    assert report["fees"] >= 0


def test_synthetic_prices_are_repeatable_and_symbol_specific():
    for symbol in SYMBOLS:
        first = ContinuousPaperBot.synthetic_price_for(symbol, 10)
        second = ContinuousPaperBot.synthetic_price_for(symbol, 10)
        assert first == second
        assert first > 0
    assert len({ContinuousPaperBot.synthetic_price_for(s, 10) for s in SYMBOLS}) == 3


def test_public_source_is_recorded_separately_from_synthetic(tmp_path):
    database = tmp_path / "public-paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    prices = {
        "BTCUSDT": Decimal("61000"),
        "ETHUSDT": Decimal("3100"),
        "BNBUSDT": Decimal("610"),
    }
    quotes = {
        symbol: (price - Decimal("0.1"), price + Decimal("0.1"))
        for symbol, price in prices.items()
    }
    bot.step(
        prices,
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes=quotes,
        candle_close_times={symbol: 1_800_000_000_000 for symbol in SYMBOLS},
        received_at_ms=1_800_000_000_100,
    )
    with sqlite3.connect(database) as db:
        recorded = db.execute(
            "SELECT DISTINCT data_source FROM bot_market_ticks"
        ).fetchall()
        count = db.execute("SELECT COUNT(*) FROM bot_market_ticks").fetchone()[0]
    assert {row[0] for row in recorded} == {"BINANCE_PUBLIC_MARKET_DATA"}
    assert count == 3


def test_repeated_public_candle_does_not_create_false_momentum(tmp_path):
    bot = ContinuousPaperBot(str(tmp_path / "dedupe.sqlite3"))
    candle_close = 1_800_000_000_000
    for value in ("100", "101", "102", "103"):
        price = Decimal(value)
        result = bot.step(
            {"BTCUSDT": price},
            source="BINANCE_PUBLIC_MARKET_DATA",
            quotes={"BTCUSDT": (price - Decimal("0.01"), price + Decimal("0.01"))},
            candle_close_times={"BTCUSDT": candle_close},
            received_at_ms=candle_close + 1000,
        )
    assert result.signal == "BTCUSDT:HOLD"


def test_stop_loss_uses_net_exit_value_after_fees_and_slippage(tmp_path):
    database = tmp_path / "net-stop.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    with sqlite3.connect(database) as db:
        db.execute(
            """UPDATE bot_account SET cash='19.95' WHERE id=1"""
        )
        db.execute(
            """INSERT INTO bot_positions
               (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
               VALUES ('BTCUSDT','0.5','100','50','0.05','2026-11-13T00:00:00+00:00')"""
        )

    bot.step(
        {"BTCUSDT": Decimal("99.4")},
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes={"BTCUSDT": (Decimal("99.4"), Decimal("99.5"))},
        candle_close_times={"BTCUSDT": 1_800_000_000_000},
        received_at_ms=1_800_000_000_100,
    )

    with sqlite3.connect(database) as db:
        remaining = db.execute(
            "SELECT COUNT(*) FROM bot_positions WHERE symbol='BTCUSDT'"
        ).fetchone()[0]
        exit_row = db.execute(
            "SELECT net_pnl FROM bot_fills WHERE symbol='BTCUSDT' AND side='SELL'"
        ).fetchone()
    assert remaining == 0
    assert exit_row is not None
    assert Decimal(exit_row[0]) <= Decimal("-0.25")


def test_daily_loss_halts_entries_before_another_symbol_can_open(tmp_path):
    database = tmp_path / "daily-halt.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bot_account SET cash='21.952' WHERE id=1")
        db.execute(
            """INSERT INTO bot_positions
               (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
               VALUES ('BTCUSDT','0.0008','60000','48','0.048','2026-11-13T00:00:00+00:00')"""
        )
        for tick, price in enumerate(("100", "100.1", "100.2", "100.3"), start=-3):
            db.execute(
                """INSERT INTO bot_market_ticks
                   (tick,symbol,price,bid,ask,data_source,candle_close_time_ms,received_at_ms)
                   VALUES (?, 'ETHUSDT', ?, ?, ?, 'BINANCE_PUBLIC_MARKET_DATA', ?, ?)""",
                (tick, price, price, price, 1_800_000_000_000 + tick, 1_800_000_000_100),
            )

    bot.step(
        {"BTCUSDT": Decimal("58000"), "ETHUSDT": Decimal("100.4")},
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes={
            "BTCUSDT": (Decimal("58000"), Decimal("58001")),
            "ETHUSDT": (Decimal("100.39"), Decimal("100.41")),
        },
        candle_close_times={
            "BTCUSDT": 1_800_000_000_010,
            "ETHUSDT": 1_800_000_000_010,
        },
        received_at_ms=1_800_000_000_100,
    )

    with sqlite3.connect(database) as db:
        account = db.execute(
            "SELECT halted, halt_reason FROM bot_account WHERE id=1"
        ).fetchone()
        eth_buys = db.execute(
            "SELECT COUNT(*) FROM bot_fills WHERE symbol='ETHUSDT' AND side='BUY'"
        ).fetchone()[0]
    assert account == (1, "MAX_DAILY_LOSS")
    assert eth_buys == 0
