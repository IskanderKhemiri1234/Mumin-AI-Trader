import pytest
from pydantic import ValidationError

from config.config import Settings, TradingEnvironment, TradingMode


def test_safe_defaults(monkeypatch):
    monkeypatch.delenv("WATCHLIST", raising=False)
    settings = Settings(_env_file=None)
    assert settings.TRADING_ENV == TradingEnvironment.TESTNET
    assert settings.TRADING_MODE == TradingMode.PAPER
    assert settings.INITIAL_CAPITAL_USD == 70
    assert settings.MAX_CAPITAL_ALLOCATION_USD == 70
    assert Settings.model_fields["WATCHLIST"].default == (
        "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,LINKUSDT"
    )
    assert settings.rest_base_url == "https://testnet.binance.vision"
    assert settings.websocket_base_url == "wss://stream.testnet.binance.vision/ws"


def test_mainnet_is_blocked_without_explicit_flags():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, TRADING_ENV="MAINNET")


def test_mainnet_is_hard_blocked_even_if_all_opt_in_flags_are_set():
    with pytest.raises(ValidationError, match="Mainnet confirmation phrase"):
        Settings(
            _env_file=None,
            TRADING_ENV="MAINNET",
            TRADING_MODE="LIVE",
            ALLOW_MAINNET=True,
            LIVE_TRADING_ENABLED=True,
            MAINNET_CONFIRMATION="I_UNDERSTAND_LIVE_RISK",
            MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=True,
            BINANCE_API_KEY="test-key",
            BINANCE_API_SECRET="test-secret",
        )


def test_endpoint_properties_fail_closed_if_model_validation_is_bypassed():
    unvalidated = Settings.model_construct(TRADING_ENV=TradingEnvironment.MAINNET)
    with pytest.raises(ValueError, match="Mainnet requires TRADING_MODE=LIVE"):
        _ = unvalidated.rest_base_url
    with pytest.raises(ValueError, match="Mainnet requires TRADING_MODE=LIVE"):
        _ = unvalidated.websocket_base_url


def test_testnet_rejects_live_execution():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LIVE_TRADING_ENABLED=True)


def test_testnet_execution_requires_both_explicit_gates():
    with pytest.raises(ValidationError, match="requires the order preflight gate"):
        Settings(
            _env_file=None,
            TRADING_MODE=TradingMode.TESTNET,
            TESTNET_SPOT_EXECUTION_ENABLED=True,
            TESTNET_ORDER_PREFLIGHT_ENABLED=False,
        )
