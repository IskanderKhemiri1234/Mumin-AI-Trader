import pytest

from mumin_trader.execution.execution_engine import ExecutionDisabledError, ExecutionEngine


@pytest.mark.asyncio
async def test_order_submission_is_disabled():
    with pytest.raises(ExecutionDisabledError):
        await ExecutionEngine().submit_order(symbol="BTCUSDT", side="BUY")
