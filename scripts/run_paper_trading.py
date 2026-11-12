import asyncio

from config.config import get_settings
from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient


async def main():
    settings = get_settings()
    if settings.TRADING_MODE.value != "PAPER":
        raise RuntimeError("This script only supports PAPER mode.")
    client = BinanceSpotRESTClient(settings)
    try:
        await client.ping()
        print(f"Binance API reachable ({settings.TRADING_ENV.value}).")
        for symbol in settings.watchlist_symbols:
            data = await client.ticker_24h(symbol)
            print(symbol, "lastPrice=", data.get("lastPrice"))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
