from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from mumin_trader.execution.execution_engine import OrderSide, PaperOrder


@dataclass(frozen=True, slots=True)
class PaperPortfolio:
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    fees_paid: Decimal


class PaperLedger:
    """Durable SQLite ledger for simulated fills only; never connects to an exchange."""

    def __init__(self, database_path: str | Path, initial_cash: Decimal = Decimal("50.00")):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.initial_cash = Decimal(initial_cash)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_portfolio (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_cash TEXT NOT NULL,
                    cash TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL DEFAULT '0',
                    fees_paid TEXT NOT NULL DEFAULT '0',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    reference_price TEXT NOT NULL,
                    fill_price TEXT NOT NULL,
                    gross_quote TEXT NOT NULL,
                    fee_quote TEXT NOT NULL,
                    net_quote_flow TEXT NOT NULL,
                    fee_rate TEXT NOT NULL,
                    fee_asset TEXT NOT NULL,
                    fee_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS paper_trades (
                    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    exit_price TEXT NOT NULL,
                    entry_fee TEXT NOT NULL,
                    exit_fee TEXT NOT NULL,
                    gross_pnl TEXT NOT NULL,
                    net_pnl TEXT NOT NULL,
                    data_source TEXT NOT NULL DEFAULT 'MOCK_SYNTHETIC',
                    closed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            db.execute(
                """INSERT OR IGNORE INTO paper_portfolio
                   (id, initial_cash, cash, realized_pnl, fees_paid)
                   VALUES (1, ?, ?, '0', '0')""",
                (str(self.initial_cash), str(self.initial_cash)),
            )

    def record_fill(self, order: PaperOrder) -> None:
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM paper_orders WHERE order_id = ?", (order.order_id,)
            ).fetchone():
                return
            portfolio = db.execute(
                "SELECT cash, fees_paid FROM paper_portfolio WHERE id = 1"
            ).fetchone()
            cash = Decimal(portfolio["cash"])
            new_cash = cash - order.net_quote_flow if order.side is OrderSide.BUY else cash + order.gross_quote - order.fee_quote
            if new_cash < Decimal("-0.00000001"):
                raise ValueError("Paper portfolio cash would become negative.")
            fees = Decimal(portfolio["fees_paid"]) + order.fee_quote
            db.execute(
                """INSERT INTO paper_orders
                   (order_id, symbol, side, quantity, reference_price, fill_price,
                    gross_quote, fee_quote, net_quote_flow, fee_rate, fee_asset,
                    fee_source, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.order_id, order.symbol, order.side.value, str(order.quantity),
                    str(order.reference_price), str(order.fill_price), str(order.gross_quote),
                    str(order.fee_quote), str(order.net_quote_flow), str(order.fee_rate),
                    order.fee_asset, order.fee_source, order.status,
                ),
            )
            db.execute(
                """UPDATE paper_portfolio SET cash = ?, fees_paid = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (str(new_cash), str(fees)),
            )

    def record_closed_trade(
        self, *, symbol: str, quantity: Decimal, entry_price: Decimal,
        exit_price: Decimal, entry_fee: Decimal, exit_fee: Decimal,
        data_source: str = "MOCK_SYNTHETIC",
    ) -> Decimal:
        gross = (Decimal(exit_price) - Decimal(entry_price)) * Decimal(quantity)
        net = gross - Decimal(entry_fee) - Decimal(exit_fee)
        with self._connect() as db:
            db.execute(
                """INSERT INTO paper_trades
                   (symbol, quantity, entry_price, exit_price, entry_fee, exit_fee,
                    gross_pnl, net_pnl, data_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(), str(quantity), str(entry_price), str(exit_price),
                    str(entry_fee), str(exit_fee), str(gross), str(net), data_source,
                ),
            )
            current = db.execute(
                "SELECT realized_pnl FROM paper_portfolio WHERE id = 1"
            ).fetchone()
            updated_realized = Decimal(current["realized_pnl"]) + net
            db.execute(
                """UPDATE paper_portfolio SET realized_pnl = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (str(updated_realized),),
            )
        return net

    def portfolio(self) -> PaperPortfolio:
        with self._connect() as db:
            row = db.execute(
                "SELECT cash, initial_cash, realized_pnl, fees_paid FROM paper_portfolio WHERE id = 1"
            ).fetchone()
        return PaperPortfolio(
            cash=Decimal(row["cash"]),
            equity=Decimal(row["cash"]),
            realized_pnl=Decimal(row["realized_pnl"]),
            fees_paid=Decimal(row["fees_paid"]),
        )

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            return {
                "orders": db.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0],
                "trades": db.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0],
            }

    def latest_trade(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM paper_trades ORDER BY trade_id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
