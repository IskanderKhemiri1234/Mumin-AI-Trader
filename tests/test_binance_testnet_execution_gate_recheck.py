from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_testnet_execution import (
    BinanceSpotTestnetExecution,
    SpotExecutionError,
)


@pytest.mark.asyncio
async def test_submit_cancel_and_emergency_stop_recheck_both_gates(tmp_path):
    settings = Settings(
        _env_file=None,
        TRADING_ENV=TradingEnvironment.TESTNET,
        TRADING_MODE=TradingMode.TESTNET,
        ALLOW_MAINNET=False,
        LIVE_TRADING_ENABLED=False,
        TESTNET_ORDER_PREFLIGHT_ENABLED=True,
        TESTNET_SPOT_EXECUTION_ENABLED=True,
        BINANCE_API_KEY="test-key",
        BINANCE_API_SECRET="test-secret",
        WATCHLIST="BTCUSDT",
        MAX_TRADE_ALLOCATION_USD=Decimal("5"),
        MIN_TRADE_ALLOCATION_USD=Decimal("5"),
        MAX_CAPITAL_ALLOCATION_USD=Decimal("70"),
    )
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(
        settings, store, transport=httpx.MockTransport(handler),
    )
    try:
        for gate_name in ("TESTNET_ORDER_PREFLIGHT_ENABLED", "TESTNET_SPOT_EXECUTION_ENABLED"):
            setattr(settings, gate_name, False)
            with pytest.raises(SpotExecutionError, match="gate"):
                await client.submit_limit_order(
                    symbol="BTCUSDT", side="BUY", quantity="0.00010",
                    price="50000.00", client_order_id=f"mumin-gate-{gate_name.lower()}",
                )
            with pytest.raises(SpotExecutionError, match="gate"):
                await client.cancel_order(
                    symbol="BTCUSDT", client_order_id="mumin-no-local-order",
                )
            with pytest.raises(SpotExecutionError, match="gate"):
                await client.emergency_stop("TEST_GATE_RECHECK")
            setattr(settings, gate_name, True)

        assert seen == []
        assert store.is_halted() == (False, "")
    finally:
        await client.close()
