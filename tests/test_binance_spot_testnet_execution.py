from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import OrderStoreError, SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_testnet_oco import BinanceSpotTestnetOCO
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
        "accountType": "SPOT",
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
        if path == "/api/v3/myTrades":
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
        await client.reconcile()
        response = await client.submit_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.00", client_order_id="mumin-test-1001",
        )
        assert response["status"] == "NEW"
        duplicate = await client.submit_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.00", client_order_id="mumin-test-1001",
        )
        assert duplicate["status"] == "NEW"
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
        await client.reconcile()
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
        await client.reconcile()
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
        with pytest.raises(SpotExecutionError, match="Execution halt"):
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
        if request.url.path == "/api/v3/myTrades":
            return httpx.Response(200, json=[])
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

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.update_risk_state(
        equity="100", trading_day="2026-11-15",
        max_daily_loss_percent="2", max_drawdown_percent="5",
    )
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
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



@pytest.mark.asyncio
async def test_partial_fill_status_and_quantities_are_persisted(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/order":
            partial = {
                **order_response("PARTIALLY_FILLED"),
                "executedQty": "0.00004",
                "cummulativeQuoteQty": "2.00",
            }
            return httpx.Response(200, json=partial)
        if request.url.path == "/api/v3/myTrades":
            trade = {
                "id": 1, "orderId": 12345, "price": "50000.00",
                "qty": "0.00004", "quoteQty": "2.00",
                "commission": "0.002", "commissionAsset": "USDT",
                "isBuyer": True, "time": 1_700_000_000_000,
            }
            return httpx.Response(200, json=[trade] if request.url.params.get("fromId") == "0" else [])
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.register_intent({
        "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": "0.00010", "price": "50000.00", "newClientOrderId": "mumin-test-1004",
    })
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.get_order(symbol="BTCUSDT", client_order_id="mumin-test-1004")
        assert result is not None
        assert result["status"] == "PARTIALLY_FILLED"
        saved = store.get_order("mumin-test-1004")
        assert saved["state"] == "PARTIALLY_FILLED"
        assert saved["executed_quantity"] == "0.00004"
        assert saved["cumulative_quote_quantity"] == "2.00"
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_reconciliation_persists_trade_fills_and_matches_account_balances(tmp_path) -> None:
    trade = {
        "id": 1,
        "orderId": 12345,
        "price": "50000.00",
        "qty": "0.00010",
        "quoteQty": "5.00",
        "commission": "0.005",
        "commissionAsset": "USDT",
        "isBuyer": True,
        "time": 1_700_000_000_000,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/account":
            account = account_info()
            account["balances"] = [
                {"asset": "USDT", "free": "94.995", "locked": "0"},
                {"asset": "BTC", "free": "0.00010", "locked": "0"},
            ]
            return httpx.Response(200, json=account)
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "50000.00"})
        if request.url.path == "/api/v3/myTrades":
            return httpx.Response(200, json=[trade] if request.url.params.get("fromId") == "0" else [])
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.register_intent({
        "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": "0.00010", "price": "50000.00", "newClientOrderId": "mumin-test-filled-1",
    })
    store.update_order(
        "mumin-test-filled-1", state="FILLED",
        response={"orderId": 12345, "executedQty": "0.00010", "cummulativeQuoteQty": "5.00"},
    )
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        report = await client.reconcile()
        assert report["discrepancies"] == []
        assert report["trade_positions"] == {"BTC": "0.00010"}
        assert store.trade_position_quantities("USDT") == {"BTC": Decimal("0.00010")}
        assert store.last_trade_id("BTCUSDT") == 1
        assert store.is_halted() == (False, "")
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_sell_limit_order_requires_and_uses_free_base_balance(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if request.url.path == "/api/v3/account":
            account = account_info()
            account["balances"] = [
                {"asset": "USDT", "free": "100", "locked": "0"},
                {"asset": "BTC", "free": "0.00010", "locked": "0"},
            ]
            return httpx.Response(200, json=account)
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v3/myTrades":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "50000.00"})
        if request.url.path == "/api/v3/order" and request.method == "POST":
            return httpx.Response(200, json={**order_response(), "side": "SELL"})
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        await client.reconcile()
        response = await client.submit_limit_order(
            symbol="BTCUSDT", side="SELL", quantity="0.00010",
            price="50000.00", client_order_id="mumin-test-sell-1",
        )
        assert response["side"] == "SELL"
        assert store.get_order("mumin-test-sell-1")["side"] == "SELL"
        assert any(
            request.method == "POST" and request.url.params.get("side") == "SELL"
            for request in seen
        )
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_emergency_stop_cancels_exchange_open_orders_and_keeps_halt(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(200, json=[order_response()])
        if request.url.path == "/api/v3/order" and request.method == "DELETE":
            return httpx.Response(200, json=order_response("CANCELED"))
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.register_intent({
        "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
        "quantity": "0.00010", "price": "50000.00", "newClientOrderId": "mumin-test-1001",
    })
    store.update_order("mumin-test-1001", state="NEW", response=order_response())
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.emergency_stop("MANUAL_EMERGENCY_STOP")
        assert result["halted"] is True
        assert len(result["cancelled"]) == 1
        assert store.get_order("mumin-test-1001")["state"] == "CANCELED"
        assert store.is_halted() == (True, "MANUAL_EMERGENCY_STOP")
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_account_with_can_withdraw_true_is_not_misclassified_as_key_scope(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/account":
            return httpx.Response(200, json={**account_info(), "canWithdraw": True})
        return httpx.Response(404, json={"msg": "unexpected endpoint"})

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        account = await client._account()
        assert account["accountType"] == "SPOT"
        assert account["canTrade"] is True
        assert account["canWithdraw"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancel_api_error_marks_order_unknown_without_retry(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if request.url.path == "/api/v3/account":
            return httpx.Response(200, json=account_info())
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v3/myTrades":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v3/order" and request.method == "POST":
            return httpx.Response(200, json=order_response())
        if request.url.path == "/api/v3/order" and request.method == "DELETE":
            return httpx.Response(500, json={"code": -1000, "msg": "unknown cancel outcome"})
        if request.url.path == "/api/v3/order" and request.method == "GET":
            return httpx.Response(200, json=order_response())
        return httpx.Response(404, json={"msg": "unexpected endpoint"})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler),
    )
    try:
        await client.reconcile()
        await client.submit_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.00", client_order_id="mumin-cancel-unknown",
        )
        with pytest.raises(SpotOrderOutcomeUnknown, match="Cancellation outcome is unknown"):
            await client.cancel_order(symbol="BTCUSDT", client_order_id="mumin-cancel-unknown")
        assert store.get_order("mumin-cancel-unknown")["state"] == "UNKNOWN"
        assert sum(req.method == "DELETE" for req in seen) == 1
    finally:
        await client.close()



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"accountType": None}, "account type"),
        ({"canTrade": None}, "Spot trading permission"),
    ],
)
async def test_missing_account_permission_fields_fail_closed(tmp_path, overrides, message) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/account":
            return httpx.Response(200, json={**account_info(), **overrides})
        return httpx.Response(404, json={"msg": "unexpected endpoint"})

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(SpotExecutionError, match=message):
            await client._account()
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_incomplete_fee_and_fifo_ledger_blocks_order_before_network(tmp_path) -> None:
    seen: list[httpx.Request] = []
    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    store.update_risk_state(
        equity="100",
        trading_day="2026-11-16",
        max_daily_loss_percent="2",
        max_drawdown_percent="5",
    )
    store.record_trade_fills(
        "BTCUSDT",
        [{
            "id": 1,
            "orderId": 1,
            "price": "100",
            "qty": "1",
            "quoteQty": "100",
            "commission": "0.01",
            "commissionAsset": "BNB",
            "isBuyer": True,
            "isMaker": False,
            "time": 1_700_000_000_000,
        }],
    )
    client = BinanceSpotTestnetExecution(
        settings(), store, transport=httpx.MockTransport(handler_for(seen)),
    )
    try:
        with pytest.raises(SpotExecutionError, match="FIFO cost-basis ledger is incomplete"):
            await client.submit_limit_order(
                symbol="BTCUSDT",
                side="BUY",
                quantity=Decimal("0.00010"),
                price=Decimal("50000"),
                client_order_id="mumin-incomplete-ledger-block",
            )
        assert seen == []
        assert store.is_halted() == (True, "PNL_LEDGER_INCOMPLETE")
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_modern_oco_uses_order_list_endpoint_and_persists_both_legs(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url).startswith(TESTNET_REST_BASE_URL)
        path = request.url.path
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "50000.00"})
        if path == "/api/v3/account":
            account = account_info()
            account["balances"][1]["free"] = "0.00100"
            return httpx.Response(200, json=account)
        if path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if path == "/api/v3/myTrades":
            return httpx.Response(200, json=[])
        if path == "/api/v3/orderList/oco" and request.method == "POST":
            params = request.url.params
            assert params["aboveType"] == "LIMIT_MAKER"
            assert params["abovePrice"] == "51000.00"
            assert params["belowType"] == "STOP_LOSS"
            assert params["belowStopPrice"] == "49000.00"
            assert params["quantity"] == "0.00011"
            return httpx.Response(200, json={
                "orderListId": 88, "contingencyType": "OCO",
                "listStatusType": "EXEC_STARTED", "listOrderStatus": "EXECUTING",
                "listClientOrderId": "mumin-oco-001", "symbol": "BTCUSDT",
                "orders": [
                    {"symbol": "BTCUSDT", "orderId": 801, "clientOrderId": "tp-child"},
                    {"symbol": "BTCUSDT", "orderId": 802, "clientOrderId": "sl-child"},
                ],
                "orderReports": [
                    {"symbol": "BTCUSDT", "orderId": 801, "clientOrderId": "tp-child",
                     "executedQty": "0", "status": "NEW"},
                    {"symbol": "BTCUSDT", "orderId": 802, "clientOrderId": "sl-child",
                     "executedQty": "0", "status": "NEW"},
                ],
            })
        if path == "/api/v3/order" and request.method == "GET":
            return httpx.Response(200, json={
                "symbol": "BTCUSDT", "orderId": int(request.url.params["orderId"]),
                "status": "NEW", "executedQty": "0",
            })
        if path == "/api/v3/orderList" and request.method == "GET":
            return httpx.Response(200, json={
                "orderListId": 88, "contingencyType": "OCO",
                "listStatusType": "EXEC_STARTED", "listOrderStatus": "EXECUTING",
                "listClientOrderId": "mumin-oco-001", "symbol": "BTCUSDT",
                "orders": [
                    {"symbol": "BTCUSDT", "orderId": 801, "clientOrderId": "tp-child"},
                    {"symbol": "BTCUSDT", "orderId": 802, "clientOrderId": "sl-child"},
                ],
            })
        return httpx.Response(404, json={"code": -2013, "msg": "Order list does not exist."})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(settings(), store, transport=httpx.MockTransport(handler))
    try:
        await client.reconcile()
        result = await client.oco.place_sell_oco(
            symbol="BTCUSDT", quantity="0.00011",
            take_profit_price="51000.00", stop_price="49000.00",
            list_client_order_id="mumin-oco-001",
        )
        assert result["orderListId"] == 88
        assert client.oco.store.get("mumin-oco-001")["state"] == "EXECUTING"
        saved_oco = client.oco.store.get("mumin-oco-001")
        assert saved_oco["order_list_id"] == "88"
        assert saved_oco["above_order_id"] == "801"
        assert saved_oco["below_order_id"] == "802"
        assert saved_oco["above_client_order_id"] == "tp-child"
        assert saved_oco["below_client_order_id"] == "sl-child"
        assert sum(req.method == "POST" and req.url.path == "/api/v3/orderList/oco" for req in seen) == 1
        assert not any(req.url.path == "/api/v3/order/oco" for req in seen)
        duplicate = await client.oco.place_sell_oco(
            symbol="BTCUSDT", quantity="0.00011",
            take_profit_price="51000.00", stop_price="49000.00",
            list_client_order_id="mumin-oco-001",
        )
        assert duplicate["orderListId"] == 88
        assert sum(req.method == "POST" and req.url.path == "/api/v3/orderList/oco" for req in seen) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_oco_cancellation_cancels_the_order_list_as_one_unit(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/orderList" and request.method == "DELETE":
            assert request.url.params["listClientOrderId"] == "mumin-oco-cancel"
            return httpx.Response(200, json={
                "orderListId": 90, "contingencyType": "OCO",
                "listStatusType": "ALL_DONE", "listOrderStatus": "ALL_DONE",
                "listClientOrderId": "mumin-oco-cancel", "symbol": "BTCUSDT",
                "orderReports": [
                    {"symbol": "BTCUSDT", "orderId": 901, "executedQty": "0", "status": "CANCELED"},
                    {"symbol": "BTCUSDT", "orderId": 902, "executedQty": "0", "status": "CANCELED"},
                ],
            })
        return httpx.Response(404, json={"code": -2013, "msg": "not found"})

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        client.oco.store.register({
            "symbol": "BTCUSDT", "side": "SELL", "quantity": "0.00011",
            "listClientOrderId": "mumin-oco-cancel", "aboveType": "LIMIT_MAKER",
            "abovePrice": "51000.00", "belowType": "STOP_LOSS",
            "belowStopPrice": "49000.00", "newOrderRespType": "RESULT",
        })
        result = await client.oco.cancel_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-oco-cancel",
        )
        assert result["listOrderStatus"] == "ALL_DONE"
        assert client.oco.store.get("mumin-oco-cancel")["state"] == "CANCELED"
        assert sum(req.method == "DELETE" and req.url.path == "/api/v3/orderList" for req in seen) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_oco_partial_fill_syncs_real_exchange_trade_fills(tmp_path) -> None:
    seen: list[httpx.Request] = []
    trade = {
        "id": 991, "orderId": 911, "price": "51000.00", "qty": "0.00005",
        "quoteQty": "2.55", "commission": "0.00255", "commissionAsset": "USDT",
        "isBuyer": False, "isMaker": True, "time": 1_700_000_000_000,
    }
    oco_submitted = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal oco_submitted
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "50000.00"})
        if request.url.path == "/api/v3/account":
            account = account_info()
            account["balances"][1]["free"] = "0.00100"
            return httpx.Response(200, json=account)
        if request.url.path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v3/orderList/oco" and request.method == "POST":
            oco_submitted = True
            return httpx.Response(200, json={
                "orderListId": 91, "contingencyType": "OCO",
                "listStatusType": "EXEC_STARTED", "listOrderStatus": "EXECUTING",
                "listClientOrderId": "mumin-oco-partial", "symbol": "BTCUSDT",
                "orders": [
                    {"symbol": "BTCUSDT", "orderId": 911, "clientOrderId": "tp-partial"},
                    {"symbol": "BTCUSDT", "orderId": 912, "clientOrderId": "sl-partial"},
                ],
                "orderReports": [
                    {"symbol": "BTCUSDT", "orderId": 911, "clientOrderId": "tp-partial",
                     "executedQty": "0.00005", "status": "PARTIALLY_FILLED"},
                    {"symbol": "BTCUSDT", "orderId": 912, "clientOrderId": "sl-partial",
                     "executedQty": "0", "status": "NEW"},
                ],
            })
        if request.url.path == "/api/v3/myTrades":
            return httpx.Response(200, json=[trade] if oco_submitted and request.url.params.get("fromId") == "0" else [])
        return httpx.Response(404, json={"code": -2013, "msg": "not found"})

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.reconcile()
        await client.oco.place_sell_oco(
            symbol="BTCUSDT", quantity="0.00011",
            take_profit_price="51000.00", stop_price="49000.00",
            list_client_order_id="mumin-oco-partial",
        )
        assert client.store.last_trade_id("BTCUSDT") == 991
        assert client.store.get_trade_fill("BTCUSDT", 991)["commission"] == "0.00255"
        assert client.oco.store.get("mumin-oco-partial")["state"] == "PARTIAL_FILL_REVIEW"
        assert client.store.is_halted() == (True, "OCO_PARTIAL_FILL_REVIEW")
        assert any(req.url.path == "/api/v3/myTrades" for req in seen)
    finally:
        await client.close()


def test_oco_partial_fill_is_never_reported_as_fully_protected() -> None:
    partial = {
        "listOrderStatus": "EXECUTING",
        "orderReports": [
            {"status": "PARTIALLY_FILLED", "executedQty": "0.00005"},
            {"status": "CANCELED", "executedQty": "0"},
        ],
    }
    assert BinanceSpotTestnetOCO._state(partial) == "PARTIAL_FILL_REVIEW"
    partially_filled_then_canceled = {
        "listOrderStatus": "ALL_DONE",
        "orderReports": [
            {"status": "CANCELED", "origQty": "0.00011", "executedQty": "0.00005"},
            {"status": "CANCELED", "origQty": "0.00011", "executedQty": "0"},
        ],
    }
    assert BinanceSpotTestnetOCO._state(partially_filled_then_canceled) == "PARTIAL_FILL_REVIEW"


def test_oco_all_done_without_any_fill_is_canceled() -> None:
    canceled = {
        "listOrderStatus": "ALL_DONE",
        "orderReports": [
            {"status": "CANCELED", "executedQty": "0"},
            {"status": "EXPIRED", "executedQty": "0"},
        ],
    }
    assert BinanceSpotTestnetOCO._state(canceled) == "CANCELED"


@pytest.mark.asyncio
async def test_oco_create_timeout_recovers_existing_list_without_duplicate_post(tmp_path) -> None:
    seen: list[httpx.Request] = []
    post_attempts = 0
    list_payload = {
        "orderListId": 501, "contingencyType": "OCO",
        "listStatusType": "EXEC_STARTED", "listOrderStatus": "EXECUTING",
        "listClientOrderId": "mumin-oco-create-recovery", "symbol": "BTCUSDT",
        "orders": [
            {"symbol": "BTCUSDT", "orderId": 1501, "clientOrderId": "tp-recovery"},
            {"symbol": "BTCUSDT", "orderId": 1502, "clientOrderId": "sl-recovery"},
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_attempts
        seen.append(request)
        path = request.url.path
        if path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=symbol_info())
        if path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "50000.00"})
        if path == "/api/v3/account":
            account = account_info()
            account["balances"][1]["free"] = "0.00100"
            return httpx.Response(200, json=account)
        if path == "/api/v3/openOrders":
            return httpx.Response(200, json=[])
        if path == "/api/v3/myTrades":
            trade = {
                "id": 1701, "orderId": 1501, "price": "51000.00",
                "qty": "0.00005", "quoteQty": "2.55",
                "commission": "0.00255", "commissionAsset": "USDT",
                "isBuyer": False, "isMaker": True, "time": 1_700_000_000_000,
            }
            return httpx.Response(
                200,
                json=[trade] if post_attempts > 0 and request.url.params.get("fromId") == "0" else [],
            )
        if path == "/api/v3/orderList/oco" and request.method == "POST":
            post_attempts += 1
            raise httpx.ReadTimeout("response lost after exchange accepted OCO", request=request)
        if path == "/api/v3/order" and request.method == "GET":
            order_id = int(request.url.params["orderId"])
            return httpx.Response(200, json={
                "symbol": "BTCUSDT", "orderId": order_id,
                "status": "PARTIALLY_FILLED" if order_id == 1501 else "CANCELED",
                "origQty": "0.00011",
                "executedQty": "0.00005" if order_id == 1501 else "0",
            })
        if path == "/api/v3/orderList" and request.method == "GET":
            return httpx.Response(200, json=list_payload)
        return httpx.Response(404, json={"code": -2013, "msg": "not found"})

    store = SQLiteOrderStore(str(tmp_path / "orders.db"))
    client = BinanceSpotTestnetExecution(settings(), store, transport=httpx.MockTransport(handler))
    try:
        await client.reconcile()
        with pytest.raises(SpotOrderOutcomeUnknown):
            await client.oco.place_sell_oco(
                symbol="BTCUSDT", quantity="0.00011",
                take_profit_price="51000.00", stop_price="49000.00",
                list_client_order_id="mumin-oco-create-recovery",
            )
        recovered = await client.oco.place_sell_oco(
            symbol="BTCUSDT", quantity="0.00011",
            take_profit_price="51000.00", stop_price="49000.00",
            list_client_order_id="mumin-oco-create-recovery",
        )
        assert recovered["orderListId"] == 501
        assert [item["orderId"] for item in recovered["orders"]] == [1501, 1502]
        assert post_attempts == 1
        assert client.oco.store.get("mumin-oco-create-recovery")["state"] == "PARTIAL_FILL_REVIEW"
        assert client.store.is_halted() == (True, "OCO_PARTIAL_FILL_REVIEW")
        assert client.store.last_trade_id("BTCUSDT") == 1701
        assert client.store.get_trade_fill("BTCUSDT", 1701)["commission"] == "0.00255"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_oco_cancel_timeout_queries_exchange_before_retry(tmp_path) -> None:
    seen: list[httpx.Request] = []
    delete_attempts = 0
    active = {
        "orderListId": 502, "contingencyType": "OCO",
        "listStatusType": "EXEC_STARTED", "listOrderStatus": "EXECUTING",
        "listClientOrderId": "mumin-oco-cancel-recovery", "symbol": "BTCUSDT",
        "orders": [
            {"symbol": "BTCUSDT", "orderId": 1601, "clientOrderId": "tp-cancel"},
            {"symbol": "BTCUSDT", "orderId": 1602, "clientOrderId": "sl-cancel"},
        ],
    }
    canceled = {
        **active, "listStatusType": "ALL_DONE", "listOrderStatus": "ALL_DONE",
        "orderReports": [
            {"symbol": "BTCUSDT", "orderId": 1601, "executedQty": "0", "status": "CANCELED"},
            {"symbol": "BTCUSDT", "orderId": 1602, "executedQty": "0", "status": "CANCELED"},
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_attempts
        seen.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/order" and request.method == "GET":
            return httpx.Response(200, json={
                "symbol": "BTCUSDT", "orderId": int(request.url.params["orderId"]),
                "status": "NEW", "executedQty": "0",
            })
        if request.url.path == "/api/v3/orderList" and request.method == "GET":
            return httpx.Response(200, json=active)
        if request.url.path == "/api/v3/orderList" and request.method == "DELETE":
            delete_attempts += 1
            if delete_attempts == 1:
                raise httpx.ReadTimeout("cancel response lost", request=request)
            return httpx.Response(200, json=canceled)
        return httpx.Response(404, json={"code": -2013, "msg": "not found"})

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "orders.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        client.oco.store.register({
            "symbol": "BTCUSDT", "side": "SELL", "quantity": "0.00011",
            "listClientOrderId": "mumin-oco-cancel-recovery",
            "aboveType": "LIMIT_MAKER", "abovePrice": "51000.00",
            "belowType": "STOP_LOSS", "belowStopPrice": "49000.00",
            "newOrderRespType": "RESULT",
        })
        client.oco.store.update(
            "mumin-oco-cancel-recovery", state="EXECUTING", response=active,
        )
        with pytest.raises(SpotOrderOutcomeUnknown):
            await client.oco.cancel_order_list(
                symbol="BTCUSDT", list_client_order_id="mumin-oco-cancel-recovery",
            )
        result = await client.oco.cancel_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-oco-cancel-recovery",
        )
        assert result["listOrderStatus"] == "ALL_DONE"
        assert delete_attempts == 2
        assert sum(req.method == "GET" and req.url.path == "/api/v3/orderList" for req in seen) == 1
        assert client.oco.store.get("mumin-oco-cancel-recovery")["state"] == "CANCELED"
    finally:
        await client.close()



def bnb_commission_response(*, account_enabled: bool = True) -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "standardCommission": {
            "maker": "0.0010", "taker": "0.0012",
            "buyer": "0.0001", "seller": "0.0001",
        },
        "taxCommission": {
            "maker": "0.0001", "taker": "0.0002",
            "buyer": "0", "seller": "0",
        },
        "specialCommission": {
            "maker": "0", "taker": "0", "buyer": "0", "seller": "0",
        },
        "discount": {
            "enabledForAccount": account_enabled,
            "enabledForSymbol": True,
            "discountAsset": "BNB",
            "discount": "0.25",
        },
    }


def bnb_fee_handler(*, free_bnb: str, account_enabled: bool = True):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(TESTNET_REST_BASE_URL)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/account/commission":
            return httpx.Response(200, json=bnb_commission_response(account_enabled=account_enabled))
        if request.url.path == "/api/v3/ticker/price":
            return httpx.Response(200, json={"symbol": "BNBUSDT", "price": "600.00"})
        if request.url.path == "/api/v3/account":
            account = account_info()
            account["balances"].append({"asset": "BNB", "free": free_bnb, "locked": "0"})
            return httpx.Response(200, json=account)
        return httpx.Response(404, json={"code": -2013, "msg": "Not found."})
    return handler


@pytest.mark.asyncio
async def test_bnb_fee_readiness_requires_enabled_discount_and_sufficient_free_reserve(tmp_path) -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return await bnb_fee_handler(free_bnb="0.01")(request)

    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "bnb-ready.db")),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client._bnb_fee_readiness(
            symbol="BTCUSDT",
            buy_notional=Decimal("5.25"),
            sell_notional=Decimal("5.35"),
        )
        assert result["discount_enabled_for_account"] == "true"
        assert result["discount_enabled_for_symbol"] == "true"
        assert Decimal(result["free_bnb"]) >= Decimal(result["required_bnb"]) > 0
        assert result["reserve_factor"] == "1.50"
        assert all(request.method == "GET" for request in seen)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bnb_fee_readiness_rejects_insufficient_free_bnb_before_any_order(tmp_path) -> None:
    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "bnb-low.db")),
        transport=httpx.MockTransport(bnb_fee_handler(free_bnb="0.00000001")),
    )
    try:
        with pytest.raises(SpotExecutionError, match="Insufficient free BNB"):
            await client._bnb_fee_readiness(
                symbol="BTCUSDT",
                buy_notional=Decimal("5.25"),
                sell_notional=Decimal("5.35"),
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bnb_fee_readiness_rejects_disabled_account_discount_before_any_order(tmp_path) -> None:
    client = BinanceSpotTestnetExecution(
        settings(), SQLiteOrderStore(str(tmp_path / "bnb-disabled.db")),
        transport=httpx.MockTransport(
            bnb_fee_handler(free_bnb="0.01", account_enabled=False)
        ),
    )
    try:
        with pytest.raises(SpotExecutionError, match="not explicitly enabled"):
            await client._bnb_fee_readiness(
                symbol="BTCUSDT",
                buy_notional=Decimal("5.25"),
                sell_notional=Decimal("5.35"),
            )
    finally:
        await client.close()
