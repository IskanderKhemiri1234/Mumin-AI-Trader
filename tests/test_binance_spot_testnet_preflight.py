from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.testnet_order_preflight import (
    BinanceSpotTestnetOrderPreflight,
    TESTNET_REST_BASE_URL,
    TestnetPreflightError,
)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TRADING_ENV": TradingEnvironment.TESTNET,
        "TRADING_MODE": TradingMode.PAPER,
        "ALLOW_MAINNET": False,
        "LIVE_TRADING_ENABLED": False,
        "TESTNET_ORDER_PREFLIGHT_ENABLED": True,
        "BINANCE_API_KEY": "test-api-key",
        "BINANCE_API_SECRET": "test-api-secret",
        "WATCHLIST": "BTCUSDT,ETHUSDT",
        "MAX_TRADE_ALLOCATION_USD": Decimal("5.00"),
    }
    values.update(overrides)
    return Settings(**values)


def exchange_info() -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "isSpotTradingAllowed": True,
                "filters": [
                    {
                        "filterType": "PRICE_FILTER",
                        "minPrice": "0.01",
                        "maxPrice": "1000000.00",
                        "tickSize": "0.01",
                    },
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.00001",
                        "maxQty": "100.00000",
                        "stepSize": "0.00001",
                    },
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5.00"},
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_preflight_is_disabled_by_default() -> None:
    settings = make_settings(TESTNET_ORDER_PREFLIGHT_ENABLED=False)
    with pytest.raises(TestnetPreflightError, match="disabled by default"):
        BinanceSpotTestnetOrderPreflight(settings)


@pytest.mark.asyncio
async def test_preflight_refuses_mainnet_and_live_flags() -> None:
    with pytest.raises(ValueError, match="(?i)mainnet|live"):
        make_settings(ALLOW_MAINNET=True)


@pytest.mark.asyncio
async def test_preflight_refuses_non_testnet_environment() -> None:
    with pytest.raises(ValueError, match="TESTNET"):
        make_settings(TRADING_ENV=TradingEnvironment.MAINNET)


@pytest.mark.asyncio
async def test_limit_order_preflight_only_calls_testnet_non_execution_endpoint() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url).startswith(TESTNET_REST_BASE_URL)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json=exchange_info())
        if request.url.path == "/api/v3/order/test":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"msg": "unexpected endpoint"})

    client = BinanceSpotTestnetOrderPreflight(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.preflight_limit_order(
            symbol="BTCUSDT",
            side="BUY",
            quantity="0.00010",
            price="50000.00",
            client_order_id="mumin-test-0001",
        )
    finally:
        await client.close()

    assert result == {}
    assert [request.url.path for request in seen] == [
        "/api/v3/exchangeInfo",
        "/api/v3/time",
        "/api/v3/order/test",
    ]
    signed_request = seen[-1]
    assert "signature" in signed_request.url.params
    assert signed_request.url.params["newClientOrderId"] == "mumin-test-0001"


@pytest.mark.asyncio
async def test_preflight_rejects_symbol_outside_watchlist() -> None:
    client = BinanceSpotTestnetOrderPreflight(make_settings())
    try:
        with pytest.raises(TestnetPreflightError, match="watchlist"):
            await client.validate_limit_order(
                symbol="DOGEUSDT",
                side="BUY",
                quantity="1",
                price="1",
                client_order_id="mumin-test-0002",
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_preflight_rejects_quantity_not_aligned_to_step_size() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=exchange_info())

    client = BinanceSpotTestnetOrderPreflight(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TestnetPreflightError, match="stepSize"):
            await client.validate_limit_order(
                symbol="BTCUSDT",
                side="BUY",
                quantity="0.000101",
                price="50000.00",
                client_order_id="mumin-test-0003",
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_preflight_rejects_notional_above_trade_ceiling() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=exchange_info())

    client = BinanceSpotTestnetOrderPreflight(
        make_settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TestnetPreflightError, match="per-trade ceiling"):
            await client.validate_limit_order(
                symbol="BTCUSDT",
                side="BUY",
                quantity="0.00020",
                price="50000.00",
                client_order_id="mumin-test-0004",
            )
    finally:
        await client.close()



@pytest.mark.asyncio
async def test_zero_exchange_filter_steps_are_treated_as_disabled(tmp_path) -> None:
    payload = exchange_info()
    metadata = payload["symbols"][0]
    metadata["filters"][0].update({"minPrice": "0", "maxPrice": "0", "tickSize": "0"})
    metadata["filters"][1].update({"minQty": "0", "maxQty": "0", "stepSize": "0"})

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = BinanceSpotTestnetOrderPreflight(
        make_settings(MAX_TRADE_ALLOCATION_USD=Decimal("6")),
        transport=httpx.MockTransport(handler),
    )
    try:
        params = await client.validate_limit_order(
            symbol="BTCUSDT", side="BUY", quantity="0.00010",
            price="50000.01", client_order_id="mumin-test-zero-filter",
        )
        assert params["price"] == "50000.01"
    finally:
        await client.close()
