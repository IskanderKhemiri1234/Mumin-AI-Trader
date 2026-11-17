from __future__ import annotations

import sqlite3

from scripts.audit_paper_ledger import build_audit


def _make_ledger(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE bot_account (
            id INTEGER PRIMARY KEY, initial_cash TEXT, cash TEXT,
            realized_pnl TEXT, fees_paid TEXT, tick_count INTEGER
        );
        CREATE TABLE bot_positions (
            symbol TEXT PRIMARY KEY, quantity TEXT, entry_gross TEXT, entry_fee TEXT
        );
        CREATE TABLE bot_fills (
            fill_id INTEGER PRIMARY KEY, tick INTEGER, symbol TEXT, side TEXT,
            quantity TEXT, reference_price TEXT, fill_price TEXT, gross_quote TEXT,
            fee_quote TEXT, net_pnl TEXT, data_source TEXT, exit_reason TEXT, created_at TEXT
        );
        CREATE TABLE bot_market_ticks (
            id INTEGER PRIMARY KEY, symbol TEXT, price TEXT,
            data_source TEXT, received_at_ms INTEGER
        );
        CREATE TABLE bot_agent_decisions (
            tick INTEGER, symbol TEXT, liquidity_sweep_vote TEXT,
            liquidity_sweep_reason TEXT, depth_imbalance_vote TEXT,
            depth_imbalance_reason TEXT, micro_alpha_filter_vote TEXT,
            micro_alpha_filter_reason TEXT, final_signal TEXT, action TEXT,
            macro_trend_reason TEXT, spread_bps TEXT, atr_pct TEXT
        );
    """)
    db.execute(
        "INSERT INTO bot_account VALUES (1,'70','70.979','0.979','0.021',2)"
    )
    db.execute(
        """INSERT INTO bot_fills VALUES
        (1,1,'BTCUSDT','BUY','1','10','10','10','0.01','0','BINANCE_PUBLIC_MARKET_DATA',NULL,'t1'),
        (2,2,'BTCUSDT','SELL','1','11','11','11','0.011','0.979','BINANCE_PUBLIC_MARKET_DATA','TAKE_PROFIT','t2')"""
    )
    db.execute(
        """INSERT INTO bot_agent_decisions VALUES
        (1,'BTCUSDT','BUY','sweep reclaim','BUY','bid imbalance','BUY','strict consensus',
         'BUY','BTCUSDT:OPEN','macro pass','1','0.2'),
        (2,'BTCUSDT','HOLD','no sweep','HOLD','neutral depth','HOLD','no consensus',
         'HOLD','BTCUSDT:CLOSE','macro context','1','0.2')"""
    )
    db.execute(
        "INSERT INTO bot_market_ticks VALUES (1,'BTCUSDT','11','BINANCE_PUBLIC_MARKET_DATA',1)"
    )
    db.commit()
    db.close()


def test_audit_reconstructs_fifo_pnl_fees_and_entry_exit_context(tmp_path):
    database = tmp_path / "paper.db"
    _make_ledger(database)

    report = build_audit(database)

    assert report["accounting_status"] == "PASS"
    assert report["account"]["closed_round_trips"] == 1
    btc = report["per_symbol"]["BTCUSDT"]
    assert btc["gross_pnl_usdt"] == "1"
    assert btc["closed_round_trip_entry_fees_usdt"] == "0.01"
    assert btc["closed_round_trip_exit_fees_usdt"] == "0.011"
    assert btc["net_pnl_usdt"] == "0.979"
    assert btc["fill_total_fees_usdt"] == "0.021"
    assert btc["entry_reason_counts"] == {"strict consensus": 1}
    assert btc["exit_signal_context_counts"] == {"HOLD": 1}
    trip = report["round_trips"][0]
    assert trip["exit_trigger_reason"] == "TAKE_PROFIT"


def test_audit_is_read_only(tmp_path):
    database = tmp_path / "paper.db"
    _make_ledger(database)
    before = database.read_bytes()

    build_audit(database)

    assert database.read_bytes() == before
