from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from config.config import TradingEnvironment, TradingMode, get_settings
from mumin_trader.execution.execution_engine import ExecutionEngine, OrderSide
from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient

LOGGER = logging.getLogger("mumin_trader.autonomous")
SMOKE_RUN_KEY = "initial-paper-smoke-v1"


def _database_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("The autonomous paper runner currently requires a SQLite DATABASE_URL.")
    path = Path(database_url[len(prefix):])
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _initialize_database(connection: sqlite3.Connection, initial_capital: Decimal) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_account (
            account_id INTEGER PRIMARY KEY CHECK (account_id = 1),
            initial_capital TEXT NOT NULL,
            cash_balance TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS paper_orders (
            order_id TEXT PRIMARY KEY,
            run_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity TEXT NOT NULL,
            reference_price TEXT NOT NULL,
            fill_price TEXT NOT NULL,
            gross_quote TEXT NOT NULL,
            fee_quote TEXT NOT NULL,
            net_quote_flow TEXT NOT NULL,
            cash_after TEXT NOT NULL,
            status TEXT NOT NULL,
            purpose TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """INSERT OR IGNORE INTO paper_account
           (account_id, initial_capital, cash_balance, created_at)
           VALUES (1, ?, ?, ?)""",
        (str(initial_capital), str(initial_capital), now),
    )
    row = connection.execute(
        "SELECT initial_capital FROM paper_account WHERE account_id = 1"
    ).fetchone()
    if row is None or Decimal(row[0]) != initial_capital:
        raise RuntimeError(
            "Configured initial capital differs from the existing paper ledger; refusing to reset it."
        )


def _exchange_constraints(exchange_info: dict[str, Any], symbol: str) -> tuple[Decimal, Decimal]:
    entries = exchange_info.get("symbols", [])
    matching = next((item for item in entries if item.get("symbol") == symbol), None)
    if matching is None:
        raise RuntimeError(f"Testnet exchangeInfo did not return {symbol}.")
    step = Decimal("0.00000001")
    minimum = Decimal("5")
    for item in matching.get("filters", []):
        filter_type = item.get("filterType")
        if filter_type == "LOT_SIZE":
            step = Decimal(str(item["stepSize"]))
        elif filter_type in {"MIN_NOTIONAL", "NOTIONAL"}:
            minimum = Decimal(str(item.get("minNotional", minimum)))
    return step, minimum


async def _run_once() -> dict[str, Any]:
    settings = get_settings()
    if settings.TRADING_ENV is not TradingEnvironment.TESTNET:
        raise RuntimeError("Refusing to run: TRADING_ENV must be TESTNET.")
    if settings.TRADING_MODE is not TradingMode.PAPER:
        raise RuntimeError("Refusing to run: TRADING_MODE must be PAPER.")
    if settings.ALLOW_MAINNET or settings.LIVE_TRADING_ENABLED:
        raise RuntimeError("Refusing to run: mainnet/live flags must remain disabled.")
    if settings.INITIAL_CAPITAL_USD != Decimal("50.00"):
        raise RuntimeError("This controlled smoke run requires INITIAL_CAPITAL_USD=50.00.")
    if not settings.watchlist_symbols:
        raise RuntimeError("WATCHLIST is empty.")

    database = _database_path(settings.DATABASE_URL)
    connection = sqlite3.connect(database, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        _initialize_database(connection, settings.INITIAL_CAPITAL_USD)
        connection.commit()
        existing = connection.execute(
            "SELECT * FROM paper_orders WHERE run_key = ?", (SMOKE_RUN_KEY,)
        ).fetchone()
        if existing is not None:
            return {
                "status": "already_recorded",
                "environment": "TESTNET",
                "mode": "PAPER",
                "database": str(database),
                "order": dict(existing),
                "message": "The one-time startup smoke order already exists; no duplicate was created.",
            }

        client = BinanceSpotRESTClient(settings)
        try:
            await client.ping()
            server_time = await client.server_time()
            candidates: list[tuple[Decimal, str, Decimal, Decimal, Decimal]] = []
            rejected: list[str] = []
            for symbol in settings.watchlist_symbols:
                try:
                    ticker = await client.ticker_24h(symbol)
                    price = Decimal(str(ticker["lastPrice"]))
                    quote_volume = Decimal(str(ticker.get("quoteVolume", "0")))
                    step, minimum = _exchange_constraints(
                        await client.exchange_info(symbol), symbol
                    )
                    budget = settings.MAX_TRADE_ALLOCATION_USD
                    raw_quantity = budget / price
                    quantity = (raw_quantity / step).to_integral_value(
                        rounding=ROUND_DOWN
                    ) * step
                    notional = quantity * price
                    if quantity <= 0 or notional < minimum:
                        rejected.append(
                            f"{symbol}: rounded notional {notional} is below exchange minimum {minimum}"
                        )
                        continue
                    candidates.append((quote_volume, symbol, price, step, minimum))
                except (KeyError, ValueError, TypeError) as exc:
                    rejected.append(f"{symbol}: invalid market metadata ({exc})")

            if not candidates:
                raise RuntimeError(
                    "No watchlist symbol can satisfy the exchange minimum within the "
                    f"configured {settings.MAX_TRADE_ALLOCATION_USD} USDT smoke budget. "
                    + "; ".join(rejected)
                )

            _, symbol, price, step, minimum = max(candidates, key=lambda item: item[0])
            quantity = (
                (settings.MAX_TRADE_ALLOCATION_USD / price) / step
            ).to_integral_value(rounding=ROUND_DOWN) * step
            engine = ExecutionEngine()
            order = await engine.submit_paper_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                reference_price=price,
                quantity_step=step,
                min_notional=minimum,
            )
        finally:
            await client.close()

        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT cash_balance FROM paper_account WHERE account_id = 1"
        ).fetchone()
        cash_before = Decimal(account["cash_balance"])
        if order.net_quote_flow > cash_before:
            connection.rollback()
            raise RuntimeError("Paper order would exceed the available simulated cash.")
        cash_after = cash_before - order.net_quote_flow
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """INSERT INTO paper_orders (
                order_id, run_key, symbol, side, quantity, reference_price,
                fill_price, gross_quote, fee_quote, net_quote_flow, cash_after,
                status, purpose, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id, SMOKE_RUN_KEY, order.symbol, order.side.value,
                str(order.quantity), str(order.reference_price), str(order.fill_price),
                str(order.gross_quote), str(order.fee_quote), str(order.net_quote_flow),
                str(cash_after), order.status, "AUTOMATED_STARTUP_SMOKE_TEST", now,
            ),
        )
        connection.execute(
            "UPDATE paper_account SET cash_balance = ? WHERE account_id = 1",
            (str(cash_after),),
        )
        connection.commit()
        return {
            "status": "paper_order_recorded",
            "environment": "TESTNET",
            "mode": "PAPER",
            "testnet_server_time_ms": server_time,
            "database": str(database),
            "initial_capital_usdt": str(settings.INITIAL_CAPITAL_USD),
            "cash_before_usdt": str(cash_before),
            "cash_after_usdt": str(cash_after),
            "order": {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": str(order.quantity),
                "reference_price": str(order.reference_price),
                "simulated_fill_price": str(order.fill_price),
                "gross_quote": str(order.gross_quote),
                "estimated_fee_quote": str(order.fee_quote),
                "total_cash_outflow": str(order.net_quote_flow),
                "status": order.status,
                "purpose": "AUTOMATED_STARTUP_SMOKE_TEST",
            },
            "live_order_submission": "HARD_DISABLED",
        }
    finally:
        connection.close()


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Connect to Binance Spot Testnet and record one persistent PAPER smoke order."
    )
    parser.parse_args()
    try:
        result = await _run_once()
    except Exception as exc:
        LOGGER.exception("Autonomous PAPER smoke run failed")
        result = {"status": "failed", "error": str(exc), "live_order_submission": "HARD_DISABLED"}
        print(json.dumps(result, indent=2, default=str))
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))
