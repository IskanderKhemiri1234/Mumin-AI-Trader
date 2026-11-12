from decimal import Decimal
import pytest

from mumin_trader.execution.execution_engine import ExecutionEngine, OrderSide


@pytest.mark.asyncio
async def test_paper_buy_applies_slippage_and_fee():
    engine = ExecutionEngine(fee_rate=Decimal("0.001"), slippage_bps=Decimal("10"))
    order = await engine.submit_paper_order(
        symbol="BTCUSDT", side=OrderSide.BUY, quantity=Decimal("0.001"),
        reference_price=Decimal("60000"), min_notional=Decimal("5"),
    )
    assert order.fill_price > order.reference_price
    assert order.fee_quote > 0 and order.status == "FILLED_PAPER"
    assert engine.get_paper_order(order.order_id) == order


@pytest.mark.asyncio
async def test_paper_order_rejects_below_minimum_notional():
    engine = ExecutionEngine()
    with pytest.raises(ValueError, match="minimum notional"):
        await engine.submit_paper_order(
            symbol="BTCUSDT", side="BUY", quantity=Decimal("0.00001"),
            reference_price=Decimal("100"), min_notional=Decimal("5"),
        )
