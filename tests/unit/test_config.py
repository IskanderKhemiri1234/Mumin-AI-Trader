import pytest
from pydantic import ValidationError

from config.config import Settings, TradingEnvironment, TradingMode


def test_safe_defaults():
    settings = Settings(_env_file=None)
    assert settings.TRADING_ENV == TradingEnvironment.TESTNET
    assert settings.TRADING_MODE == TradingMode.PAPER


def test_mainnet_is_blocked_without_explicit_flags():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, TRADING_ENV="MAINNET")


def test_testnet_rejects_live_execution():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LIVE_TRADING_ENABLED=True)
