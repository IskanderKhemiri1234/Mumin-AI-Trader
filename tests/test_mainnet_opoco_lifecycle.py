from __future__ import annotations

import httpx
import pytest

from config.config import MAINNET_CONFIRMATION_PHRASE, Settings
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_mainnet_execution import BinanceSpotMainnetExecution


def _settings(path) -> Settings:
    return Settings(
        _env_file=None,
        TRADING_ENV="MAINNET",
        TRADING_MODE="LIVE",
        ALLOW_MAINNET=True,
        LIVE_TRADING_ENABLED=True,
        MAINNET_ORDER_PREFLIGHT_ENABLED=True,
        MAINNET_SPOT_EXECUTION_ENABLED=True,
        MAINNET_CONFIRMATION=MAINNET_CONFIRMATION_PHRASE,
        MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=True,
        BINANCE_MAINNET_API_KEY="test-key",
        BINANCE_MAINNET_API_SECRET="test-secret",
        MAINNET_ORDER_DATABASE_URL=f"sqlite:///{path}",
    )


def _opoco_request() -> dict[str, str]:
    return {
        "listClientOrderId": "mumin-list-0001",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.0001",
        "workingClientOrderId": "mumin-work-0001",
        "pendingAboveClientOrderId": "mumin-tp-0001",
        "pendingBelowClientOrderId": "mumin-sl-0001",
    }


def _transport(*, working_status: str = "FILLED", working_filled: str = "0.0001",
               take_profit_status: str = "NEW", stop_status: str = "NEW"):
    ids = _opoco_request()
    parent = {
        "orderListId": 701,
        "listClientOrderId": ids["listClientOrderId"],
        "listStatusType": "EXEC_STARTED",
        "listOrderStatus": "EXECUTING" if working_status not in {"CANCELED", "EXPIRED"} else "ALL_DONE",
        "orders": [
            {"symbol": "BTCUSDT", "orderId": 801, "clientOrderId": ids["workingClientOrderId"]},
            {"symbol": "BTCUSDT", "orderId": 802, "clientOrderId": ids["pendingAboveClientOrderId"]},
            {"symbol": "BTCUSDT", "orderId": 803, "clientOrderId": ids["pendingBelowClientOrderId"]},
        ],
    }
    reports = {
        ids["workingClientOrderId"]: {
            "symbol": "BTCUSDT", "orderId": 801, "clientOrderId": ids["workingClientOrderId"],
            "side": "BUY", "type": "LIMIT", "status": working_status,
            "origQty": "0.0001", "executedQty": working_filled,
        },
        ids["pendingAboveClientOrderId"]: {
            "symbol": "BTCUSDT", "orderId": 802, "clientOrderId": ids["pendingAboveClientOrderId"],
            "side": "SELL", "type": "LIMIT_MAKER", "status": take_profit_status,
            "origQty": "0.0001", "executedQty": "0",
        },
        ids["pendingBelowClientOrderId"]: {
            "symbol": "BTCUSDT", "orderId": 803, "clientOrderId": ids["pendingBelowClientOrderId"],
            "side": "SELL", "type": "STOP_LOSS_LIMIT", "status": stop_status,
            "origQty": "0.0001", "executedQty": "0",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.method == "GET" and request.url.path == "/api/v3/orderList":
            return httpx.Response(200, json=parent)
        if request.method == "DELETE" and request.url.path == "/api/v3/orderList":
            return httpx.Response(200, json={"orderListId": 701, "listClientOrderId": ids["listClientOrderId"]})
        if request.method == "GET" and request.url.path == "/api/v3/order":
            client_id = request.url.params.get("origClientOrderId")
            if client_id in reports:
                return httpx.Response(200, json=reports[client_id])
        return httpx.Response(404, json={"code": -2013, "msg": "Order does not exist."})

    return httpx.MockTransport(handler)


def _executor(path, transport) -> BinanceSpotMainnetExecution:
    executor = BinanceSpotMainnetExecution(
        _settings(path),
        SQLiteOrderStore(str(path)),
        transport=transport,
    )

    async def no_fill_sync(_response):
        return None

    executor._sync_fills_if_needed = no_fill_sync
    executor.opoco.store.register(_opoco_request())
    return executor


@pytest.mark.asyncio
async def test_partial_fill_is_halted_and_requires_manual_review(tmp_path) -> None:
    path = tmp_path / "partial.sqlite3"
    executor = _executor(
        path,
        _transport(working_status="PARTIALLY_FILLED", working_filled="0.00003"),
    )
    try:
        result = await executor.opoco.get_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-list-0001",
        )
        assert executor.opoco._state(result) == "PARTIAL_FILL_REVIEW"
        assert executor.opoco.store.get("mumin-list-0001")["state"] == "PARTIAL_FILL_REVIEW"
        assert executor.store.is_halted() == (True, "OPOCO_PARTIAL_FILL_REVIEW")
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_protection_armed_state_survives_process_restart_and_reconciliation(tmp_path) -> None:
    path = tmp_path / "restart.sqlite3"
    first = _executor(path, _transport())
    try:
        result = await first.opoco.get_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-list-0001",
        )
        assert first.opoco._state(result) == "PROTECTION_ARMED"
        assert first.opoco.store.get("mumin-list-0001")["state"] == "PROTECTION_ARMED"
    finally:
        await first.close()

    second = _executor(path, _transport())
    try:
        persisted = second.opoco.store.get("mumin-list-0001")
        assert persisted is not None
        assert persisted["state"] == "PROTECTION_ARMED"
        recovered = await second.opoco.get_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-list-0001",
        )
        assert second.opoco._state(recovered) == "PROTECTION_ARMED"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_cancel_after_partial_fill_keeps_halt_and_partial_fill_review(tmp_path) -> None:
    path = tmp_path / "partial-cancel.sqlite3"
    executor = _executor(
        path,
        _transport(working_status="PARTIALLY_FILLED", working_filled="0.00003"),
    )
    try:
        result = await executor.opoco.cancel_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-list-0001",
        )
        assert executor.opoco._state(result) == "PARTIAL_FILL_REVIEW"
        assert executor.opoco.store.get("mumin-list-0001")["state"] == "PARTIAL_FILL_REVIEW"
        assert executor.store.is_halted() == (True, "OPOCO_PARTIAL_FILL_REVIEW")
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_cancel_unfilled_working_order_persists_canceled_state(tmp_path) -> None:
    path = tmp_path / "unfilled-cancel.sqlite3"
    executor = _executor(
        path,
        _transport(working_status="CANCELED", working_filled="0"),
    )
    try:
        result = await executor.opoco.cancel_order_list(
            symbol="BTCUSDT", list_client_order_id="mumin-list-0001",
        )
        assert executor.opoco._state(result) == "CANCELED"
        assert executor.opoco.store.get("mumin-list-0001")["state"] == "CANCELED"
    finally:
        await executor.close()
