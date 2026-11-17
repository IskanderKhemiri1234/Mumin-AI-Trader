import sqlite3
from decimal import Decimal

from fastapi.testclient import TestClient

from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger
from mumin_trader.main import app
from scripts.run_continuous_paper import ContinuousPaperBot


def _seed_partial_fifo_ledger(database):
    ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    with sqlite3.connect(database) as db:
        db.execute(
            """UPDATE bot_account
               SET cash='67.3926', realized_pnl='0.3956', fees_paid='0.0074'
               WHERE id=1"""
        )
        db.execute(
            """INSERT INTO bot_positions
               (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
               VALUES ('BTCUSDT','0.06','50','3','0.003','2026-11-17T10:00:00+00:00')"""
        )
        db.executemany(
            """INSERT INTO bot_fills
               (tick,symbol,side,quantity,reference_price,fill_price,gross_quote,
                fee_quote,net_pnl,data_source,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1, "BTCUSDT", "BUY", "0.1", "50", "50", "5", "0.005", "0",
                 "MOCK_SYNTHETIC", "2026-11-17T10:00:00+00:00"),
                (2, "BTCUSDT", "SELL", "0.04", "60", "60", "2.4", "0.0024", "0.3956",
                 "MOCK_SYNTHETIC", "2026-11-17T10:01:00+00:00"),
            ],
        )
        db.execute(
            """INSERT INTO bot_market_ticks
               (tick,symbol,price,bid,ask,data_source,received_at_ms)
               VALUES (2,'BTCUSDT','55','54.99','55.01','MOCK_SYNTHETIC',1794910000000)"""
        )


def test_fifo_reconciliation_matches_partial_sell_fees_cash_and_equity(tmp_path):
    database = tmp_path / "fifo.sqlite3"
    _seed_partial_fifo_ledger(database)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        result = reconcile_paper_ledger(connection)
    finally:
        connection.close()

    assert result["status"] == "PASS"
    assert all(result["checks"].values())
    assert Decimal(result["fifo_realized_pnl"]) == Decimal("0.3956")
    assert Decimal(result["recorded_fees_paid"]) == Decimal("0.0074")
    assert Decimal(result["expected_cash_from_fills"]) == Decimal("67.3926")
    assert Decimal(result["open_market_value"]) == Decimal("3.30")
    assert Decimal(result["unrealized_pnl_including_entry_fees"]) == Decimal("0.297")
    assert Decimal(result["reconciled_equity"]) == Decimal("70.6926")
    assert result["sell_fifo_matches"][0]["matched_quantity"] == "0.04"


def test_fifo_reconciliation_detects_fee_double_count_or_missing_fee(tmp_path):
    database = tmp_path / "fee-mismatch.sqlite3"
    _seed_partial_fifo_ledger(database)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE bot_account SET fees_paid='0.0148' WHERE id=1")

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        result = reconcile_paper_ledger(connection)
    finally:
        connection.close()

    assert result["status"] == "FAIL"
    assert result["checks"]["fees_match_fills"] is False
    assert any("Fee mismatch" in issue for issue in result["issues"])


def test_dashboard_reports_fifo_audit_entry_fee_and_real_worker_state(monkeypatch, tmp_path):
    database = tmp_path / "dashboard-fifo.sqlite3"
    _seed_partial_fifo_ledger(database)
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "test-dashboard-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard",
        headers={"X-Dashboard-Token": "test-dashboard-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accounting"]["status"] == "PASS"
    assert payload["account"]["unrealized_pnl"] == 0.297
    assert payload["positions"][0]["unrealized_pnl"] == 0.297
    assert payload["engine_status"] == "OFFLINE"
    assert payload["paper_worker"]["worker_status"] == "offline"
    assert payload["control_plane"]["mainnet_order_submission"] == "BLOCKED"



def test_real_continuous_synthetic_run_reconciles_all_ledger_totals(tmp_path):
    database = tmp_path / "forty-cycle-ledger.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    for _ in range(40):
        bot.step()

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        result = reconcile_paper_ledger(connection)
    finally:
        connection.close()

    assert result["status"] == "PASS"
    assert all(value is True for value in result["checks"].values())
    assert result["checks"]["fills_match_fifo"] is True
    assert result["checks"]["cash_matches_fills"] is True
    assert result["checks"]["fees_match_fills"] is True
    assert result["checks"]["realized_pnl_matches_fifo"] is True
    assert result["checks"]["open_positions_match_fifo"] is True
    assert result["checks"]["equity_identity_matches"] is True
    assert result["market_data"]
