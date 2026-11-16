from decimal import Decimal

from mumin_trader.exchanges.binance.realized_pnl import build_realized_pnl_report


def fill(trade_id, side, price, qty, quote_qty, fee="0", fee_asset="USDT", **extra):
    return {
        "symbol": "BTCUSDT",
        "trade_id": trade_id,
        "trade_time_ms": trade_id,
        "price": str(price),
        "quantity": str(qty),
        "quote_quantity": str(quote_qty),
        "commission": str(fee),
        "commission_asset": fee_asset,
        "is_buyer": side == "BUY",
        "is_maker": extra.get("is_maker", False),
        "commission_quote_value": extra.get("commission_quote_value"),
    }


def test_fifo_realized_pnl_includes_quote_and_base_fees_and_maker_taker():
    report = build_realized_pnl_report([
        fill(1, "BUY", "100", "1", "100", "0.1", "USDT", is_maker=True),
        fill(2, "SELL", "120", "0.5", "60", "0.001", "BTC", is_maker=False),
    ])
    # Buy cost 100.1 for 1 BTC; sell disposes 0.501 BTC due to base fee.
    expected_cost = Decimal("0.501") * Decimal("100.1")
    expected = Decimal("60") - expected_cost
    assert Decimal(report["realized_pnl_quote"]) == expected
    assert report["maker_fills"] == 1
    assert report["taker_fills"] == 1
    assert Decimal(report["maker_effective_fee_rate_mark"]) == Decimal("0.001")
    assert Decimal(report["taker_effective_fee_rate_mark"]) == Decimal("0.00001666666666666666666666666667")
    assert report["complete"] is True


def test_bnb_fee_is_only_counted_with_historical_quote_valuation():
    report = build_realized_pnl_report([
        fill(1, "BUY", "100", "1", "100", "0.01", "BNB", commission_quote_value="3"),
        fill(2, "SELL", "110", "1", "110", "0.01", "BNB", commission_quote_value="3.2"),
    ])
    assert Decimal(report["realized_pnl_quote"]) == Decimal("3.8")
    assert report["complete"] is True


def test_missing_bnb_valuation_marks_report_incomplete_instead_of_guessing():
    report = build_realized_pnl_report([
        fill(1, "BUY", "100", "1", "100", "0.01", "BNB"),
        fill(2, "SELL", "110", "1", "110"),
    ])
    assert report["complete"] is False
    assert "missing_historical_BNB_fee_valuation" in report["unresolved"][0]


def test_fifo_multiple_entries_and_partial_exit():
    report = build_realized_pnl_report([
        fill(1, "BUY", "100", "1", "100"),
        fill(2, "BUY", "120", "1", "120"),
        fill(3, "SELL", "130", "1.5", "195"),
    ])
    assert Decimal(report["realized_pnl_quote"]) == Decimal("35")
    assert Decimal(report["symbols"]["BTCUSDT"]["open_quantity"]) == Decimal("0.5")
    assert Decimal(report["symbols"]["BTCUSDT"]["open_cost_basis_quote"]) == Decimal("60")


def test_sell_without_fifo_inventory_is_unresolved():
    report = build_realized_pnl_report([fill(1, "SELL", "110", "1", "110")])
    assert report["complete"] is False
    assert "sell_exceeds_FIFO_inventory" in report["unresolved"][0]



def test_zero_bnb_commission_does_not_require_a_conversion_mark():
    report = build_realized_pnl_report([
        fill(1, "BUY", "100", "1", "100", "0", "BNB"),
        fill(2, "SELL", "110", "1", "110", "0", "BNB"),
    ])
    assert report["complete"] is True
    assert Decimal(report["realized_pnl_quote"]) == Decimal("10")
