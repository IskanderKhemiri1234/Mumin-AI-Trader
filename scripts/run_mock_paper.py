from __future__ import annotations

import argparse
import asyncio
import os
from decimal import Decimal
from pathlib import Path

from config.config import get_settings
from mumin_trader.execution.execution_engine import ExecutionEngine, OrderSide
from mumin_trader.execution.paper_ledger import PaperLedger
from mumin_trader.market_scanner.mock_exchange import MockExchange, MockQuote


def _database_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Local mock runner requires a SQLite DATABASE_URL.")
    path = database_url[len(prefix):]
    return path if path == ":memory:" else str(Path(path))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run one fully offline synthetic Paper trade.")
    parser.add_argument("--db", default=None, help="Optional SQLite file path.")
    args = parser.parse_args()

    settings = get_settings()
    if settings.TRADING_MODE.value != "PAPER" or settings.LIVE_TRADING_ENABLED:
        raise RuntimeError("Mock runner refuses non-PAPER or live-enabled configuration.")
    if settings.TRADING_ENV.value == "MAINNET":
        raise RuntimeError("Mock runner refuses MAINNET configuration.")

    database = args.db or os.getenv("MOCK_PAPER_DATABASE") or _database_path(settings.DATABASE_URL)
    ledger = PaperLedger(database, initial_cash=settings.INITIAL_CAPITAL_USD)
    exchange = MockExchange()
    initial = exchange.quote("BTCUSDT")

    # Synthetic quote path deliberately includes a modest upward move. These
    # numbers are fixtures for validating accounting, not live prices or alpha.
    entry_quote = initial
    exit_quote = MockQuote(
        symbol="BTCUSDT",
        bid=Decimal("60720.00"),
        ask=Decimal("60726.00"),
        last=Decimal("60723.00"),
    )

    engine = ExecutionEngine(
        fee_rate=Decimal("0.001"),
        slippage_bps=Decimal("2"),
        quote_asset=settings.QUOTE_ASSET,
        require_verified_commission=True,
    )
    allocation = min(
        settings.MAX_TRADE_ALLOCATION_USD,
        settings.INITIAL_CAPITAL_USD * (Decimal(1) - settings.CAPITAL_RESERVE_PERCENT / Decimal(100)),
    )
    quantity = (allocation / entry_quote.ask).quantize(Decimal("0.00000001"))

    buy = await engine.submit_paper_order(
        symbol=entry_quote.symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        reference_price=entry_quote.ask,
        quantity_step=Decimal("0.00000001"),
        min_notional=Decimal("4.95"),
        fee_rate_override=Decimal("0.001"),
    )
    ledger.record_fill(buy)

    sell = await engine.submit_paper_order(
        symbol=exit_quote.symbol,
        side=OrderSide.SELL,
        quantity=buy.quantity,
        reference_price=exit_quote.bid,
        quantity_step=Decimal("0.00000001"),
        min_notional=Decimal("5"),
        fee_rate_override=Decimal("0.001"),
    )
    ledger.record_fill(sell)

    net_pnl = ledger.record_closed_trade(
        symbol=buy.symbol,
        quantity=buy.quantity,
        entry_price=buy.fill_price,
        exit_price=sell.fill_price,
        entry_fee=buy.fee_quote,
        exit_fee=sell.fee_quote,
        data_source=initial.source,
    )
    portfolio = ledger.portfolio()
    counts = ledger.counts()
    print("=== MUMIN AI TRADER | OFFLINE MOCK PAPER ===")
    print("Network calls: 0 | API keys: none | Exchange orders: 0")
    print(f"Market data: {initial.source}")
    print(f"Database: {database}")
    print(f"Trade: {buy.symbol} BUY -> SELL | quantity={buy.quantity}")
    print(f"Entry fill: {buy.fill_price} | exit fill: {sell.fill_price}")
    print(f"Fees paid (both legs): {buy.fee_quote + sell.fee_quote} {settings.QUOTE_ASSET}")
    print(f"Gross PnL: {(sell.fill_price - buy.fill_price) * buy.quantity} {settings.QUOTE_ASSET}")
    print(f"Net realized PnL: {net_pnl} {settings.QUOTE_ASSET}")
    print(f"Paper cash/equity: {portfolio.cash} {settings.QUOTE_ASSET}")
    print(f"Persisted records: {counts['orders']} orders, {counts['trades']} closed trades")
    print("NOTICE: synthetic demonstration only; not a market forecast or evidence of profitability.")


if __name__ == "__main__":
    asyncio.run(main())
