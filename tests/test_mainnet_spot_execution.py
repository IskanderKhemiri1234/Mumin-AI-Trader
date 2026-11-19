from __future__ import annotations

from urllib.parse import parse_qs
import sqlite3

import httpx
import pytest

from config.config import MAINNET_CONFIRMATION_PHRASE, Settings
from mumin_trader.exchanges.binance.mainnet_order_preflight import (
    BinanceSpotMainnetOrderPreflight,
)
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_mainnet_execution import (
    BinanceSpotMainnetExecution,
    SpotExecutionError,
)


def mainnet_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TRADING_ENV": "MAINNET",
        "TRADING_MODE": "LIVE",
        "ALLOW_MAINNET": True,
        "LIVE_TRADING_ENABLED": True,
        "MAINNET_CONFIRMATION": MAINNET_CONFIRMATION_PHRASE,
        "MAINNET_LEGAL_ELIGIBILITY_CONFIRMED": True,
        "BINANCE_MAINNET_API_KEY": "server-mainnet-key",
        "BINANCE_MAINNET_API_SECRET": "server-mainnet-secret",
        "MAINNET_ORDER_PREFLIGHT_ENABLED": True,
        "MAINNET_SPOT_EXECUTION_ENABLED": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_mainnet_preflight_is_pinned_and_posts_only_to_non_executing_test_endpoint() -> None:
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        assert request.url.host == "api.binance.com"
        if request.method == "GET" and request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={
                "symbols": [{
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "quoteAsset": "USDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "0", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "100", "stepSize": "0.00001"},
                        {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "0"},
                    ],
                }]
            })
        if request.method == "GET" and request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1700000000000})
        if request.method == "POST" and request.url.path == "/api/v3/order/test":
            query = parse_qs(request.url.query.decode() if isinstance(request.url.query, bytes) else request.url.query)
            assert "signature" in query
            assert query["symbol"] == ["BTCUSDT"]
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected Mainnet preflight request: {request.method} {request.url}")

    preflight = BinanceSpotMainnetOrderPreflight(
        mainnet_settings(MAINNET_SPOT_EXECUTION_ENABLED=False),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await preflight.preflight_limit_order(
            symbol="BTCUSDT",
            side="BUY",
            quantity="0.001",
            price="5000.00",
            client_order_id="mumin-preflight-0001",
        )
        assert result == {}
    finally:
        await preflight.close()

    assert ("POST", "/api/v3/order/test") in paths
    assert not any(method == "POST" and path == "/api/v3/order" for method, path in paths)


def test_mainnet_execution_refuses_default_paper_settings(tmp_path) -> None:
    with pytest.raises(SpotExecutionError):
        BinanceSpotMainnetExecution(
            Settings(_env_file=None),
            SQLiteOrderStore(str(tmp_path / "mainnet.sqlite3")),
        )


@pytest.mark.asyncio
async def test_mainnet_executor_and_preflight_use_fixed_binance_host(tmp_path) -> None:
    settings = mainnet_settings()
    store = SQLiteOrderStore(str(tmp_path / "mainnet.sqlite3"))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"serverTime": 1700000000000})
    )
    executor = BinanceSpotMainnetExecution(settings, store, transport=transport)
    try:
        assert str(executor._client.base_url) == "https://api.binance.com"
        assert str(executor._validator._client.base_url) == "https://api.binance.com"
    finally:
        await executor.close()


@pytest.mark.asyncio
async def test_mainnet_orders_require_reconciliation_younger_than_90_seconds(tmp_path) -> None:
    settings = mainnet_settings()
    store = SQLiteOrderStore(str(tmp_path / "mainnet-stale.sqlite3"))
    store.record_reconciliation([], [], [])
    with sqlite3.connect(store.database_path) as database:
        database.execute(
            "UPDATE spot_reconciliation_snapshots SET created_at=?",
            ("2000-01-01T00:00:00+00:00",),
        )
    executor = BinanceSpotMainnetExecution(
        settings,
        store,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"serverTime": 1700000000000})
        ),
    )
    try:
        with pytest.raises(SpotExecutionError, match="reconciliation is stale"):
            executor._ensure_recent_reconciliation()
    finally:
        await executor.close()
