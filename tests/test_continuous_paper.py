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
    assert fills == 0  # The synthetic 40-tick series does not clear the net-edge gate.
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


def test_anti_loss_shield_blocks_new_entries_when_liquidity_is_abnormal(tmp_path):
    from types import SimpleNamespace

    bot = ContinuousPaperBot(str(tmp_path / "shield-entry.sqlite3"))
    bot.strategy.assess = lambda *args, **kwargs: SimpleNamespace(signal="BUY")
    with sqlite3.connect(bot.database) as db:
        for tick in range(-30, 0):
            close_time = 1_800_000_000_000 + tick * 60_000
            db.execute(
                """INSERT INTO bot_market_ticks
                   (tick,symbol,price,bid,ask,data_source,candle_close_time_ms,received_at_ms)
                   VALUES (?, 'BTCUSDT', '100', '99.99', '100.01',
                           'BINANCE_PUBLIC_MARKET_DATA', ?, ?)""",
                (tick, close_time, close_time + 1000),
            )

    bot.step(
        {"BTCUSDT": Decimal("100")},
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes={"BTCUSDT": (Decimal("99.99"), Decimal("100.01"))},
        candle_close_times={"BTCUSDT": 1_800_000_000_000},
        received_at_ms=1_800_000_001_000,
        liquidity_metrics={
            "BTCUSDT": (Decimal("0.10"), Decimal("0.10")),
        },
    )
    with sqlite3.connect(bot.database) as db:
        buys = db.execute(
            "SELECT COUNT(*) FROM bot_fills WHERE side='BUY'"
        ).fetchone()[0]
        risk_event = db.execute(
            "SELECT shield_mode, block_entries FROM bot_risk_events WHERE symbol='BTCUSDT'"
        ).fetchone()
    assert buys == 0
    assert risk_event == ("CRITICAL", 1)


def test_anti_loss_shield_reduces_position_in_stages_when_spread_is_executable(tmp_path):
    from types import SimpleNamespace

    database = tmp_path / "shield-derisk.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.strategy.assess = lambda *args, **kwargs: SimpleNamespace(signal="HOLD")
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bot_account SET cash='64.995' WHERE id=1")
        db.execute(
            """INSERT INTO bot_positions
               (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
               VALUES ('BTCUSDT','0.05','100','5','0.005','2026-11-13T00:00:00+00:00')"""
        )
        for tick in range(-30, 0):
            close_time = 1_800_000_000_000 + tick * 60_000
            db.execute(
                """INSERT INTO bot_market_ticks
                   (tick,symbol,price,bid,ask,data_source,candle_close_time_ms,received_at_ms)
                   VALUES (?, 'BTCUSDT', '100', '99.99', '100.01',
                           'BINANCE_PUBLIC_MARKET_DATA', ?, ?)""",
                (tick, close_time, close_time + 1000),
            )

    result = bot.step(
        {"BTCUSDT": Decimal("100")},
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes={"BTCUSDT": (Decimal("99.99"), Decimal("100.01"))},
        candle_close_times={"BTCUSDT": 1_800_000_000_000},
        received_at_ms=1_800_000_001_000,
        liquidity_metrics={
            "BTCUSDT": (Decimal("0.30"), Decimal("0.40")),
        },
    )
    with sqlite3.connect(database) as db:
        remaining = Decimal(db.execute(
            "SELECT quantity FROM bot_positions WHERE symbol='BTCUSDT'"
        ).fetchone()[0])
        sold = Decimal(db.execute(
            "SELECT quantity FROM bot_fills WHERE side='SELL'"
        ).fetchone()[0])
    assert result.action.startswith("BTCUSDT:DE_RISK")
    assert sold == Decimal("0.01250000")
    assert remaining == Decimal("0.03750000")

def test_runtime_compounding_uses_realized_profit_only_and_respects_five_usdt_cap(tmp_path):
    from types import SimpleNamespace

    database = tmp_path / "compounding-runtime.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.strategy.assess = lambda *args, **kwargs: SimpleNamespace(
        signal="BUY", score=Decimal("95"), regime="BULL",
        mtf_confluence=True, atr_percent=Decimal("0.30"),
    )
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE bot_account SET cash='80', realized_pnl='10', high_watermark='80' WHERE id=1"
        )
        for tick in range(-30, 0):
            for symbol in ("BTCUSDT", "ETHUSDT"):
                db.execute(
                    """INSERT INTO bot_market_ticks
                       (tick,symbol,price,bid,ask,data_source,candle_close_time_ms,received_at_ms)
                       VALUES (?, ?, '100', '100', '100', 'MOCK_SYNTHETIC', NULL, ?)""",
                    (tick, symbol, 1_800_000_000_000 + tick * 1000),
                )

    bot.step(
        {"BTCUSDT": Decimal("100"), "ETHUSDT": Decimal("100")},
        source="MOCK_SYNTHETIC",
        quotes={
            "BTCUSDT": (Decimal("100"), Decimal("100")),
            "ETHUSDT": (Decimal("100"), Decimal("100")),
        },
        liquidity_metrics={
            "BTCUSDT": (Decimal("1"), Decimal("1")),
            "ETHUSDT": (Decimal("1"), Decimal("1")),
        },
        higher_timeframe_metrics={
            "BTCUSDT": ({"5m": [], "15m": []}, Decimal("0.30")),
            "ETHUSDT": ({"5m": [], "15m": []}, Decimal("0.30")),
        },
    )
    with sqlite3.connect(database) as db:
        positions = db.execute(
            "SELECT entry_gross, entry_fee, profit_funded_quote FROM bot_positions ORDER BY symbol"
        ).fetchall()
        compounding_events = db.execute(
            "SELECT allocated_profit FROM bot_compounding_events ORDER BY event_id"
        ).fetchall()

    assert len(positions) == 2
    assert len(compounding_events) == 2
    deployed = [Decimal(row[0]) + Decimal(row[1]) for row in positions]
    funded = [Decimal(row[2]) for row in positions]
    assert all(Decimal("1.01") <= amount <= Decimal("4.03") for amount in deployed)
    assert sum(funded, Decimal("0")) == Decimal("3")
    assert sorted(funded) == [Decimal("0"), Decimal("3")]
    event_allocations = [Decimal(row[0]) for row in compounding_events]
    assert sorted(event_allocations) == [Decimal("0"), Decimal("3")]
    assert all(amount <= Decimal("5") for amount in deployed)


def test_runtime_compounding_does_not_increase_seed_ticket_without_realized_profit(tmp_path):
    from types import SimpleNamespace

    database = tmp_path / "compounding-seed.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.strategy.assess = lambda *args, **kwargs: SimpleNamespace(
        signal="BUY", score=Decimal("99"), regime="BULL",
        mtf_confluence=True, atr_percent=Decimal("0.30"),
    )
    with sqlite3.connect(database) as db:
        for tick in range(-30, 0):
            db.execute(
                """INSERT INTO bot_market_ticks
                   (tick,symbol,price,bid,ask,data_source,candle_close_time_ms,received_at_ms)
                   VALUES (?, 'BTCUSDT', '100', '100', '100', 'MOCK_SYNTHETIC', NULL, ?)""",
                (tick, 1_800_000_000_000 + tick * 1000),
            )

    bot.step(
        {"BTCUSDT": Decimal("100")},
        source="MOCK_SYNTHETIC",
        quotes={"BTCUSDT": (Decimal("100"), Decimal("100"))},
    )
    with sqlite3.connect(database) as db:
        position = db.execute(
            "SELECT entry_gross, entry_fee, profit_funded_quote FROM bot_positions WHERE symbol='BTCUSDT'"
        ).fetchone()

    assert position is not None
    deployed = Decimal(position[0]) + Decimal(position[1])
    assert Decimal("1.01") <= deployed <= Decimal("1.03")
    assert Decimal(position[2]) == Decimal("0")

def test_compounding_schema_migrates_existing_paper_positions_without_reset(tmp_path):
    database = tmp_path / "legacy-paper.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE bot_positions (
                symbol TEXT PRIMARY KEY,
                quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                entry_gross TEXT NOT NULL,
                entry_fee TEXT NOT NULL,
                opened_at TEXT NOT NULL
            )"""
        )
        db.execute(
            """INSERT INTO bot_positions
               (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
               VALUES ('BTCUSDT','0.001','60000','60','0.06','2026-11-13T00:00:00+00:00')"""
        )

    ContinuousPaperBot(str(database))
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(bot_positions)")}
        position = db.execute(
            "SELECT quantity, profit_funded_quote, take_profit_percent "
            "FROM bot_positions WHERE symbol='BTCUSDT'"
        ).fetchone()
        event_table = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_compounding_events'"
        ).fetchone()

    assert "profit_funded_quote" in columns
    assert "take_profit_percent" in columns
    assert position == ("0.001", "0", "1.25")
    assert event_table == ("bot_compounding_events",)

