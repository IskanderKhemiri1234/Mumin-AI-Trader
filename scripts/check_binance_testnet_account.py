from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.rest_client import BinanceAPIError, BinanceSpotRESTClient


def report_geo_block_skip() -> None:
    message = (
        "SKIPPED: Binance returned HTTP 451 because this GitHub-hosted runner is not "
        "eligible by location. Credentials remain unverified by this run; no order was submitted."
    )
    print(f"::warning title=Binance Testnet geo-restriction::{message}")
    print(message)

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write("result=skipped_geo_restriction\n")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(
                "### Binance Spot Testnet account check: skipped (HTTP 451)\n\n"
                "- Reason: GitHub-hosted runner rejected by Binance restricted-location eligibility.\n"
                "- Credential validity: **not established by this run**.\n"
                "- Order submission: **not attempted; no order was submitted**.\n"
                "- Required CI compile, tests, lint and Paper smoke remain independent and mandatory.\n"
            )


async def main() -> None:
    settings = Settings()
    if settings.TRADING_ENV is not TradingEnvironment.TESTNET:
        raise SystemExit("Refusing account check: TRADING_ENV must be TESTNET.")
    if settings.TRADING_MODE is not TradingMode.PAPER:
        raise SystemExit("Refusing account check: TRADING_MODE must remain PAPER.")
    if settings.ALLOW_MAINNET or settings.LIVE_TRADING_ENABLED:
        raise SystemExit("Refusing account check: Mainnet/live flags must remain disabled.")
    if settings.TESTNET_ORDER_PREFLIGHT_ENABLED or settings.TESTNET_SPOT_EXECUTION_ENABLED:
        raise SystemExit("Refusing account check: order preflight and execution gates must remain disabled.")
    if not settings.BINANCE_API_KEY.get_secret_value() or not settings.BINANCE_API_SECRET.get_secret_value():
        raise SystemExit("Missing BINANCE_API_KEY or BINANCE_API_SECRET GitHub Secret.")

    client = BinanceSpotRESTClient(settings)
    try:
        # Verify credentials with the signed, read-only account endpoint directly.
        # A public ping can be blocked independently by Binance location eligibility.
        account = await client.account()
        balances = {
            str(item.get("asset", "")).upper(): (
                Decimal(str(item.get("free", "0"))),
                Decimal(str(item.get("locked", "0"))),
            )
            for item in account.get("balances", [])
        }
        free, locked = balances.get("USDT", (Decimal("0"), Decimal("0")))
        total = free + locked
        print("Binance Spot TESTNET signed account verification: OK")
        print(f"Configured strategy capital ceiling: {settings.MAX_CAPITAL_ALLOCATION_USD} USDT")
        print(f"USDT free: {free}")
        print(f"USDT locked: {locked}")
        print(f"USDT total: {total}")
        print("Order submission: NOT ATTEMPTED (read-only verification)")
        if total < settings.MAX_CAPITAL_ALLOCATION_USD:
            print("NOTICE: Testnet USDT balance is below the configured 70 USDT ceiling.")
    except BinanceAPIError as exc:
        if exc.status_code == 451:
            report_geo_block_skip()
            return
        if exc.status_code == 401:
            message = (
                "Binance rejected the signed account request with HTTP 401. Check the "
                "Testnet API key/secret and USER DATA permission. No order was submitted."
            )
        else:
            message = (
                f"Binance signed account verification failed with HTTP {exc.status_code}. "
                "No order was submitted."
            )
        raise SystemExit(message) from None
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
