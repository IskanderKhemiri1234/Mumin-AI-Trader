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
    assert len(SYMBOLS) == 8
    assert len({ContinuousPaperBot.synthetic_price_for(s, 10) for s in SYMBOLS}) == 8


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


def test_public_micro_alpha_entry_persists_atr_adaptive_position_targets(tmp_path):
    database = tmp_path / "micro-alpha.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    for candle in range(14):
        bot.step(
            {"BTCUSDT": Decimal("100")},
            source="BINANCE_PUBLIC_MARKET_DATA",
            quotes={"BTCUSDT": (Decimal("100.0"), Decimal("100.1"))},
            candle_close_times={"BTCUSDT": candle + 1},
            received_at_ms=1_800_000_000_100 + candle,
            market_features={"BTCUSDT": {
                "open": Decimal("100"), "high": Decimal("100.2"),
                "low": Decimal("99.8"), "volume": Decimal("10"),
                "bid_depth_qty": Decimal("50"), "ask_depth_qty": Decimal("50"),
            }},
        )

    result = bot.step(
        {"BTCUSDT": Decimal("100.2")},
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes={"BTCUSDT": (Decimal("100.1"), Decimal("100.15"))},
        candle_close_times={"BTCUSDT": 15},
        received_at_ms=1_800_000_000_115,
        market_features={"BTCUSDT": {
            "open": Decimal("99.5"), "high": Decimal("100.3"),
            "low": Decimal("99.0"), "volume": Decimal("15"),
            "bid_depth_qty": Decimal("80"), "ask_depth_qty": Decimal("20"),
            "macro_hourly_closes": [Decimal("100")] * 21,
        }},
    )

    with sqlite3.connect(database) as db:
        position = db.execute(
            "SELECT stop_loss_pct, take_profit_pct FROM bot_positions WHERE symbol='BTCUSDT'"
        ).fetchone()
        decision = db.execute(
            """SELECT liquidity_sweep_vote, liquidity_sweep_reason,
                      depth_imbalance_vote, depth_imbalance_reason,
                      micro_alpha_filter_vote, micro_alpha_filter_reason,
                      spread_bps, atr_pct, final_signal, action
               FROM bot_agent_decisions WHERE symbol='BTCUSDT' ORDER BY tick DESC LIMIT 1"""
        ).fetchone()
    assert result.signal.startswith("BTCUSDT:BUY")
    assert position is not None
    assert decision is not None
    assert decision[0] == "BUY"
    assert "RECLAIMED" in decision[1]
    assert decision[2] == "BUY"
    assert "IMBALANCE" in decision[3]
    assert decision[4] == "BUY"
    assert "STRICT_3_OF_3_BUY_CONSENSUS_PASSED" in decision[5]
    assert Decimal(decision[6]) > 0
    assert Decimal(decision[7]) > 0
    assert decision[8] == "BUY"
    assert decision[9].startswith("BTCUSDT:OPEN")
    stop, target = map(Decimal, position)
    assert Decimal("0.20") <= stop <= Decimal("0.50")
    assert Decimal("0.40") <= target <= Decimal("1.25")
    assert stop != Decimal("0.50") or target != Decimal("1.25")



def test_sqlite_batch_audit_checks_integrity_and_checkpoints_wal(tmp_path):
    database = tmp_path / "sqlite-audit.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    for _ in range(3):
        bot.step({"BTCUSDT": Decimal("60000")})
    audit = bot.finalize_database_batch()
    assert audit["integrity"] == "ok"
    assert audit["wal_busy"] == 0
    assert audit["wal_checkpointed_frames"] >= audit["wal_log_frames"]
    with sqlite3.connect(database) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        columns = {row[1] for row in db.execute("PRAGMA table_info(bot_agent_decisions)")}
    assert integrity == "ok"
    assert {"liquidity_sweep_reason", "depth_imbalance_reason",
            "micro_alpha_filter_reason", "spread_bps", "atr_pct"} <= columns


def test_public_sma20_inputs_are_isolated_per_symbol_and_persisted(tmp_path):
    database = tmp_path / "isolated-sma20.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    bases = {"BTCUSDT": Decimal("100"), "ETHUSDT": Decimal("200")}

    for candle in range(14):
        prices = dict(bases)
        features = {}
        quotes = {}
        for symbol, base in bases.items():
            prices[symbol] = base
            features[symbol] = {
                "open": base,
                "high": base * Decimal("1.002"),
                "low": base * Decimal("0.998"),
                "volume": Decimal("10"),
                "bid_depth_qty": Decimal("50"),
                "ask_depth_qty": Decimal("50"),
                "macro_hourly_closes": [base] * 21,
            }
            quotes[symbol] = (base * Decimal("0.99975"), base * Decimal("1.00025"))
        bot.step(
            prices,
            source="BINANCE_PUBLIC_MARKET_DATA",
            quotes=quotes,
            candle_close_times={symbol: candle + 1 for symbol in bases},
            received_at_ms=1_800_000_000_100 + candle,
            market_features=features,
        )

    prices = {"BTCUSDT": Decimal("100.2"), "ETHUSDT": Decimal("200.4")}
    features = {}
    quotes = {}
    for symbol, base in bases.items():
        features[symbol] = {
            "open": base * Decimal("0.995"),
            "high": base * Decimal("1.003"),
            "low": base * Decimal("0.990"),
            "volume": Decimal("15"),
            "bid_depth_qty": Decimal("80"),
            "ask_depth_qty": Decimal("20"),
            "macro_hourly_closes": [base] * 21,
        }
        price = prices[symbol]
        quotes[symbol] = (price * Decimal("0.99975"), price * Decimal("1.00025"))

    bot.step(
        prices,
        source="BINANCE_PUBLIC_MARKET_DATA",
        quotes=quotes,
        candle_close_times={symbol: 15 for symbol in bases},
        received_at_ms=1_800_000_000_115,
        market_features=features,
    )

    with sqlite3.connect(database) as db:
        rows = db.execute(
            """SELECT symbol, macro_sma20, macro_sma20_slope, macro_trend_reason,
                      spread_pass, agent_consensus, agent_consensus_pass
               FROM bot_agent_decisions WHERE tick=15 ORDER BY symbol"""
        ).fetchall()

    decisions = {row[0]: row[1:] for row in rows}
    assert set(decisions) == set(bases)
    assert Decimal(decisions["BTCUSDT"][0]) == Decimal("100")
    assert Decimal(decisions["ETHUSDT"][0]) == Decimal("200")
    assert decisions["BTCUSDT"][2].startswith("MACRO_TREND_PASS")
    assert decisions["ETHUSDT"][2].startswith("MACRO_TREND_PASS")
    assert decisions["BTCUSDT"][3] == decisions["ETHUSDT"][3] == 1
    assert decisions["BTCUSDT"][4] == decisions["ETHUSDT"][4] == "BUY_3_OF_3"
    assert decisions["BTCUSDT"][5] == decisions["ETHUSDT"][5] == 1


def test_export_contract_exposes_all_assets_and_actual_compounder_allocations(tmp_path):
    from scripts.export_paper_state import build_ui_contract
    from scripts.export_paper_report import load_snapshot

    database = tmp_path / "ui-contract.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    bot.step()
    snapshot = load_snapshot(str(database))
    contract = build_ui_contract(str(database), snapshot)

    assert len(contract["assets"]) == 8
    assert {asset["symbol"] for asset in contract["assets"]} == set(SYMBOLS)
    assert all("compounder_actual_allocation_pct" in asset for asset in contract["assets"])
    assert "next_entry_budget_usdt" in contract["compounder"]
    assert contract["compounder"]["allocation_semantics"] == (
        "actual_position_market_value_divided_by_current_equity"
    )
