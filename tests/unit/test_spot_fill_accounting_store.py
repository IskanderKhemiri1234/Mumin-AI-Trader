from decimal import Decimal
import sqlite3

from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore


def test_store_persists_exchange_fill_commission_and_maker_metadata(tmp_path):
    store = SQLiteOrderStore(str(tmp_path / "fills.db"))
    fills = [
        {
            "id": 1,
            "orderId": 11,
            "price": "100",
            "qty": "1",
            "quoteQty": "100",
            "commission": "0.01",
            "commissionAsset": "BNB",
            "isBuyer": True,
            "isMaker": True,
            "time": 1000,
        },
        {
            "id": 2,
            "orderId": 12,
            "price": "110",
            "qty": "1",
            "quoteQty": "110",
            "commission": "0.11",
            "commissionAsset": "USDT",
            "isBuyer": False,
            "isMaker": False,
            "time": 2000,
        },
    ]
    store.record_trade_fills(
        "BTCUSDT",
        fills,
        commission_quote_values={1: "2.5"},
    )
    report = store.realized_pnl_report("USDT")
    assert report["complete"] is True
    assert Decimal(report["realized_pnl_quote"]) == Decimal("7.39")
    assert report["maker_fills"] == 1
    assert report["taker_fills"] == 1
    assert Decimal(report["maker_fees_quote_equivalent_mark"]) == Decimal("2.5")
    assert Decimal(report["taker_fees_quote_equivalent_mark"]) == Decimal("0.11")


def test_store_reports_unvalued_bnb_fee_as_incomplete_until_backfilled(tmp_path):
    store = SQLiteOrderStore(str(tmp_path / "unvalued.db"))
    store.record_trade_fills(
        "BTCUSDT",
        [{
            "id": 3,
            "orderId": 13,
            "price": "100",
            "qty": "1",
            "quoteQty": "100",
            "commission": "0.01",
            "commissionAsset": "BNB",
            "isBuyer": True,
            "isMaker": False,
            "time": 3000,
        }],
    )
    assert len(store.unvalued_bnb_fills()) == 1
    assert store.realized_pnl_report("USDT")["complete"] is False
    store.set_commission_quote_value("BTCUSDT", 3, "2.75")
    assert store.unvalued_bnb_fills() == []
    assert store.realized_pnl_report("USDT")["complete"] is True



def test_existing_fill_database_is_migrated_without_losing_rows(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE spot_trade_fills (
                symbol TEXT NOT NULL,
                trade_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                price TEXT NOT NULL,
                quantity TEXT NOT NULL,
                quote_quantity TEXT NOT NULL,
                commission TEXT NOT NULL,
                commission_asset TEXT NOT NULL,
                is_buyer INTEGER NOT NULL,
                trade_time_ms INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY(symbol, trade_id)
            )"""
        )
        db.execute(
            """INSERT INTO spot_trade_fills VALUES
               ('BTCUSDT',1,10,'100','1','100','0.01','USDT',1,1000,'{}')"""
        )
    SQLiteOrderStore(str(database))
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(spot_trade_fills)")}
        row = db.execute(
            "SELECT symbol,trade_id,is_maker,commission_quote_value FROM spot_trade_fills"
        ).fetchone()
    assert {"is_maker", "commission_quote_value"}.issubset(columns)
    assert row == ("BTCUSDT", 1, 0, None)



def test_order_fill_summary_nets_base_asset_commission(tmp_path):
    store = SQLiteOrderStore(str(tmp_path / "net-quantity.db"))
    store.record_trade_fills(
        "BTCUSDT",
        [
            {
                "id": 31, "orderId": 301, "price": "50000", "qty": "0.00010",
                "quoteQty": "5", "commission": "0.00000010",
                "commissionAsset": "BTC", "isBuyer": True, "isMaker": False, "time": 31000,
            },
            {
                "id": 32, "orderId": 301, "price": "50000", "qty": "0.00005",
                "quoteQty": "2.5", "commission": "0.0025",
                "commissionAsset": "USDT", "isBuyer": True, "isMaker": True, "time": 32000,
            },
        ],
    )
    summary = store.order_fill_summary("BTCUSDT", 301, base_asset="BTC")
    assert summary == {
        "fill_count": "2",
        "gross_quantity": "0.00015",
        "net_base_quantity": "0.00014990",
        "quote_quantity": "7.50",
        "base_commission": "1.0E-7",
    }
    assert len(store.trade_fills_for_order("BTCUSDT", 301)) == 2


def test_oco_child_fills_are_matched_to_fifo_ledger(tmp_path):
    store = SQLiteOrderStore(str(tmp_path / "oco-ledger.db"))
    store.record_trade_fills(
        "BTCUSDT",
        [{
            "id": 41, "orderId": 401, "price": "51000", "qty": "0.00010",
            "quoteQty": "5.1", "commission": "0.0051",
            "commissionAsset": "USDT", "isBuyer": False, "isMaker": True, "time": 41000,
        }],
    )
    with sqlite3.connect(tmp_path / "oco-ledger.db") as db:
        db.execute(
            """CREATE TABLE spot_oco_order_lists (
                list_client_order_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                above_order_id TEXT,
                below_order_id TEXT,
                response_json TEXT NOT NULL
            )"""
        )
        db.execute(
            """INSERT INTO spot_oco_order_lists
               (list_client_order_id,symbol,above_order_id,below_order_id,response_json)
               VALUES (?,?,?,?,?)""",
            (
                "mumin-oco-test", "BTCUSDT", "401", "402",
                '{"orderReports":['
                '{"orderId":401,"executedQty":"0.00010","status":"FILLED"},'
                '{"orderId":402,"executedQty":"0","status":"CANCELED"}]}',
            ),
        )
    assert store.unmatched_trade_order_ids() == []
    assert store.order_fill_quantity_discrepancies() == []
