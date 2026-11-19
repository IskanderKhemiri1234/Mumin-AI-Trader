from __future__ import annotations

import httpx
import pytest

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.mainnet_order_preflight import (
    BinanceSpotMainnetOrderPreflight,
    MAINNET_REST_BASE_URL,
    MainnetPreflightError,
)
from mumin_trader.exchanges.binance.spot_mainnet_execution import (
    BinanceSpotMainnetExecution,
    SpotExecutionError,
)


def mainnet_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "TRADING_ENV": TradingEnvironment.MAINNET,
        "TRADING_MODE": TradingMode.LIVE,
        "ALLOW_MAINNET": True,
        "LIVE_TRADING_ENABLED": True,
        "MAINNET_ORDER_PREFLIGHT_ENABLED": True,
        "MAINNET_SPOT_EXECUTION_ENABLED": True,
        "MAINNET_CONFIRMATION": "I_ACKNOWLEDGE_REAL_MONEY_SPOT_TRADING",
        "MAINNET_LEGAL_ELIGIBILITY_CONFIRMED": True,
        "BINANCE_MAINNET_API_KEY": "server-only-mainnet-key",
        "BINANCE_MAINNET_API_SECRET": "server-only-mainnet-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_mainnet_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.TRADING_ENV is TradingEnvironment.TESTNET
    assert settings.TRADING_MODE is TradingMode.PAPER
    assert settings.ALLOW_MAINNET is False
    assert settings.LIVE_TRADING_ENABLED is False
    assert settings.MAINNET_ORDER_PREFLIGHT_ENABLED is False
    assert settings.MAINNET_SPOT_EXECUTION_ENABLED is False


def test_mainnet_configuration_requires_all_explicit_gates() -> None:
    with pytest.raises(ValueError, match="both independent live-trading gates"):
        Settings(
            _env_file=None,
            TRADING_ENV="MAINNET",
            TRADING_MODE="LIVE",
            MAINNET_ORDER_PREFLIGHT_ENABLED=True,
            BINANCE_MAINNET_API_KEY="key",
            BINANCE_MAINNET_API_SECRET="secret",
            MAINNET_CONFIRMATION="I_ACKNOWLEDGE_REAL_MONEY_SPOT_TRADING",
            MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=True,
        )


def test_mainnet_execution_cannot_be_enabled_without_preflight() -> None:
    with pytest.raises(ValueError, match="requires the Mainnet order preflight gate"):
        Settings(
            _env_file=None,
            TRADING_ENV="MAINNET",
            TRADING_MODE="LIVE",
            ALLOW_MAINNET=True,
            LIVE_TRADING_ENABLED=True,
            MAINNET_SPOT_EXECUTION_ENABLED=True,
            MAINNET_CONFIRMATION="I_ACKNOWLEDGE_REAL_MONEY_SPOT_TRADING",
            MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=True,
        )


def test_mainnet_adapter_rejects_incomplete_confirmation() -> None:
    settings = mainnet_settings()
    settings.MAINNET_CONFIRMATION = "wrong"
    with pytest.raises(SpotExecutionError, match="confirmation phrase"):
        BinanceSpotMainnetExecution._assert_safety_gate(settings)


@pytest.mark.asyncio
async def test_mainnet_preflight_is_pinned_and_only_calls_non_executing_order_test() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={"symbols": [{
                "status": "TRADING",
                "isSpotTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "1000", "stepSize": "0.00001"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                ],
            }]})
        if request.method == "POST" and request.url.path == "/api/v3/order/test":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"msg": "unexpected endpoint"})

    preflight = BinanceSpotMainnetOrderPreflight(
        mainnet_settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await preflight.preflight_limit_order(
            symbol="BTCUSDT",
            side="BUY",
            quantity="0.00010",
            price="50000",
            client_order_id="mumin-mainnet-preflight-001",
        )
    finally:
        await preflight.close()

    assert result == {}
    assert MAINNET_REST_BASE_URL == "https://api.binance.com"
    assert all(request.url.host == "api.binance.com" for request in requests)
    assert [request.url.path for request in requests] == [
        "/api/v3/time",
        "/api/v3/exchangeInfo",
        "/api/v3/time",
        "/api/v3/order/test",
    ]
    assert not any(request.url.path == "/api/v3/order" for request in requests)
