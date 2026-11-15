from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OrderStoreError(RuntimeError):
    pass


class SQLiteOrderStore:
    """Durable idempotency ledger and persistent emergency-stop state."""

    def __init__(self, database_path: str) -> None:
        if database_path.startswith("sqlite:///"):
            database_path = database_path.removeprefix("sqlite:///")
        elif "://" in database_path:
            raise OrderStoreError("Only a local SQLite database is supported for Testnet orders.")
        self.database_path = database_path
        if database_path != ":memory:":
            Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        if self.database_path != ":memory:":
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS spot_order_intents (
                    client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    state TEXT NOT NULL,
                    exchange_order_id TEXT,
                    executed_quantity TEXT NOT NULL DEFAULT '0',
                    cumulative_quote_quantity TEXT NOT NULL DEFAULT '0',
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_spot_order_state
                    ON spot_order_intents(state, updated_at);
                CREATE TABLE IF NOT EXISTS spot_execution_control (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    halted INTEGER NOT NULL DEFAULT 0,
                    halt_reason TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spot_reconciliation_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    balances_json TEXT NOT NULL,
                    open_orders_json TEXT NOT NULL,
                    discrepancies_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spot_risk_state (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    trading_day TEXT NOT NULL,
                    daily_start_equity TEXT NOT NULL,
                    high_watermark TEXT NOT NULL,
                    last_equity TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO spot_execution_control
                    (id, halted, halt_reason, updated_at)
                VALUES (1, 0, '', '1970-01-01T00:00:00+00:00');
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def is_halted(self) -> tuple[bool, str]:
        with self._connection() as db:
            row = db.execute(
                "SELECT halted, halt_reason FROM spot_execution_control WHERE id=1"
            ).fetchone()
            return bool(row["halted"]), str(row["halt_reason"])

    def halt(self, reason: str) -> None:
        normalized = reason.strip()[:500] or "MANUAL_EMERGENCY_STOP"
        with self._connection() as db:
            db.execute(
                """UPDATE spot_execution_control
                   SET halted=1, halt_reason=?, updated_at=? WHERE id=1""",
                (normalized, self._now()),
            )

    def register_intent(self, request: dict[str, str]) -> tuple[dict[str, Any], bool]:
        now = self._now()
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            halted = db.execute(
                "SELECT halted, halt_reason FROM spot_execution_control WHERE id=1"
            ).fetchone()
            if halted["halted"] and not (
                request.get("side") == "SELL"
                and halted["halt_reason"] in {"MAX_DAILY_LOSS", "MAX_DRAWDOWN"}
            ):
                raise OrderStoreError(f"Execution halt is active: {halted['halt_reason']}")
            existing = db.execute(
                "SELECT * FROM spot_order_intents WHERE client_order_id=?",
                (request["newClientOrderId"],),
            ).fetchone()
            if existing:
                if existing["request_json"] != encoded:
                    raise OrderStoreError("clientOrderId collision: existing intent has different parameters.")
                return dict(existing), False
            db.execute(
                """INSERT INTO spot_order_intents
                   (client_order_id,symbol,side,quantity,price,state,request_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,'PREPARED',?,?,?)""",
                (
                    request["newClientOrderId"], request["symbol"], request["side"],
                    request["quantity"], request["price"], encoded, now, now,
                ),
            )
            row = db.execute(
                "SELECT * FROM spot_order_intents WHERE client_order_id=?",
                (request["newClientOrderId"],),
            ).fetchone()
            return dict(row), True

    def claim_submission(self, client_order_id: str) -> bool:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            halted = db.execute(
                "SELECT halted, halt_reason FROM spot_execution_control WHERE id=1"
            ).fetchone()
            intent = db.execute(
                "SELECT side FROM spot_order_intents WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            if intent is None:
                raise OrderStoreError("Cannot claim a submission without a persisted order intent.")
            if halted["halted"] and not (
                intent["side"] == "SELL"
                and halted["halt_reason"] in {"MAX_DAILY_LOSS", "MAX_DRAWDOWN"}
            ):
                raise OrderStoreError("Execution halt is active; this order is blocked.")
            cursor = db.execute(
                """UPDATE spot_order_intents SET state='SUBMITTING', updated_at=?
                   WHERE client_order_id=? AND state='PREPARED'""",
                (self._now(), client_order_id),
            )
            return cursor.rowcount == 1

    def update_order(
        self,
        client_order_id: str,
        *,
        state: str,
        response: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        allowed = {
            "SUBMITTING", "UNKNOWN", "NEW", "PARTIALLY_FILLED", "FILLED",
            "CANCEL_PENDING", "CANCELED", "REJECTED", "EXPIRED",
        }
        if state not in allowed:
            raise OrderStoreError(f"Unsupported order state: {state}")
        payload = response or {}
        with self._connection() as db:
            db.execute(
                """UPDATE spot_order_intents SET state=?, exchange_order_id=?,
                   executed_quantity=?, cumulative_quote_quantity=?, response_json=?,
                   last_error=?, updated_at=? WHERE client_order_id=?""",
                (
                    state,
                    str(payload.get("orderId", "")) or None,
                    str(payload.get("executedQty", "0")),
                    str(payload.get("cummulativeQuoteQty", payload.get("cumulativeQuoteQty", "0"))),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    error[:1000],
                    self._now(),
                    client_order_id,
                ),
            )

    def get_order(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM spot_order_intents WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            return dict(row) if row else None

    def unresolved_orders(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT * FROM spot_order_intents
                   WHERE state IN ('SUBMITTING','UNKNOWN','NEW','PARTIALLY_FILLED','CANCEL_PENDING')
                   ORDER BY created_at"""
            ).fetchall()
            return [dict(row) for row in rows]

    def update_risk_state(
        self,
        *,
        equity: str,
        trading_day: str,
        max_daily_loss_percent: str,
        max_drawdown_percent: str,
    ) -> str | None:
        from decimal import Decimal

        current = Decimal(equity)
        daily_limit = Decimal(max_daily_loss_percent)
        drawdown_limit = Decimal(max_drawdown_percent)
        if not current.is_finite() or current <= 0:
            raise OrderStoreError("Account equity is invalid; risk state cannot be updated.")
        if not daily_limit.is_finite() or daily_limit <= 0 or not drawdown_limit.is_finite() or drawdown_limit <= 0:
            raise OrderStoreError("Risk limits must be positive finite percentages.")

        reason: str | None = None
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM spot_risk_state WHERE id=1").fetchone()
            if row is None:
                daily_start = current
                high_watermark = current
                db.execute(
                    """INSERT INTO spot_risk_state
                       (id,trading_day,daily_start_equity,high_watermark,last_equity,updated_at)
                       VALUES (1,?,?,?,?,?)""",
                    (trading_day, str(current), str(current), str(current), self._now()),
                )
            else:
                daily_start = Decimal(row["daily_start_equity"])
                high_watermark = Decimal(row["high_watermark"])
                if row["trading_day"] != trading_day:
                    daily_start = current
                high_watermark = max(high_watermark, current)
                daily_loss_pct = (
                    (daily_start - current) / daily_start * Decimal("100")
                    if daily_start > 0 else Decimal("100")
                )
                drawdown_pct = (
                    (high_watermark - current) / high_watermark * Decimal("100")
                    if high_watermark > 0 else Decimal("100")
                )
                if daily_loss_pct >= daily_limit:
                    reason = "MAX_DAILY_LOSS"
                if drawdown_pct >= drawdown_limit:
                    reason = "MAX_DRAWDOWN"
                db.execute(
                    """UPDATE spot_risk_state SET trading_day=?,daily_start_equity=?,
                       high_watermark=?,last_equity=?,updated_at=? WHERE id=1""",
                    (trading_day, str(daily_start), str(high_watermark), str(current), self._now()),
                )
            if reason:
                db.execute(
                    """UPDATE spot_execution_control
                       SET halted=1,halt_reason=?,updated_at=? WHERE id=1""",
                    (reason, self._now()),
                )
        return reason

    def record_reconciliation(
        self,
        balances: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        discrepancies: list[str],
    ) -> None:
        with self._connection() as db:
            db.execute(
                """INSERT INTO spot_reconciliation_snapshots
                   (created_at,balances_json,open_orders_json,discrepancies_json)
                   VALUES (?,?,?,?)""",
                (
                    self._now(),
                    json.dumps(balances, sort_keys=True),
                    json.dumps(open_orders, sort_keys=True),
                    json.dumps(discrepancies, sort_keys=True),
                ),
            )
