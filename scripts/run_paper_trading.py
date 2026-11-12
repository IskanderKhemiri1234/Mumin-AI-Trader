"""Default local Paper Trading entrypoint: no network and no API credentials required.

For a deterministic offline smoke trade run:
    python scripts/run_paper_trading.py
The Binance TESTNET-connected runner remains an explicit separate script.
"""
import asyncio

from run_mock_paper import main


if __name__ == "__main__":
    asyncio.run(main())
