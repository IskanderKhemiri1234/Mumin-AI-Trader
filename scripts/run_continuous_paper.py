from __future__ import annotations

import argparse
import asyncio
import math
import os
import signal
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from mumin_trader.strategy.smart_sniper import SmartSniperStrategy
from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield, ShieldAssessment

from mumin_trader.exchanges.binance.public_market_data import (
    ALLOWED_SYMBOLS,
    PublicMarketData,
    simulated_fill_price,
)

QUOTE = "USDT"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")
INITIAL_CASH = Decimal(os.getenv("INITIAL_CAPITAL_USD", "70.00"))
MAX_TRADE = Decimal(os.getenv("MAX_TRADE_ALLOCATION_USD", "5.00"))
RESERVE_PERCENT = Decimal(os.getenv("CAPITAL_RESERVE_PERCENT", "20"))
MAX_CAPITAL = Decimal(os.getenv("MAX_CAPITAL_ALLOCATION_USD", str(INITIAL_CASH)))
MAX_TOTAL_EXPOSURE = MAX_CAPITAL * (Decimal("100") - RESERVE_PERCENT) / Decimal("100")
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "2"))
FEE_RATE = Decimal("0.001")
SLIPPAGE_BPS = Decimal("2")
STOP_LOSS_PERCENT = Decimal(os.getenv("SIMULATED_STOP_LOSS_PERCENT", "0.50"))
TAKE_PROFIT_PERCENT = Decimal(os.getenv("SIMULATED_TAKE_PROFIT_PERCENT", "1.00"))
MAX_DAILY_LOSS_PERCENT = Decimal(os.getenv("MAX_DAILY_LOSS_PERCENT", "2.00"))
MAX_DRAWDOWN_PERCENT = Decimal(os.getenv("MAX_DRAWDOWN_PERCENT", "5.00"))

# These are explicit product-level safety limits for the 70 USDT Paper account.
# An unexpected environment override must stop the engine rather than silently
# weaken the agreed risk policy.
if INITIAL_CASH != Decimal("70.00"):
    raise RuntimeError("Paper engine requires the configured 70.00 USDT starting capital.")
if STOP_LOSS_PERCENT != Decimal("0.50"):
    raise RuntimeError("Paper stop-loss is fixed at 0.50%.")
if TAKE_PROFIT_PERCENT != Decimal("1.00"):
    raise RuntimeError("Paper take-profit is fixed at 1.00%.")
if MAX_DAILY_LOSS_PERCENT != Decimal("2.00"):
    raise RuntimeError("Paper daily loss limit is fixed at 2.00%.")

REPORT_SECONDS = 3600
SYNTHETIC_SOURCE = "MOCK_SYNTHETIC"
PUBLIC_SOURCE = "BINANCE_PUBLIC_MARKET_DATA"
BASE_PRICES = {
    "BTCUSDT": Decimal("60000"),
    "ETHUSDT": Decimal("3000"),
    "BNBUSDT": Decimal("600"),
}


@dataclass(frozen=True)
class TickResult:
    tick: int
    price: Decimal
    signal: str
    action: str
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    daily_pnl: Decimal
    fees_paid: Decimal
    halted: bool


class ContinuousPaperBot:
    """Multi-symbol SQLite Paper engine. It never submits exchange orders."""

    def __init__(self, database: str, initial_cash: Decimal = INITIAL_CASH):
        self.database = database
        self.initial_cash = Decimal(initial_cash)
        self.strategy = SmartSniperStrategy()
        self.anti_loss_shield = AntiLossQuantumShield()
        if database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=20000")
        return db

    def _init_db(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS bot_account (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    initial_cash TEXT NOT NULL,
                    cash TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL DEFAULT '0',
                    fees_paid TEXT NOT NULL DEFAULT '0',
                    high_watermark TEXT NOT NULL,
                    daily_start_equity TEXT NOT NULL,
                    daily_realized_pnl TEXT NOT NULL DEFAULT '0',
                    trading_day TEXT NOT NULL,
                    tick_count INTEGER NOT NULL DEFAULT 0,
                    halted INTEGER NOT NULL DEFAULT 0,
                    halt_reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    entry_gross TEXT NOT NULL,
                    entry_fee TEXT NOT NULL,
                    opened_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_fills (
                    fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tick INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    reference_price TEXT NOT NULL,
                    fill_price TEXT NOT NULL,
                    gross_quote TEXT NOT NULL,
                    fee_quote TEXT NOT NULL,
                    net_pnl TEXT NOT NULL,
                    data_source TEXT NOT NULL DEFAULT 'MOCK_SYNTHETIC',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_equity (
                    tick INTEGER PRIMARY KEY,
                    price TEXT NOT NULL,
                    cash TEXT NOT NULL,
                    equity TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL,
                    daily_pnl TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_reports (
                    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    tick INTEGER NOT NULL,
                    cash TEXT NOT NULL,
                    equity TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL,
                    daily_pnl TEXT NOT NULL,
                    fees_paid TEXT NOT NULL,
                    trade_count INTEGER NOT NULL,
                    win_count INTEGER NOT NULL,
                    loss_count INTEGER NOT NULL,
                    halt_reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_market_ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tick INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    price TEXT NOT NULL,
                    bid TEXT NOT NULL,
                    ask TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    candle_close_time_ms INTEGER,
                    received_at_ms INTEGER NOT NULL,
                    UNIQUE(tick, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_market_ticks_symbol_source
                    ON bot_market_ticks(symbol, data_source, tick);
                CREATE TABLE IF NOT EXISTS bot_portfolio_snapshots (
                    tick INTEGER PRIMARY KEY,
                    cash TEXT NOT NULL,
                    equity TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL,
                    unrealized_pnl TEXT NOT NULL,
                    fees_paid TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            row = db.execute("SELECT initial_cash FROM bot_account WHERE id=1").fetchone()
            if row is None:
                db.execute(
                    """INSERT INTO bot_account
                    (id, initial_cash, cash, high_watermark, daily_start_equity,
                     trading_day, updated_at) VALUES (1, ?, ?, ?, ?, ?, ?)""",
                    (str(self.initial_cash), str(self.initial_cash), str(self.initial_cash),
                     str(self.initial_cash), today, datetime.now(timezone.utc).isoformat()),
                )
            elif Decimal(row["initial_cash"]) != self.initial_cash:
                raise RuntimeError(
                    "Configured initial capital differs from stored ledger; refusing reset."
                )

    @staticmethod
    def synthetic_price(tick: int) -> Decimal:
        return ContinuousPaperBot.synthetic_price_for("BTCUSDT", tick)

    @staticmethod
    def synthetic_price_for(symbol: str, tick: int) -> Decimal:
        if symbol not in BASE_PRICES:
            raise ValueError(f"Unsupported synthetic symbol: {symbol}")
        phase = {"BTCUSDT": 0.0, "ETHUSDT": 1.3, "BNBUSDT": 2.1}[symbol]
        wave = 0.0022 * math.sin(tick / 5.0 + phase)
        slow_wave = 0.0010 * math.sin(tick / 17.0 + phase)
        micro = 0.00035 * math.sin(tick / 1.7 + phase)
        return (BASE_PRICES[symbol] * Decimal(str(1.0 + wave + slow_wave + micro))).quantize(
            Decimal("0.00000001")
        )

    @staticmethod
    def _momentum(prices: list[Decimal]) -> Decimal:
        if len(prices) < 4 or prices[-4] <= 0:
            return Decimal("0")
        return (prices[-1] / prices[-4] - Decimal(1)) * Decimal("10000")

    @staticmethod
    def _position_value(position, prices: dict[str, Decimal]) -> Decimal:
        if position is None:
            return Decimal("0")
        price = prices.get(position["symbol"])
        return Decimal(position["quantity"]) * price if price is not None else Decimal("0")

    def _latest_prices(self, db, source: str) -> dict[str, Decimal]:
        prices: dict[str, Decimal] = {}
        for row in db.execute(
            """SELECT m.symbol, m.price FROM bot_market_ticks m
               JOIN (SELECT symbol, MAX(id) AS max_id FROM bot_market_ticks
                     GROUP BY symbol) latest ON latest.max_id=m.id"""
        ).fetchall():
            prices[row["symbol"]] = Decimal(row["price"])
        return prices

    def step(
        self,
        prices: dict[str, Decimal] | None = None,
        *,
        source: str = SYNTHETIC_SOURCE,
        quotes: dict[str, tuple[Decimal, Decimal]] | None = None,
        candle_close_times: dict[str, int] | None = None,
        received_at_ms: int | None = None,
        liquidity_metrics: dict[str, tuple[Decimal | None, Decimal | None]] | None = None,
    ) -> TickResult:
        if source not in {SYNTHETIC_SOURCE, PUBLIC_SOURCE}:
            raise ValueError("Unknown market data source.")
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        received = int(received_at_ms or now.timestamp() * 1000)
        if prices is None:
            with self._connect() as db:
                next_tick = int(db.execute(
                    "SELECT tick_count FROM bot_account WHERE id=1"
                ).fetchone()["tick_count"]) + 1
            prices = {symbol: self.synthetic_price_for(symbol, next_tick) for symbol in SYMBOLS}
        normalized = {symbol: Decimal(value) for symbol, value in prices.items()}
        if not normalized or any(
            symbol not in ALLOWED_SYMBOLS or not value.is_finite() or value <= 0
            for symbol, value in normalized.items()
        ):
            raise ValueError("Prices must contain positive finite values for allowlisted symbols.")
        quotes = quotes or {symbol: (price, price) for symbol, price in normalized.items()}
        candle_close_times = candle_close_times or {}
        liquidity_metrics = liquidity_metrics or {}

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
            tick = int(account["tick_count"]) + 1
            stored_sources = {
                row["data_source"] for row in db.execute(
                    """SELECT DISTINCT data_source FROM bot_fills
                       UNION SELECT DISTINCT data_source FROM bot_market_ticks"""
                ).fetchall()
            }
            if stored_sources and stored_sources != {source}:
                raise RuntimeError(
                    "SQLite ledger market source mismatch; use a separate database for each source."
                )
            cash = Decimal(account["cash"])
            realized = Decimal(account["realized_pnl"])
            fees_paid = Decimal(account["fees_paid"])
            high_water = Decimal(account["high_watermark"])
            day_start = Decimal(account["daily_start_equity"])
            daily_realized = Decimal(account["daily_realized_pnl"])
            halted = bool(account["halted"])
            halt_reason = str(account["halt_reason"])

            for symbol, price in normalized.items():
                bid, ask = quotes.get(symbol, (price, price))
                bid, ask = Decimal(bid), Decimal(ask)
                if bid <= 0 or ask < bid:
                    raise ValueError(f"Invalid bid/ask for {symbol}")
                db.execute(
                    """INSERT INTO bot_market_ticks
                    (tick,symbol,price,bid,ask,data_source,candle_close_time_ms,received_at_ms)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (tick, symbol, str(price), str(bid), str(ask), source,
                     candle_close_times.get(symbol), received),
                )

            latest_prices = self._latest_prices(db, source)
            latest_prices.update(normalized)
            if account["trading_day"] != now.date().isoformat():
                equity_before = cash + sum(
                    (Decimal(pos["quantity"]) * latest_prices.get(pos["symbol"], Decimal("0"))
                     for pos in db.execute("SELECT * FROM bot_positions").fetchall()),
                    Decimal("0"),
                )
                day_start = equity_before
                daily_realized = Decimal("0")
                if halt_reason == "MAX_DAILY_LOSS":
                    halted, halt_reason = False, ""
                db.execute(
                    """UPDATE bot_account SET daily_start_equity=?, daily_realized_pnl='0',
                       trading_day=? WHERE id=1""",
                    (str(day_start), now.date().isoformat()),
                )

            signals: list[str] = []
            actions: list[str] = []
            open_positions = {
                row["symbol"]: row for row in db.execute("SELECT * FROM bot_positions").fetchall()
            }

            # Fail closed before considering any entry if the marked portfolio has
            # already reached either account-level loss boundary.
            marked_equity = cash + sum(
                (Decimal(pos["quantity"]) * latest_prices.get(pos_symbol, Decimal("0"))
                 for pos_symbol, pos in open_positions.items()),
                Decimal("0"),
            )
            marked_daily_pnl = marked_equity - day_start
            if marked_daily_pnl <= -(day_start * MAX_DAILY_LOSS_PERCENT / Decimal("100")):
                halted, halt_reason = True, "MAX_DAILY_LOSS"
            if high_water - marked_equity >= high_water * MAX_DRAWDOWN_PERCENT / Decimal("100"):
                halted, halt_reason = True, "MAX_DRAWDOWN"

            # First manage existing positions for every symbol with a fresh quote.
            for symbol, price in normalized.items():
                if candle_close_times.get(symbol) is not None:
                    history = db.execute(
                        """SELECT price FROM bot_market_ticks
                           WHERE symbol=? AND data_source=? AND candle_close_time_ms IS NOT NULL
                           GROUP BY candle_close_time_ms
                           ORDER BY MAX(tick) DESC LIMIT 60""",
                        (symbol, source),
                    ).fetchall()
                else:
                    history = db.execute(
                        """SELECT price FROM bot_market_ticks
                           WHERE symbol=? AND data_source=? ORDER BY tick DESC LIMIT 60""",
                        (symbol, source),
                    ).fetchall()
                closes = [Decimal(row["price"]) for row in reversed(history)]
                bid, ask = quotes.get(symbol, (price, price))
                midpoint = (Decimal(bid) + Decimal(ask)) / Decimal("2")
                spread_bps = (
                    (Decimal(ask) - Decimal(bid)) / midpoint * Decimal("10000")
                    if midpoint > 0 else Decimal("10000")
                )
                assessment = self.strategy.assess(closes, spread_bps=spread_bps)
                signal_name = assessment.signal
                if source == PUBLIC_SOURCE:
                    volume_ratio, trade_ratio = liquidity_metrics.get(symbol, (None, None))
                    previous_close = closes[-2] if len(closes) >= 2 else price
                    price_change_bps = (
                        (price / previous_close - Decimal("1")) * Decimal("10000")
                        if previous_close > 0 else Decimal("0")
                    )
                    candle_age_ms = max(
                        0, received - candle_close_times.get(symbol, received)
                    )
                    shield_decision = self.anti_loss_shield.assess(
                        spread_bps=spread_bps,
                        quote_volume_ratio=volume_ratio,
                        trade_count_ratio=trade_ratio,
                        price_change_bps=price_change_bps,
                        candle_age_ms=candle_age_ms,
                    )
                else:
                    shield_decision = ShieldAssessment(
                        "BYPASSED_SYNTHETIC", False, Decimal("0"),
                        ("Synthetic test feed has no real liquidity observations.",),
                    )
                if shield_decision.block_entries and signal_name == "BUY":
                    signal_name = "HOLD"
                signals.append(f"{symbol}:{signal_name}")
                position = open_positions.get(symbol)
                action = "NONE"
                bid, ask = quotes.get(symbol, (price, price))
                if position is not None:
                    # Trigger exits on estimated NET return after both entry and
                    # exit fees plus conservative executable-side slippage.
                    fill = simulated_fill_price("SELL", bid, ask, SLIPPAGE_BPS)
                    quantity = Decimal(position["quantity"])
                    gross = quantity * fill
                    fee = (gross * FEE_RATE).quantize(Decimal("0.00000001"))
                    entry_cost = Decimal(position["entry_gross"]) + Decimal(position["entry_fee"])
                    projected_net = gross - fee - entry_cost
                    net_return_pct = (
                        projected_net / entry_cost * Decimal("100")
                        if entry_cost > 0 else Decimal("-100")
                    )
                    hard_exit = (
                        net_return_pct <= -STOP_LOSS_PERCENT
                        or net_return_pct >= TAKE_PROFIT_PERCENT
                        or signal_name == "SELL"
                    )
                    exit_fraction = Decimal("1") if hard_exit else shield_decision.exit_fraction
                    quantity_to_sell = (
                        quantity * exit_fraction
                    ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
                    if exit_fraction > 0 and quantity_to_sell > 0:
                        actual_fraction = quantity_to_sell / quantity
                        sold_gross = quantity_to_sell * fill
                        sold_fee = (sold_gross * FEE_RATE).quantize(Decimal("0.00000001"))
                        sold_entry_gross = Decimal(position["entry_gross"]) * actual_fraction
                        sold_entry_fee = Decimal(position["entry_fee"]) * actual_fraction
                        net_trade = sold_gross - sold_fee - sold_entry_gross - sold_entry_fee
                        cash += sold_gross - sold_fee
                        realized += net_trade
                        daily_realized += net_trade
                        fees_paid += sold_fee
                        remaining_quantity = quantity - quantity_to_sell
                        db.execute(
                            """INSERT INTO bot_fills
                            (tick,symbol,side,quantity,reference_price,fill_price,gross_quote,
                             fee_quote,net_pnl,data_source,created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (tick, symbol, "SELL", str(quantity_to_sell), str(price), str(fill),
                             str(sold_gross), str(sold_fee), str(net_trade), source, timestamp),
                        )
                        if remaining_quantity > 0:
                            remaining_entry_gross = Decimal(position["entry_gross"]) - sold_entry_gross
                            remaining_entry_fee = Decimal(position["entry_fee"]) - sold_entry_fee
                            db.execute(
                                """UPDATE bot_positions SET quantity=?, entry_gross=?, entry_fee=?
                                   WHERE symbol=?""",
                                (str(remaining_quantity), str(remaining_entry_gross),
                                 str(remaining_entry_fee), symbol),
                            )
                            open_positions[symbol] = {
                                **dict(position),
                                "quantity": str(remaining_quantity),
                                "entry_gross": str(remaining_entry_gross),
                                "entry_fee": str(remaining_entry_fee),
                            }
                            action = f"{symbol}:DE_RISK qty={quantity_to_sell}"
                        else:
                            db.execute("DELETE FROM bot_positions WHERE symbol=?", (symbol,))
                            action = f"{symbol}:CLOSE net={net_trade:.8f}"
                            open_positions.pop(symbol, None)
                        # Re-evaluate account-wide daily loss immediately after a
                        # realized exit, before another symbol can open a position.
                        marked_equity = cash + sum(
                            (Decimal(pos["quantity"]) * latest_prices.get(pos_symbol, Decimal("0"))
                             for pos_symbol, pos in open_positions.items()),
                            Decimal("0"),
                        )
                        marked_daily_pnl = marked_equity - day_start
                        if marked_daily_pnl <= -(day_start * MAX_DAILY_LOSS_PERCENT / Decimal("100")):
                            halted, halt_reason = True, "MAX_DAILY_LOSS"
                        if high_water - marked_equity >= high_water * MAX_DRAWDOWN_PERCENT / Decimal("100"):
                            halted, halt_reason = True, "MAX_DRAWDOWN"

                # Entries are considered only after exits, and share one cash/risk budget.
                if (
                    action == "NONE" and position is None and signal_name == "BUY"
                    and not halted and len(open_positions) < MAX_OPEN_POSITIONS
                ):
                    reserve_cash = self.initial_cash * RESERVE_PERCENT / Decimal("100")
                    exposure = sum(
                        (Decimal(pos["quantity"]) * latest_prices.get(pos_symbol, Decimal("0"))
                         for pos_symbol, pos in open_positions.items()),
                        Decimal("0"),
                    )
                    allocation = min(MAX_TRADE, max(Decimal("0"), cash - reserve_cash))
                    if exposure + allocation <= MAX_TOTAL_EXPOSURE and allocation > 0:
                        fill = simulated_fill_price("BUY", bid, ask, SLIPPAGE_BPS)
                        quantity = (allocation / (fill * (Decimal(1) + FEE_RATE))).quantize(
                            Decimal("0.00000001"), rounding=ROUND_DOWN
                        )
                        gross = quantity * fill
                        fee = (gross * FEE_RATE).quantize(Decimal("0.00000001"))
                        total = gross + fee
                        if quantity > 0 and gross >= Decimal("1.00") and total <= cash - reserve_cash:
                            cash -= total
                            fees_paid += fee
                            db.execute(
                                """INSERT INTO bot_positions
                                (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
                                VALUES (?,?,?,?,?,?)""",
                                (symbol, str(quantity), str(fill), str(gross), str(fee), timestamp),
                            )
                            db.execute(
                                """INSERT INTO bot_fills
                                (tick,symbol,side,quantity,reference_price,fill_price,gross_quote,
                                 fee_quote,net_pnl,data_source,created_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                                (tick, symbol, "BUY", str(quantity), str(price), str(fill),
                                 str(gross), str(fee), "0", source, timestamp),
                            )
                            action = f"{symbol}:OPEN qty={quantity}"
                            open_positions[symbol] = {
                                "symbol": symbol, "quantity": str(quantity)
                            }
                if action != "NONE":
                    actions.append(action)

            open_rows = db.execute("SELECT * FROM bot_positions").fetchall()
            equity = cash + sum(
                (Decimal(pos["quantity"]) * latest_prices.get(pos["symbol"], Decimal("0"))
                 for pos in open_rows),
                Decimal("0"),
            )
            unrealized = equity - cash - sum(
                (Decimal(pos["entry_gross"]) + Decimal(pos["entry_fee"])
                 for pos in open_rows),
                Decimal("0"),
            )
            high_water = max(high_water, equity)
            daily_pnl = equity - day_start
            if not halted and daily_pnl <= -(day_start * MAX_DAILY_LOSS_PERCENT / Decimal("100")):
                halted, halt_reason = True, "MAX_DAILY_LOSS"
            if not halted and high_water - equity >= high_water * MAX_DRAWDOWN_PERCENT / Decimal("100"):
                halted, halt_reason = True, "MAX_DRAWDOWN"

            btc_price = normalized.get("BTCUSDT", latest_prices.get("BTCUSDT", Decimal("0")))
            db.execute(
                """INSERT INTO bot_equity
                (tick,price,cash,equity,realized_pnl,daily_pnl,signal,action,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (tick, str(btc_price), str(cash), str(equity), str(realized),
                 str(daily_pnl), ",".join(signals), ",".join(actions) or "NONE", timestamp),
            )
            db.execute(
                """INSERT INTO bot_portfolio_snapshots
                (tick,cash,equity,realized_pnl,unrealized_pnl,fees_paid,data_source,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (tick, str(cash), str(equity), str(realized), str(unrealized),
                 str(fees_paid), source, timestamp),
            )
            db.execute(
                """UPDATE bot_account SET cash=?, realized_pnl=?, fees_paid=?,
                high_watermark=?, daily_start_equity=?, daily_realized_pnl=?,
                tick_count=?, halted=?, halt_reason=?, updated_at=? WHERE id=1""",
                (str(cash), str(realized), str(fees_paid), str(high_water),
                 str(day_start), str(daily_realized), tick, int(halted), halt_reason, timestamp),
            )
            db.commit()
            primary_price = normalized.get("BTCUSDT", next(iter(normalized.values())))
            return TickResult(
                tick, primary_price, ",".join(signals), ",".join(actions) or "NONE",
                cash, equity, realized, daily_pnl, fees_paid, halted,
            )

    def report(self) -> dict:
        with self._connect() as db:
            account = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
            # The most recent quote per symbol may have different sources; use the latest row.
            latest_prices = {}
            for row in db.execute(
                """SELECT m.symbol,m.price FROM bot_market_ticks m
                   JOIN (SELECT symbol,MAX(id) AS max_id FROM bot_market_ticks GROUP BY symbol) x
                   ON x.max_id=m.id"""
            ).fetchall():
                latest_prices[row["symbol"]] = Decimal(row["price"])
            positions = db.execute("SELECT * FROM bot_positions").fetchall()
            cash = Decimal(account["cash"])
            equity = cash + sum(
                (Decimal(pos["quantity"]) * latest_prices.get(pos["symbol"], Decimal("0"))
                 for pos in positions),
                Decimal("0"),
            )
            daily_pnl = equity - Decimal(account["daily_start_equity"])
            counts = db.execute(
                """SELECT COUNT(*) AS total,
                SUM(CASE WHEN CAST(net_pnl AS REAL)>0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN CAST(net_pnl AS REAL)<0 THEN 1 ELSE 0 END) AS losses
                FROM bot_fills WHERE side='SELL'"""
            ).fetchone()
            symbols = {
                row["symbol"]: {
                    "quantity": Decimal(row["quantity"]),
                    "entry_price": Decimal(row["entry_price"]),
                    "mark_price": latest_prices.get(row["symbol"]),
                }
                for row in positions
            }
            return {
                "tick": account["tick_count"], "cash": cash, "equity": equity,
                "realized_pnl": Decimal(account["realized_pnl"]),
                "daily_pnl": daily_pnl, "fees": Decimal(account["fees_paid"]),
                "trades": counts["total"] or 0, "wins": counts["wins"] or 0,
                "losses": counts["losses"] or 0, "positions": symbols,
                "halted": bool(account["halted"]), "halt_reason": account["halt_reason"],
            }

    def save_hourly_report(self) -> dict:
        report = self.report()
        with self._connect() as db:
            db.execute(
                """INSERT INTO bot_reports
                (created_at,tick,cash,equity,realized_pnl,daily_pnl,fees_paid,
                 trade_count,win_count,loss_count,halt_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), report["tick"],
                 str(report["cash"]), str(report["equity"]), str(report["realized_pnl"]),
                 str(report["daily_pnl"]), str(report["fees"]), report["trades"],
                 report["wins"], report["losses"], report["halt_reason"]),
            )
        return report


async def run_bot(
    database: str,
    interval_seconds: float,
    cycles: int | None,
    data_source: str = SYNTHETIC_SOURCE,
) -> None:
    bot = ContinuousPaperBot(database)
    feed = PublicMarketData() if data_source == PUBLIC_SOURCE else None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    print("MUMIN AI TRADER | MULTI-SYMBOL PAPER ONLY", flush=True)
    print(
        f"Symbols: {','.join(SYMBOLS)} | Market data: {data_source} | "
        "API keys: none | Live orders: disabled",
        flush=True,
    )
    print(f"SQLite ledger: {database} | cycle interval: {interval_seconds}s", flush=True)
    last_report = loop.time()
    completed = 0
    try:
        while not stop.is_set() and (cycles is None or completed < cycles):
            completed += 1
            if feed is None:
                result = bot.step()
            else:
                snapshots, errors = await feed.snapshots(SYMBOLS)
                for symbol, error in errors.items():
                    print(f"MARKET DATA UNAVAILABLE {symbol}: {error}", flush=True)
                if not snapshots:
                    print("No fresh public quotes; skipping this cycle without synthetic fallback.", flush=True)
                    if loop.time() - last_report >= REPORT_SECONDS:
                        report = bot.save_hourly_report()
                        print(
                            f"HOURLY STATUS | no fresh market data | equity={report['equity']:.8f} "
                            f"{QUOTE} | halted={report['halted']} {report['halt_reason']}",
                            flush=True,
                        )
                        last_report = loop.time()
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                    except asyncio.TimeoutError:
                        pass
                    continue
                prices = {symbol: item.last_closed_price for symbol, item in snapshots.items()}
                quotes = {symbol: (item.bid, item.ask) for symbol, item in snapshots.items()}
                candle_times = {symbol: item.candle_close_time_ms for symbol, item in snapshots.items()}
                liquidity_metrics = {
                    symbol: (item.quote_volume_ratio, item.trade_count_ratio)
                    for symbol, item in snapshots.items()
                }
                received = min(item.received_at_ms for item in snapshots.values())
                result = bot.step(
                    prices, source=PUBLIC_SOURCE, quotes=quotes,
                    candle_close_times=candle_times, received_at_ms=received,
                    liquidity_metrics=liquidity_metrics,
                )

            print(
                f"[tick {result.tick}] pricesource={data_source} price={result.price} {QUOTE} "
                f"signals={result.signal} actions={result.action} cash={result.cash:.8f} "
                f"equity={result.equity:.8f} realized_net={result.realized_pnl:.8f} "
                f"daily_net={result.daily_pnl:.8f}",
                flush=True,
            )
            if result.halted:
                print(f"RISK HALT: {bot.report()['halt_reason']}; new entries blocked.", flush=True)
            if loop.time() - last_report >= REPORT_SECONDS or (cycles is not None and completed == cycles):
                report = bot.save_hourly_report()
                print(
                    "HOURLY PERFORMANCE | "
                    f"equity={report['equity']:.8f} {QUOTE} | cash={report['cash']:.8f} | "
                    f"realized_net={report['realized_pnl']:.8f} | daily_net={report['daily_pnl']:.8f} | "
                    f"fees={report['fees']:.8f} | closed_trades={report['trades']} "
                    f"(wins={report['wins']}, losses={report['losses']}) | "
                    f"positions={list(report['positions'])} | halted={report['halted']} "
                    f"{report['halt_reason']}",
                    flush=True,
                )
                last_report = loop.time()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        if feed is not None:
            await feed.close()
    print("Continuous Paper loop stopped cleanly.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous multi-symbol Paper Trading; no live orders.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--cycles", type=int, default=None, help="Finite smoke run; omit for continuous operation.")
    parser.add_argument(
        "--data-source", choices=(SYNTHETIC_SOURCE, PUBLIC_SOURCE),
        default=os.getenv("MUMIN_MARKET_DATA_SOURCE", SYNTHETIC_SOURCE),
    )
    args = parser.parse_args()
    if args.interval_seconds <= 0 or (args.cycles is not None and args.cycles <= 0):
        parser.error("interval-seconds and cycles must be positive.")
    database = args.db or (
        "data/mumin_binance_public_paper.db"
        if args.data_source == PUBLIC_SOURCE
        else "data/mumin_continuous_paper.db"
    )
    asyncio.run(run_bot(database, args.interval_seconds, args.cycles, args.data_source))


if __name__ == "__main__":
    main()
