from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import OrderStoreError, SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_testnet_execution import (
    BinanceSpotTestnetExecution,
    SpotExecutionError,
    SpotOrderOutcomeUnknown,
)
from mumin_trader.exchanges.binance.testnet_order_preflight import TESTNET_REST_BASE_URL


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TRADING_ENV": TradingEnvironment.TESTNET,
        "TRADING_MODE": TradingMode.TESTNET,
        "ALLOW_MAINNET": False,
        "LIVE_TRADING_ENABLED": False,
        "TESTNET_ORDER_PREFLIGHT_ENABLED": True,
        "TESTNET_SPOT_EXECUTION_ENABLED": True,
        "BINANCE_API_KEY": "test-key",
        "BINANCE_API_SECRET": "test-secret",
        "WATCHLIST": "BTCUSDT",
        "MAX_TRADE_ALLOCATION_USD": Decimal("5"),
        "MIN_TRADE_ALLOCATION_USD": Decimal("5"),
        "MAX_CAPITAL_ALLOCATION_USD": Decimal("70"),
        "CAPITAL_RESERVE_PERCENT": Decimal("20"),
        "MAX_OPEN_POSITIONS": 2,
    }
    values.update(overrides)
    return Settings(**values)


def symbol_info() -> dict[str, object]:
    return {
        "symbols": [{
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "isSpotTradingAllowed": True,
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "100", "stepSize": "0.00001"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "5.00"},
            ],
        }]
    }


def account_info() -> dict[str, object]:
    return {
        "canTrade": True,
        "canWithdraw": False,
        "balances": [
            {"asset": "USDT", "free": "100", "locked": "0"},
            {"asset": "BTC", "free": "0", "locked": "0"},
        ],
    }


def order_response(status: str = "NEW") -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "orderId": 12345,
        "clientOrderId": "mumin-test-1001",
        "price": "50000.00",
        "origQty": "0.00010",
        "executedQty": "0",
        "cummulativeQuoteQty": "0",
        "status": status,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "GTC",
    }


def handler_for(seen: list[httpx.Request], *, timeout_submit: bool = False, missing_query: bool = False):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url).startswith(TESTNET_REST_BASE_URL)
        path = request.url.path
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if path == "/api/v3/account":
            return httpx.Response(200, json=account_info())
        if path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if path == "/api/v3/order" and request.method == "POST":
            if timeout_submit:
                raise httpx.ReadTimeout("simulated timeout", request=request)
            return httpx.Response(200, json=order_response())
        if path == "/api/v3/order" and request.method == "GET":
            if missing_query:
                return httpx.Response(400, json={"code": -2013, "msg": "Order does not exist."})
            return httpx.Response(200, json=order_response())
        if path == "/api/v3/order" and request.method == "DELETE":
            return httpx.Response(200, json=order_response("CANCELED"))
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})
    return handler


@pytest.mark.asyncio
async def test_execution_gate_is_disabled_by_default(tmp_path) -> None:
    cfg = settings(TESTNET_SPOT_EXECUTION_ENABLED=False)
    with pytest.raises(SpotExecutionError, match="disabled by default"):
        BinanceSpotTestnetExecution(cfg, SQLiteOrderStore(str(tmp_path / "orders.db")))


@pytest.mark.asyncio
async def test_execution_refuses_mainnet_or_live_configuration(tmp_path) -> None:
    with pytest.raises(ValueError, match="(?i)mainnet|live"):
        settings(ALLOW_MAINNET=True)


@pytest.mark.asyncio
async def test_submit_is_testnet_only_and_persists_idempotent_order(tmp_path) -> None:
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(handler_for(seen))
    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(settings(), store, transport=transport)
    try:
        response = await client.submit_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.00", client_order_id="mumin-test-1001",
        )
        assert response["status"] == "NEW"
        duplicate = await client.submit_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.00", client_order_id="mumin-test-1001",
        )
        assert duplicate["state"] == "NEW"
        assert sum(req.method == "POST" and req.url.path == "/api/v3/order" for req in seen) == 1
        assert store.get_order("mumin-test-1001")["state"] == "NEW"
        assert all(str(req.url).startswith(TESTNET_REST_BASE_URL) for req in seen)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancel_order_persists_exchange_cancel_status(tmp_path) -> None:
    seen: list[httpx.Request] = []
    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler_for(seen)),
    )
    try:
        await client.submit_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.00", client_order_id="mumin-test-1001",
        )
        result = await client.cancel_order(symbol="BTCUSDT", client_order_id="mumin-test-1001")
        assert result["status"] == "CANCELED"
        assert client.store.get_order("mumin-test-1001")["state"] == "CANCELED"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_marks_unknown_and_never_resubmits(tmp_path) -> None:
    seen: list[httpx.Request] = []
    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler_for(seen, timeout_submit=True, missing_query=True)),
    )
    try:
        with pytest.raises(SpotOrderOutcomeUnknown):
            await client.submit_limit_order(
                symbol="BTCUSDT", side="BUY", quantity="0.00010",
                price="50000.00", client_order_id="mumin-test-1001",
            )
        assert client.store.get_order("mumin-test-1001")["state"] == "UNKNOWN"
        with pytest.raises(SpotOrderOutcomeUnknown):
            await client.submit_limit_order(
                symbol="BTCUSDT", side="BUY", quantity="0.00010",
                price="50000.00", client_order_id="mumin-test-1001",
            )
        assert sum(req.method == "POST" and req.url.path == "/api/v3/order" for req in seen) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_emergency_stop_blocks_new_orders_persistently(tmp_path) -> None:
    seen: list[httpx.Request] = []
    store_path = str(tmp_path / "orders.db")
    store = SQLiteOrderStore(store_path)
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler_for(seen)),
    )
    try:
        result = await client.emergency_stop("TEST_KILL_SWITCH")
        assert result["halted"] is True
        with pytest.raises(SpotExecutionError, match="Emergency stop"):
            await client.submit_limit_order(
                symbol="BTCUSDT", side="BUY", quantity="0.00010",
                price="50000.00", client_order_id="mumin-test-1002",
            )
        assert SQLiteOrderStore(store_path).is_halted() == (True, "TEST_KILL_SWITCH")
        assert not any(req.method == "POST" and req.url.path == "/api/v3/order" for req in seen)
    finally:
        await client.close()


def test_client_order_id_cannot_be_reused_for_different_parameters(tmp_path) -> None:
    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    request = {
        "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": "0.00010", "price": "50000.00", "newClientOrderId": "mumin-test-1001",
    }
    store.register_intent(request)
    changed = dict(request, price="50001.00")
    with pytest.raises(OrderStoreError, match="collision"):
        store.register_intent(changed)



@pytest.mark.asyncio
async def test_reconciliation_mismatch_persistently_halts_execution(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/account":
            return httpx.Response(200, json=account_info())
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(200, json=[order_response()])
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        report = await client.reconcile()
        assert report["discrepancies"] == ["EXCHANGE_ORDER_WITHOUT_LOCAL_INTENT:mumin-test-1001"]
        assert report["halted"] is True
        assert store.is_halted() == (True, "RECONCILIATION_MISMATCH")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_reconciliation_transport_failure_activates_kill_switch(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        raise httpx.ConnectError("simulated exchange outage", request=request)

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(httpx.ConnectError):
            await client.reconcile()
        assert store.is_halted() == (True, "RECONCILIATION_UNAVAILABLE")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_account_without_spot_trade_permission_blocks_before_order(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if request.url.path == "/api/v3/account":
            return httpx.Response(200, json={**account_info(), "canTrade": False})
        return httpx.Response(200, json=[])

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(SpotExecutionError, match="Spot trading permission"):
            await client.submit_limit_order(
                symbol="BTCUSDT", side="BUY", quantity="0.00010",
                price="50000.00", client_order_id="mumin-test-1003",
            )
        assert not any(req.method == "POST" and req.url.path == "/api/v3/order" for req in seen)
    finally:
        await client.close()



def test_daily_loss_and_drawdown_limits_persist_a_halt(tmp_path) -> None:
    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    assert store.update_risk_state(
        equity="100", trading_day="2026-11-15",
        max_daily_loss_percent="2", max_drawdown_percent="5",
    ) is None
    assert store.update_risk_state(
        equity="97.99", trading_day="2026-11-15",
        max_daily_loss_percent="2", max_drawdown_percent="5",
    ) == "MAX_DAILY_LOSS"
    assert store.is_halted() == (True, "MAX_DAILY_LOSS")


def test_drawdown_limit_takes_priority_and_halts(tmp_path) -> None:
    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.update_risk_state(
        equity="100", trading_day="2026-11-15",
        max_daily_loss_percent="2", max_drawdown_percent="5",
    )
    reason = store.update_risk_state(
        equity="94", trading_day="2026-11-15",
        max_daily_loss_percent="2", max_drawdown_percent="5",
    )
    assert reason == "MAX_DRAWDOWN"
    assert store.is_halted() == (True, "MAX_DRAWDOWN")


def test_risk_halt_allows_only_sell_intents_while_manual_halt_blocks_all(tmp_path) -> None:
    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.halt("MAX_DAILY_LOSS")
    sell = {
        "symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": "0.00010", "price": "50000.00", "newClientOrderId": "risk-exit-1",
    }
    _, created = store.register_intent(sell)
    assert created is True
    assert store.claim_submission("risk-exit-1") is True

    manual_store = SQLiteOrderStore(str(tmp_path / "manual-orders.db"))
    manual_store.halt("MANUAL_EMERGENCY_STOP")
    with pytest.raises(OrderStoreError, match="Execution halt"):
        manual_store.register_intent(sell)
