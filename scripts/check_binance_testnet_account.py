from __future__ import annotations

import asyncio
from decimal import Decimal

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient


async def main() -> None:
    settings = Settings()
    if settings.TRADING_ENV is not TradingEnvironment.TESTNET:
        raise SystemExit("Refusing account check: TRADING_ENV must be TESTNET.")
    if settings.TRADING_MODE is not TradingMode.PAPER:
        raise SystemExit("Refusing account check: TRADING_MODE must remain PAPER.")
    if settings.ALLOW_MAINNET or settings.LIVE_TRADING_ENABLED:
        raise SystemExit("Refusing account check: Mainnet/live flags must remain disabled.")
    if not settings.BINANCE_API_KEY.get_secret_value() or not settings.BINANCE_API_SECRET.get_secret_value():
        raise SystemExit("Missing BINANCE_API_KEY or BINANCE_API_SECRET GitHub Secret.")

    client = BinanceSpotRESTClient(settings)
    try:
        await client.ping()
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
        print("Binance Spot TESTNET account connection: OK")
        print(f"Configured strategy capital ceiling: {settings.MAX_CAPITAL_ALLOCATION_USD} USDT")
        print(f"USDT free: {free}")
        print(f"USDT locked: {locked}")
        print(f"USDT total: {total}")
        print("Order submission: NOT ATTEMPTED (read-only verification)")
        if total < settings.MAX_CAPITAL_ALLOCATION_USD:
            print("NOTICE: Testnet USDT balance is below the configured 70 USDT ceiling.")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
