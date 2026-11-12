from decimal import Decimal

from mumin_trader.execution.execution_engine import ExecutionEngine, OrderSide
from mumin_trader.execution.paper_ledger import PaperLedger


async def test_offline_mock_round_trip_is_persisted(tmp_path):
    database = tmp_path / "paper.sqlite3"
    ledger = PaperLedger(database, initial_cash=Decimal("50.00"))
    engine = ExecutionEngine(slippage_bps=Decimal("2"), require_verified_commission=True)

    buy = await engine.submit_paper_order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.000083"),
        reference_price=Decimal("60006"),
        min_notional=Decimal("4.95"),
        fee_rate_override=Decimal("0.001"),
    )
    ledger.record_fill(buy)
    sell = await engine.submit_paper_order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        quantity=buy.quantity,
        reference_price=Decimal("60720"),
        min_notional=Decimal("5"),
        fee_rate_override=Decimal("0.001"),
    )
    ledger.record_fill(sell)
    net = ledger.record_closed_trade(
        symbol="BTCUSDT",
        quantity=buy.quantity,
        entry_price=buy.fill_price,
        exit_price=sell.fill_price,
        entry_fee=buy.fee_quote,
        exit_fee=sell.fee_quote,
    )

    reopened = PaperLedger(database, initial_cash=Decimal("50.00"))
    portfolio = reopened.portfolio()
    assert net > 0
    assert portfolio.realized_pnl == net
    assert portfolio.cash > Decimal("50")
    assert reopened.counts() == {"orders": 2, "trades": 1}
    assert reopened.latest_trade()["data_source"] == "MOCK_SYNTHETIC"


async def test_paper_engine_never_submits_exchange_orders():
    import pytest
    from mumin_trader.execution.execution_engine import ExecutionDisabledError

    engine = ExecutionEngine()
    with pytest.raises(ExecutionDisabledError):
        await engine.submit_order(symbol="BTCUSDT", side="BUY", quantity="1")
