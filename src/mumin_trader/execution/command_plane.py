from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger

ZERO = Decimal("0")
FEE_RATE = Decimal("0.001")
SLIPPAGE = Decimal("0.0002")
MAX_NOTIONAL = Decimal("5.00")
RESERVE = Decimal("14.00")
MAX_POSITIONS = 2
ALLOWED_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
}


class CommandRejected(ValueError):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise CommandRejected(f"Invalid {field}.", status_code=422) from exc
    if not result.is_finite():
        raise CommandRejected(f"{field} must be finite.", status_code=422)
    return result


def _connect(path: str | Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS bot_commands (
            command_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            payload_hash TEXT NOT NULL,
            command_type TEXT NOT NULL,
            status TEXT NOT NULL,
            actor TEXT NOT NULL,
            symbol TEXT,
            quantity TEXT,
            quote_amount TEXT,
            result_json TEXT,
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bot_command_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(command_id) REFERENCES bot_commands(command_id)
        );
        CREATE INDEX IF NOT EXISTS idx_bot_command_events_command
            ON bot_command_events(command_id, event_id);
        """
    )


def _event(db: sqlite3.Connection, command_id: str, event_type: str,
           status: str, detail: str) -> None:
    db.execute(
        """INSERT INTO bot_command_events
           (command_id,event_type,status,detail,created_at) VALUES (?,?,?,?,?)""",
        (command_id, event_type, status, detail[:500], _now()),
    )


def _fifo_lots(db: sqlite3.Connection, symbol: str) -> list[dict[str, Decimal]]:
    rows = db.execute(
        """SELECT side,quantity,fill_price,gross_quote,fee_quote
           FROM bot_fills WHERE symbol=? ORDER BY fill_id ASC""",
        (symbol,),
    ).fetchall()
    lots: list[dict[str, Decimal]] = []
    for row in rows:
        qty = Decimal(row["quantity"])
        gross = Decimal(row["gross_quote"])
        fee = Decimal(row["fee_quote"])
        if row["side"] == "BUY":
            lots.append({
                "qty": qty,
                "gross_per_unit": gross / qty,
                "fee_per_unit": fee / qty,
            })
        else:
            remaining = qty
            while remaining > ZERO and lots:
                lot = lots[0]
                matched = min(remaining, lot["qty"])
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] == ZERO:
                    lots.pop(0)
            if remaining > ZERO:
                raise CommandRejected("Existing ledger has a SELL exceeding FIFO inventory.")
    return lots


def _position_from_lots(lots: list[dict[str, Decimal]]) -> tuple[Decimal, Decimal, Decimal]:
    quantity = sum((lot["qty"] for lot in lots), ZERO)
    gross = sum((lot["qty"] * lot["gross_per_unit"] for lot in lots), ZERO)
    fees = sum((lot["qty"] * lot["fee_per_unit"] for lot in lots), ZERO)
    return quantity, gross, fees


def submit_paper_order(
    database_path: str | Path,
    *,
    idempotency_key: str,
    side: str,
    symbol: str,
    quantity: str | Decimal | None = None,
    quote_amount: str | Decimal | None = None,
    confirmed: bool = False,
    actor: str = "dashboard_authenticated",
) -> dict[str, Any]:
    """Execute a confirmed, idempotent Paper-only fill against the bot FIFO ledger."""
    side = str(side).upper()
    symbol = str(symbol).upper()
    if not idempotency_key or len(idempotency_key) > 128:
        raise CommandRejected("A valid Idempotency-Key header is required.", status_code=422)
    if side not in {"BUY", "SELL"}:
        raise CommandRejected("Only BUY and SELL are supported.", status_code=422)
    if symbol not in ALLOWED_SYMBOLS:
        raise CommandRejected("Symbol is not allowlisted.", status_code=422)
    if confirmed is not True:
        raise CommandRejected("Explicit order confirmation is required.", status_code=428)

    qty_input = str(quantity) if quantity is not None else None
    quote_input = str(quote_amount) if quote_amount is not None else None
    payload = {
        "side": side, "symbol": symbol,
        "quantity": qty_input, "quote_amount": quote_input,
    }
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    command_id = str(uuid.uuid4())
    now = _now()

    with _connect(database_path) as db:
        _ensure_schema(db)
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT * FROM bot_commands WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["payload_hash"] != payload_hash:
                db.rollback()
                raise CommandRejected(
                    "Idempotency key was already used with a different payload.", status_code=409
                )
            result = dict(existing)
            db.commit()
            return {
                "command_id": result["command_id"],
                "status": result["status"],
                "duplicate": True,
                "result": json.loads(result["result_json"]) if result["result_json"] else None,
                "reason": result["rejection_reason"],
            }

        db.execute(
            """INSERT INTO bot_commands
               (command_id,idempotency_key,payload_hash,command_type,status,actor,
                symbol,quantity,quote_amount,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (command_id, idempotency_key, payload_hash, f"PAPER_{side}", "RECEIVED",
             actor[:120], symbol, qty_input, quote_input, now, now),
        )
        _event(db, command_id, "COMMAND_RECEIVED", "RECEIVED", f"{side} {symbol}")
        try:
            db.execute(
                "UPDATE bot_commands SET status='VALIDATING',updated_at=? WHERE command_id=?",
                (_now(), command_id),
            )
            _event(db, command_id, "VALIDATION_STARTED", "VALIDATING", "Paper risk checks")

            required = {"bot_account", "bot_positions", "bot_fills", "bot_market_ticks"}
            tables = {
                row["name"] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not required.issubset(tables):
                raise CommandRejected("The active database is not the continuous Paper ledger.")
            account = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
            if account is None:
                raise CommandRejected("Paper account is not initialized.")
            if side == "BUY" and bool(account["halted"]):
                raise CommandRejected(f"Paper risk halt blocks new entries: {account['halt_reason'] or 'unknown'}.")
            if side == "BUY":
                if quantity is not None or quote_amount is None:
                    raise CommandRejected("BUY requires quote_amount only.", status_code=422)
                notional = _decimal(quote_amount, "quote_amount")
                if not ZERO < notional <= MAX_NOTIONAL:
                    raise CommandRejected("BUY allocation must be greater than zero and at most 5 USDT.", status_code=422)
            else:
                if quote_amount is not None or quantity is None:
                    raise CommandRejected("SELL requires quantity only.", status_code=422)
                requested_qty = _decimal(quantity, "quantity")
                if requested_qty <= ZERO:
                    raise CommandRejected("SELL quantity must be positive.", status_code=422)

            mark = db.execute(
                """SELECT price,bid,ask,received_at_ms,data_source
                   FROM bot_market_ticks WHERE symbol=? ORDER BY id DESC LIMIT 1""",
                (symbol,),
            ).fetchone()
            if mark is None:
                raise CommandRejected("No market price is available for this symbol.")
            age_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - int(mark["received_at_ms"])
            if age_ms < -5000 or age_ms > 90000:
                raise CommandRejected("Market quote is stale; order rejected.")
            price = Decimal(mark["ask"] if side == "BUY" else mark["bid"])
            if price <= ZERO:
                raise CommandRejected("Market bid/ask is invalid.")
            fill_price = price * (Decimal(1) + SLIPPAGE if side == "BUY" else Decimal(1) - SLIPPAGE)
            cash = Decimal(account["cash"])
            fee_rate = FEE_RATE
            current_positions = db.execute(
                "SELECT symbol,quantity FROM bot_positions"
            ).fetchall()
            marked_equity = cash
            for current_position in current_positions:
                position_mark = db.execute(
                    "SELECT price FROM bot_market_ticks WHERE symbol=? ORDER BY id DESC LIMIT 1",
                    (current_position["symbol"],),
                ).fetchone()
                if position_mark is None:
                    raise CommandRejected(
                        f"No current mark exists for open position {current_position['symbol']}."
                    )
                marked_equity += (
                    Decimal(current_position["quantity"]) * Decimal(position_mark["price"])
                )
            daily_start = Decimal(account["daily_start_equity"])
            high_water = Decimal(account["high_watermark"])
            if side == "BUY" and marked_equity <= daily_start * Decimal("0.98"):
                raise CommandRejected("Daily loss limit reached; new Paper entries are blocked.")
            if side == "BUY" and high_water > ZERO and marked_equity <= high_water * Decimal("0.95"):
                raise CommandRejected("Maximum drawdown limit reached; new Paper entries are blocked.")

            if side == "BUY":
                open_count = int(db.execute("SELECT COUNT(*) FROM bot_positions").fetchone()[0])
                if db.execute("SELECT 1 FROM bot_positions WHERE symbol=?", (symbol,)).fetchone():
                    raise CommandRejected("A position already exists for this symbol.")
                if open_count >= MAX_POSITIONS:
                    raise CommandRejected("Maximum open positions reached.")
                reserve = max(RESERVE, Decimal(account["initial_cash"]) * Decimal("0.20"))
                if cash - notional < reserve:
                    raise CommandRejected("BUY would breach the 20% cash reserve.")
                effective_qty = (notional / (fill_price * (Decimal(1) + fee_rate))).quantize(
                    Decimal("0.00000001"), rounding=ROUND_DOWN
                )
                gross = effective_qty * fill_price
                fee = (gross * fee_rate).quantize(Decimal("0.00000001"))
                total = gross + fee
                if effective_qty <= ZERO or total > notional or total > cash - reserve:
                    raise CommandRejected("BUY failed quantity, fee, or available-cash checks.")
                new_cash = cash - total
                new_realized = Decimal(account["realized_pnl"])
                new_daily_realized = Decimal(account["daily_realized_pnl"])
                db.execute(
                    """INSERT INTO bot_positions
                       (symbol,quantity,entry_price,entry_gross,entry_fee,stop_loss_pct,
                        take_profit_pct,opened_at) VALUES (?,?,?,?,?,?,?,?)""",
                    (symbol, str(effective_qty), str(fill_price), str(gross), str(fee),
                     "0.50", "1.25", _now()),
                )
                net_pnl = ZERO
                result_quantity = effective_qty
            else:
                requested_qty = _decimal(quantity, "quantity")
                lots = _fifo_lots(db, symbol)
                available, _, _ = _position_from_lots(lots)
                if requested_qty > available:
                    raise CommandRejected("SELL quantity exceeds available FIFO inventory.")
                gross = requested_qty * fill_price
                fee = (gross * fee_rate).quantize(Decimal("0.00000001"))
                remaining = requested_qty
                entry_cost = ZERO
                while remaining > ZERO:
                    lot = lots[0]
                    matched = min(remaining, lot["qty"])
                    entry_cost += matched * (lot["gross_per_unit"] + lot["fee_per_unit"])
                    lot["qty"] -= matched
                    remaining -= matched
                    if lot["qty"] == ZERO:
                        lots.pop(0)
                net_pnl = gross - fee - entry_cost
                new_cash = cash + gross - fee
                new_realized = Decimal(account["realized_pnl"]) + net_pnl
                new_daily_realized = Decimal(account["daily_realized_pnl"]) + net_pnl
                result_quantity = requested_qty
                remaining_qty, remaining_gross, remaining_fees = _position_from_lots(lots)
                if remaining_qty == ZERO:
                    db.execute("DELETE FROM bot_positions WHERE symbol=?", (symbol,))
                else:
                    average = remaining_gross / remaining_qty
                    db.execute(
                        """UPDATE bot_positions SET quantity=?,entry_price=?,entry_gross=?,
                           entry_fee=? WHERE symbol=?""",
                        (str(remaining_qty), str(average), str(remaining_gross),
                         str(remaining_fees), symbol),
                    )

            tick = int(account["tick_count"]) + 1
            total_fees = Decimal(account["fees_paid"]) + fee
            high_water = max(Decimal(account["high_watermark"]), new_cash)
            timestamp = _now()
            db.execute(
                """INSERT INTO bot_fills
                   (tick,symbol,side,quantity,reference_price,fill_price,gross_quote,
                    fee_quote,net_pnl,data_source,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (tick, symbol, side, str(result_quantity), str(price), str(fill),
                 str(gross), str(fee), str(net_pnl), str(mark["data_source"]), timestamp),
            )
            db.execute(
                """UPDATE bot_account SET cash=?,realized_pnl=?,daily_realized_pnl=?,
                   fees_paid=?,tick_count=?,high_watermark=?,updated_at=? WHERE id=1""",
                (str(new_cash), str(new_realized), str(new_daily_realized), str(total_fees),
                 tick, str(high_water), timestamp),
            )
            positions = db.execute("SELECT symbol,quantity FROM bot_positions").fetchall()
            market_value = ZERO
            marks_complete = True
            for position in positions:
                price_row = db.execute(
                    "SELECT price FROM bot_market_ticks WHERE symbol=? ORDER BY id DESC LIMIT 1",
                    (position["symbol"],),
                ).fetchone()
                if price_row is None:
                    marks_complete = False
                    break
                market_value += Decimal(position["quantity"]) * Decimal(price_row["price"])
            equity = new_cash + market_value if marks_complete else new_cash
            daily_pnl = equity - Decimal(account["daily_start_equity"])
            db.execute(
                """INSERT OR REPLACE INTO bot_equity
                   (tick,price,cash,equity,realized_pnl,daily_pnl,signal,action,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (tick, str(price), str(new_cash), str(equity), str(new_realized),
                 str(daily_pnl), f"MANUAL_{side}", f"COMMAND:{command_id}", timestamp),
            )
            audit = reconcile_paper_ledger(db)
            if audit["status"] not in {"PASS", "INCOMPLETE"}:
                raise CommandRejected(
                    "Post-fill FIFO reconciliation failed: " + "; ".join(audit["issues"][:3])
                )
            result_payload = {
                "order_id": command_id,
                "symbol": symbol,
                "side": side,
                "quantity": str(result_quantity),
                "reference_price": str(price),
                "fill_price": str(fill_price),
                "gross_quote": str(gross),
                "fee_quote": str(fee),
                "net_pnl": str(net_pnl),
                "cash": str(new_cash),
                "realized_pnl": str(new_realized),
                "fifo_status": audit["status"],
                "data_source": str(mark["data_source"]),
            }
            db.execute(
                """UPDATE bot_commands SET status='COMPLETED',quantity=?,result_json=?,
                   updated_at=? WHERE command_id=?""",
                (str(result_quantity), json.dumps(result_payload, sort_keys=True),
                 timestamp, command_id),
            )
            _event(db, command_id, "COMMAND_COMPLETED", "COMPLETED",
                   f"{side} fill persisted; FIFO={audit['status']}")
            db.commit()
            return {
                "command_id": command_id,
                "status": "COMPLETED",
                "duplicate": False,
                "result": result_payload,
            }
        except CommandRejected as exc:
            db.rollback()
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT OR IGNORE INTO bot_commands
                   (command_id,idempotency_key,payload_hash,command_type,status,actor,
                    symbol,quantity,quote_amount,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (command_id, idempotency_key, payload_hash, f"PAPER_{side}", "REJECTED",
                 actor[:120], symbol, qty_input, quote_input, now, _now()),
            )
            row = db.execute(
                "SELECT command_id FROM bot_commands WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            persisted_id = row["command_id"]
            db.execute(
                """UPDATE bot_commands SET status='REJECTED',rejection_reason=?,updated_at=?
                   WHERE command_id=?""",
                (str(exc)[:500], _now(), persisted_id),
            )
            _event(db, persisted_id, "COMMAND_REJECTED", "REJECTED", str(exc))
            db.commit()
            raise
        except Exception:
            db.rollback()
            raise


def list_commands(database_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    if not Path(database_path).is_file():
        return []
    with _connect(database_path) as db:
        _ensure_schema(db)
        rows = db.execute(
            """SELECT command_id,command_type,status,actor,symbol,quantity,quote_amount,
                      result_json,rejection_reason,created_at,updated_at
               FROM bot_commands ORDER BY created_at DESC LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [
            {**dict(row), "result": json.loads(row["result_json"]) if row["result_json"] else None}
            for row in rows
        ]


def get_command(database_path: str | Path, command_id: str) -> dict[str, Any] | None:
    with _connect(database_path) as db:
        _ensure_schema(db)
        row = db.execute(
            "SELECT * FROM bot_commands WHERE command_id=?", (command_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result["result_json"]) if result["result_json"] else None
        return result
