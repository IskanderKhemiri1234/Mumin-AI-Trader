from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.config import (
    MAINNET_CONFIRMATION_PHRASE,
    Settings,
    TradingEnvironment,
    TradingMode,
)


def test_mainnet_is_disabled_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.TRADING_ENV is TradingEnvironment.TESTNET
    assert settings.TRADING_MODE is TradingMode.PAPER
    assert settings.ALLOW_MAINNET is False
    assert settings.LIVE_TRADING_ENABLED is False
    assert settings.MAINNET_ORDER_PREFLIGHT_ENABLED is False
    assert settings.MAINNET_SPOT_EXECUTION_ENABLED is False
    assert settings.rest_base_url == "https://testnet.binance.vision"


def test_mainnet_rejects_partial_or_missing_authorization() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            TRADING_ENV="MAINNET",
            TRADING_MODE="LIVE",
            BINANCE_MAINNET_API_KEY="key",
            BINANCE_MAINNET_API_SECRET="secret",
        )


def test_mainnet_execution_requires_independent_preflight_gate() -> None:
    with pytest.raises(ValidationError, match="requires Mainnet order preflight"):
        Settings(
            _env_file=None,
            TRADING_ENV="MAINNET",
            TRADING_MODE="LIVE",
            ALLOW_MAINNET=True,
            LIVE_TRADING_ENABLED=True,
            MAINNET_CONFIRMATION=MAINNET_CONFIRMATION_PHRASE,
            MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=True,
            BINANCE_MAINNET_API_KEY="key",
            BINANCE_MAINNET_API_SECRET="secret",
            MAINNET_SPOT_EXECUTION_ENABLED=True,
            MAINNET_ORDER_PREFLIGHT_ENABLED=False,
        )


def test_fully_gated_mainnet_configuration_uses_only_official_host() -> None:
    settings = Settings(
        _env_file=None,
        TRADING_ENV="MAINNET",
        TRADING_MODE="LIVE",
        ALLOW_MAINNET=True,
        LIVE_TRADING_ENABLED=True,
        MAINNET_CONFIRMATION=MAINNET_CONFIRMATION_PHRASE,
        MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=True,
        BINANCE_MAINNET_API_KEY="server-key",
        BINANCE_MAINNET_API_SECRET="server-secret",
        MAINNET_ORDER_PREFLIGHT_ENABLED=True,
        MAINNET_SPOT_EXECUTION_ENABLED=True,
    )
    assert settings.rest_base_url == "https://api.binance.com"
    assert settings.websocket_base_url == "wss://stream.binance.com:9443/ws"


def test_testnet_rejects_mainnet_flags() -> None:
    with pytest.raises(ValidationError, match="prohibited in TESTNET"):
        Settings(
            _env_file=None,
            TRADING_ENV="TESTNET",
            ALLOW_MAINNET=True,
        )
