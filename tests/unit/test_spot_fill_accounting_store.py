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
