from __future__ import annotations

import pytest

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.rest_client import BinanceAPIError
from scripts import check_binance_testnet_account as account_check


@pytest.mark.asyncio
async def test_http_451_is_reported_as_warning_only_skip(monkeypatch, tmp_path, capsys):
    settings = Settings(
        _env_file=None,
        TRADING_ENV=TradingEnvironment.TESTNET,
        TRADING_MODE=TradingMode.PAPER,
        ALLOW_MAINNET=False,
        LIVE_TRADING_ENABLED=False,
        TESTNET_ORDER_PREFLIGHT_ENABLED=False,
        TESTNET_SPOT_EXECUTION_ENABLED=False,
        BINANCE_API_KEY="test-key",
        BINANCE_API_SECRET="test-secret",
    )

    class RestrictedRunnerClient:
        async def account(self):
            raise BinanceAPIError(451, {"msg": "restricted location"})

        async def close(self):
            return None

    monkeypatch.setattr(account_check, "Settings", lambda: settings)
    monkeypatch.setattr(account_check, "BinanceSpotRESTClient", lambda _: RestrictedRunnerClient())
    output_file = tmp_path / "github-output.txt"
    summary_file = tmp_path / "github-summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    await account_check.main()

    output = capsys.readouterr().out
    assert "::warning title=Binance Testnet geo-restriction::" in output
    assert "SKIPPED:" in output
    assert "result=skipped_geo_restriction" in output_file.read_text()
    summary = summary_file.read_text()
    assert "skipped (HTTP 451)" in summary
    assert "not established by this run" in summary
    assert "no order" in summary.lower()


@pytest.mark.asyncio
async def test_http_401_remains_a_hard_failure(monkeypatch):
    settings = Settings(
        _env_file=None,
        TRADING_ENV=TradingEnvironment.TESTNET,
        TRADING_MODE=TradingMode.PAPER,
        ALLOW_MAINNET=False,
        LIVE_TRADING_ENABLED=False,
        TESTNET_ORDER_PREFLIGHT_ENABLED=False,
        TESTNET_SPOT_EXECUTION_ENABLED=False,
        BINANCE_API_KEY="test-key",
        BINANCE_API_SECRET="test-secret",
    )

    class InvalidCredentialsClient:
        async def account(self):
            raise BinanceAPIError(401, {"msg": "invalid key"})

        async def close(self):
            return None

    monkeypatch.setattr(account_check, "Settings", lambda: settings)
    monkeypatch.setattr(account_check, "BinanceSpotRESTClient", lambda _: InvalidCredentialsClient())

    with pytest.raises(SystemExit, match="HTTP 401"):
        await account_check.main()
