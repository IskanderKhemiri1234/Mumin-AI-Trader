from __future__ import annotations

import argparse
import asyncio
import math
import signal
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

QUOTE = "USDT"
SYMBOL = "BTCUSDT"
INITIAL_CASH = Decimal("50.00")
MAX_TRADE = Decimal("5.00")
RESERVE_PERCENT = Decimal("20")
FEE_RATE = Decimal("0.001")
SLIPPAGE_BPS = Decimal("2")
STOP_LOSS_PERCENT = Decimal("0.50")
TAKE_PROFIT_PERCENT = Decimal("1.00")
MAX_DAILY_LOSS_PERCENT = Decimal("2.00")
MAX_DRAWDOWN_PERCENT = Decimal("5.00")
REPORT_SECONDS = 3600


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
    """Offline, SQLite-persistent synthetic-market paper bot. No network or API keys."""

    def __init__(self, database: str, initial_cash: Decimal = INITIAL_CASH):
        self.database = database
        self.initial_cash = initial_cash
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
                raise RuntimeError("Configured initial capital differs from stored ledger; refusing reset.")

    @staticmethod
    def synthetic_price(tick: int) -> Decimal:
        # Repeatable, explicitly artificial market path. It is not exchange data.
        wave = 0.0022 * math.sin(tick / 5.0)
        slow_wave = 0.0010 * math.sin(tick / 17.0)
        micro = 0.00035 * math.sin(tick / 1.7)
        return (Decimal("60000") * Decimal(str(1.0 + wave + slow_wave + micro))).quantize(
            Decimal("0.01")
        )

    @staticmethod
    def _momentum(prices: list[Decimal]) -> Decimal:
        if len(prices) < 4:
            return Decimal("0")
        return (prices[-1] / prices[-4] - Decimal(1)) * Decimal("10000")

    def _mark_equity(self, cash: Decimal, position, price: Decimal) -> Decimal:
        if position is None:
            return cash
        return cash + Decimal(position["quantity"]) * price

    def step(self) -> TickResult:
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        timestamp = now.isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            account = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
            tick = int(account["tick_count"]) + 1
            price = self.synthetic_price(tick)
            cash = Decimal(account["cash"])
            realized = Decimal(account["realized_pnl"])
            fees_paid = Decimal(account["fees_paid"])
            high_water = Decimal(account["high_watermark"])
            day_start = Decimal(account["daily_start_equity"])
            daily_realized = Decimal(account["daily_realized_pnl"])
            halted = bool(account["halted"])
            halt_reason = str(account["halt_reason"])

            position = db.execute(
                "SELECT * FROM bot_positions WHERE symbol=?", (SYMBOL,)
            ).fetchone()
            if account["trading_day"] != today:
                current_equity = self._mark_equity(cash, position, price)
                day_start = current_equity
                daily_realized = Decimal("0")
                db.execute(
                    "UPDATE bot_account SET daily_start_equity=?, daily_realized_pnl='0', trading_day=? WHERE id=1",
                    (str(day_start), today),
                )

            previous = db.execute(
                "SELECT price FROM bot_equity ORDER BY tick DESC LIMIT 3"
            ).fetchall()
            prices = [Decimal(row["price"]) for row in reversed(previous)] + [price]
            momentum = self._momentum(prices)
            signal = "BUY" if momentum >= Decimal("8") else (
                "SELL" if momentum <= Decimal("-8") else "HOLD"
            )
            action = "NONE"

            # The open position is managed before considering a new entry.
            if position is not None:
                quantity = Decimal(position["quantity"])
                entry = Decimal(position["entry_price"])
                change_pct = (price / entry - Decimal(1)) * Decimal("100")
                should_exit = (
                    change_pct <= -STOP_LOSS_PERCENT
                    or change_pct >= TAKE_PROFIT_PERCENT
                    or signal == "SELL"
                )
                if should_exit:
                    fill = price * (Decimal(1) - SLIPPAGE_BPS / Decimal("10000"))
                    gross = quantity * fill
                    fee = (gross * FEE_RATE).quantize(Decimal("0.00000001"))
                    entry_gross = Decimal(position["entry_gross"])
                    entry_fee = Decimal(position["entry_fee"])
                    net_trade = gross - entry_gross - entry_fee - fee
                    cash += gross - fee
                    realized += net_trade
                    daily_realized += net_trade
                    fees_paid += fee
                    db.execute("DELETE FROM bot_positions WHERE symbol=?", (SYMBOL,))
                    db.execute(
                        """INSERT INTO bot_fills
                        (tick,symbol,side,quantity,reference_price,fill_price,gross_quote,
                         fee_quote,net_pnl,data_source,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,'MOCK_SYNTHETIC',?)""",
                        (tick, SYMBOL, "SELL", str(quantity), str(price), str(fill),
                         str(gross), str(fee), str(net_trade), timestamp),
                    )
                    action = f"CLOSE net={net_trade:.8f} {QUOTE}"

            # Entry is allowed only if all account-level safety gates remain open.
            if action == "NONE" and position is None and signal == "BUY" and not halted:
                reserve_cash = self.initial_cash * RESERVE_PERCENT / Decimal("100")
                deployable = max(Decimal("0"), cash - reserve_cash)
                risk_budget = self.initial_cash * Decimal("0.005")
                risk_sized_notional = risk_budget / (STOP_LOSS_PERCENT / Decimal("100"))
                allocation = min(MAX_TRADE, deployable, risk_sized_notional)
                quantity = (allocation / (price * Decimal("1.0002") * (Decimal(1) + FEE_RATE))).quantize(
                    Decimal("0.00000001"), rounding=ROUND_DOWN
                )
                fill = price * (Decimal(1) + SLIPPAGE_BPS / Decimal("10000"))
                gross = quantity * fill
                fee = (gross * FEE_RATE).quantize(Decimal("0.00000001"))
                total = gross + fee
                if quantity > 0 and gross >= Decimal("5") and total <= cash - reserve_cash:
                    cash -= total
                    fees_paid += fee
                    db.execute(
                        """INSERT INTO bot_positions
                        (symbol,quantity,entry_price,entry_gross,entry_fee,opened_at)
                        VALUES (?,?,?,?,?,?)""",
                        (SYMBOL, str(quantity), str(fill), str(gross), str(fee), timestamp),
                    )
                    db.execute(
                        """INSERT INTO bot_fills
                        (tick,symbol,side,quantity,reference_price,fill_price,gross_quote,
                         fee_quote,net_pnl,data_source,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,'MOCK_SYNTHETIC',?)""",
                        (tick, SYMBOL, "BUY", str(quantity), str(price), str(fill),
                         str(gross), str(fee), "0", timestamp),
                    )
                    action = f"OPEN qty={quantity}"

            position = db.execute(
                "SELECT * FROM bot_positions WHERE symbol=?", (SYMBOL,)
            ).fetchone()
            equity = self._mark_equity(cash, position, price)
            high_water = max(high_water, equity)
            daily_pnl = equity - day_start
            daily_loss_limit = day_start * MAX_DAILY_LOSS_PERCENT / Decimal("100")
            drawdown_limit = high_water * MAX_DRAWDOWN_PERCENT / Decimal("100")
            if not halted and daily_pnl <= -daily_loss_limit:
                halted, halt_reason = True, "MAX_DAILY_LOSS"
            if not halted and high_water - equity >= drawdown_limit:
                halted, halt_reason = True, "MAX_DRAWDOWN"

            db.execute(
                """INSERT INTO bot_equity
                (tick,price,cash,equity,realized_pnl,daily_pnl,signal,action,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (tick, str(price), str(cash), str(equity), str(realized),
                 str(daily_pnl), signal, action, timestamp),
            )
            db.execute(
                """UPDATE bot_account SET cash=?, realized_pnl=?, fees_paid=?,
                high_watermark=?, daily_start_equity=?, daily_realized_pnl=?,
                tick_count=?, halted=?, halt_reason=?, updated_at=? WHERE id=1""",
                (str(cash), str(realized), str(fees_paid), str(high_water),
                 str(day_start), str(daily_realized), tick, int(halted), halt_reason, timestamp),
            )
            db.commit()
            return TickResult(tick, price, signal, action, cash, equity, realized,
                              daily_pnl, fees_paid, halted)

    def report(self) -> dict:
        with self._connect() as db:
            account = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
            position = db.execute("SELECT * FROM bot_positions LIMIT 1").fetchone()
            latest = db.execute("SELECT price FROM bot_equity ORDER BY tick DESC LIMIT 1").fetchone()
            price = Decimal(latest["price"]) if latest else Decimal("60000")
            cash = Decimal(account["cash"])
            equity = self._mark_equity(cash, position, price)
            daily_pnl = equity - Decimal(account["daily_start_equity"])
            counts = db.execute(
                """SELECT COUNT(*) AS total,
                SUM(CASE WHEN CAST(net_pnl AS REAL)>0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN CAST(net_pnl AS REAL)<0 THEN 1 ELSE 0 END) AS losses
                FROM bot_fills WHERE side='SELL'"""
            ).fetchone()
            return {
                "tick": account["tick_count"], "cash": cash, "equity": equity,
                "realized_pnl": Decimal(account["realized_pnl"]),
                "daily_pnl": daily_pnl, "fees": Decimal(account["fees_paid"]),
                "trades": counts["total"] or 0, "wins": counts["wins"] or 0,
                "losses": counts["losses"] or 0,
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


async def run_bot(database: str, interval_seconds: float, cycles: int | None) -> None:
    bot = ContinuousPaperBot(database)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    print("MUMIN AI TRADER | CONTINUOUS OFFLINE MOCK PAPER", flush=True)
    print("Market data: SYNTHETIC ONLY | API keys: none | Network: disabled | Live orders: disabled", flush=True)
    print(f"SQLite ledger: {database} | cycle interval: {interval_seconds}s", flush=True)
    last_report = loop.time()
    completed = 0
    while not stop.is_set() and (cycles is None or completed < cycles):
        result = bot.step()
        print(
            f"[tick {result.tick}] price={result.price} {QUOTE} signal={result.signal} "
            f"action={result.action} cash={result.cash:.8f} equity={result.equity:.8f} "
            f"realized={result.realized_pnl:.8f} daily={result.daily_pnl:.8f}",
            flush=True,
        )
        completed += 1
        if result.halted:
            print(f"RISK HALT: {result.halted}. No new positions will be opened.", flush=True)
        if loop.time() - last_report >= REPORT_SECONDS or (cycles is not None and completed == cycles):
            report = bot.save_hourly_report()
            print(
                "HOURLY PERFORMANCE | "
                f"equity={report['equity']:.8f} {QUOTE} | cash={report['cash']:.8f} | "
                f"realized_net={report['realized_pnl']:.8f} | daily_net={report['daily_pnl']:.8f} | "
                f"fees={report['fees']:.8f} | closed_trades={report['trades']} "
                f"(wins={report['wins']}, losses={report['losses']}) | "
                f"halted={report['halted']} {report['halt_reason']}",
                flush=True,
            )
            last_report = loop.time()
        if result.halted:
            # Keep reporting and monitoring, but do not restart trading automatically.
            await asyncio.sleep(interval_seconds)
        else:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
    print("Continuous Paper loop stopped cleanly.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="24/7 offline synthetic-market Paper Trading loop.")
    parser.add_argument("--db", default="data/mumin_continuous_paper.db")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--cycles", type=int, default=None, help="Finite smoke run; omit for continuous operation.")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or (args.cycles is not None and args.cycles <= 0):
        parser.error("interval-seconds and cycles must be positive.")
    asyncio.run(run_bot(args.db, args.interval_seconds, args.cycles))


if __name__ == "__main__":
    main()
