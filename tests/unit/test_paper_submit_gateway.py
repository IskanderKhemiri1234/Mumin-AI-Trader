from decimal import Decimal
from types import SimpleNamespace

import pytest

from mumin_trader.execution.execution_engine import (
    ExecutionDisabledError,
    ExecutionEngine,
    OrderSide,
)


@pytest.mark.asyncio
async def test_unified_submit_order_routes_only_to_paper():
    settings = SimpleNamespace(
        TRADING_ENV="TESTNET",
        TRADING_MODE="PAPER",
        ALLOW_MAINNET=False,
        LIVE_TRADING_ENABLED=False,
    )
    engine = ExecutionEngine(fee_rate=Decimal("0.001"), slippage_bps=Decimal("0"))
    order = await engine.submit_order(
        settings=settings,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=Decimal("0.001"),
        reference_price=Decimal("60000"),
    )
    assert order.status == "FILLED_PAPER"
    assert engine.get_paper_order(order.order_id) == order


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        SimpleNamespace(TRADING_ENV="MAINNET", TRADING_MODE="LIVE"),
        SimpleNamespace(TRADING_ENV="TESTNET", TRADING_MODE="LIVE"),
        SimpleNamespace(
            TRADING_ENV="TESTNET",
            TRADING_MODE="PAPER",
            ALLOW_MAINNET=True,
            LIVE_TRADING_ENABLED=False,
        ),
        SimpleNamespace(
            TRADING_ENV="TESTNET",
            TRADING_MODE="PAPER",
            ALLOW_MAINNET=False,
            LIVE_TRADING_ENABLED=True,
        ),
    ],
)
async def test_unified_submit_order_fails_closed_outside_paper(settings):
    engine = ExecutionEngine()
    with pytest.raises(ExecutionDisabledError, match="PAPER-only"):
        await engine.submit_order(
            settings=settings,
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("0.001"),
            reference_price=Decimal("60000"),
        )
