from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
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
        if database_path == ":memory:":
            raise OrderStoreError("Spot execution requires a durable file-backed SQLite database.")
        self.database_path = database_path
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
                CREATE TABLE IF NOT EXISTS spot_trade_fills (
                    symbol TEXT NOT NULL,
                    trade_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    price TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    quote_quantity TEXT NOT NULL,
                    commission TEXT NOT NULL,
                    commission_asset TEXT NOT NULL,
                    is_buyer INTEGER NOT NULL,
                    is_maker INTEGER NOT NULL DEFAULT 0,
                    commission_quote_value TEXT,
                    trade_time_ms INTEGER NOT NULL,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY(symbol, trade_id)
                );
                CREATE INDEX IF NOT EXISTS idx_spot_trade_order
                    ON spot_trade_fills(symbol, order_id);
                CREATE TABLE IF NOT EXISTS spot_trade_sync_state (
                    symbol TEXT PRIMARY KEY,
                    last_trade_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS spot_balance_baseline (
                    asset TEXT PRIMARY KEY,
                    baseline_amount TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO spot_execution_control
                    (id, halted, halt_reason, updated_at)
                VALUES (1, 0, '', '1970-01-01T00:00:00+00:00');
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(spot_trade_fills)").fetchall()
            }
            if "is_maker" not in columns:
                db.execute(
                    "ALTER TABLE spot_trade_fills ADD COLUMN is_maker INTEGER NOT NULL DEFAULT 0"
                )
            if "commission_quote_value" not in columns:
                db.execute(
                    "ALTER TABLE spot_trade_fills ADD COLUMN commission_quote_value TEXT"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def has_risk_state(self) -> bool:
        with self._connection() as db:
            row = db.execute("SELECT 1 FROM spot_risk_state WHERE id=1").fetchone()
            return row is not None

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

    def last_trade_id(self, symbol: str) -> int | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT last_trade_id FROM spot_trade_sync_state WHERE symbol=?",
                (symbol.upper(),),
            ).fetchone()
            return int(row["last_trade_id"]) if row else None

    def record_trade_fills(
        self,
        symbol: str,
        fills: list[dict[str, Any]],
        *,
        commission_quote_values: dict[int, str] | None = None,
    ) -> int | None:
        normalized = symbol.upper()
        values = commission_quote_values or {}
        latest: int | None = None
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for fill in fills:
                trade_id = int(fill["id"])
                latest = trade_id if latest is None else max(latest, trade_id)
                fee_value = values.get(trade_id)
                db.execute(
                    """INSERT OR IGNORE INTO spot_trade_fills
                       (symbol,trade_id,order_id,price,quantity,quote_quantity,commission,
                        commission_asset,is_buyer,is_maker,commission_quote_value,
                        trade_time_ms,raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        normalized, trade_id, int(fill["orderId"]), str(fill["price"]),
                        str(fill["qty"]), str(fill["quoteQty"]), str(fill["commission"]),
                        str(fill["commissionAsset"]).upper(), int(bool(fill["isBuyer"])),
                        int(bool(fill.get("isMaker", False))),
                        None if fee_value is None else str(fee_value),
                        int(fill["time"]), json.dumps(fill, sort_keys=True),
                    ),
                )
                if fee_value is not None:
                    db.execute(
                        """UPDATE spot_trade_fills SET commission_quote_value=?
                           WHERE symbol=? AND trade_id=? AND commission_quote_value IS NULL""",
                        (str(fee_value), normalized, trade_id),
                    )
            if latest is not None:
                db.execute(
                    """INSERT INTO spot_trade_sync_state(symbol,last_trade_id,updated_at)
                       VALUES (?,?,?)
                       ON CONFLICT(symbol) DO UPDATE SET
                       last_trade_id=MAX(last_trade_id,excluded.last_trade_id),
                       updated_at=excluded.updated_at""",
                    (normalized, latest, self._now()),
                )
        return latest

    def unvalued_bnb_fills(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT symbol,trade_id,trade_time_ms
                   FROM spot_trade_fills
                   WHERE commission_asset='BNB' AND commission_quote_value IS NULL
                     AND CAST(commission AS REAL)>0
                   ORDER BY trade_time_ms,trade_id"""
            ).fetchall()
            return [dict(row) for row in rows]

    def get_trade_fill(self, symbol: str, trade_id: int) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT symbol,trade_id,commission,commission_asset,commission_quote_value
                   FROM spot_trade_fills WHERE symbol=? AND trade_id=?""",
                (symbol.upper(), int(trade_id)),
            ).fetchone()
            return dict(row) if row else None

    def trade_fills_for_order(self, symbol: str, order_id: int) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT trade_id,order_id,price,quantity,quote_quantity,commission,
                          commission_asset,is_buyer,is_maker,commission_quote_value,
                          trade_time_ms,raw_json
                   FROM spot_trade_fills WHERE symbol=? AND order_id=?
                   ORDER BY trade_time_ms,trade_id""",
                (symbol.upper(), int(order_id)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["exchange_trade"] = json.loads(item.pop("raw_json"))
            result.append(item)
        return result

    def order_fill_summary(
        self, symbol: str, order_id: int, *, base_asset: str,
    ) -> dict[str, str]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT quantity,quote_quantity,commission,commission_asset
                   FROM spot_trade_fills WHERE symbol=? AND order_id=?
                   ORDER BY trade_time_ms,trade_id""",
                (symbol.upper(), int(order_id)),
            ).fetchall()
        gross = sum((Decimal(row["quantity"]) for row in rows), Decimal("0"))
        quote = sum((Decimal(row["quote_quantity"]) for row in rows), Decimal("0"))
        base_fee = sum(
            (
                Decimal(row["commission"])
                for row in rows
                if str(row["commission_asset"]).upper() == base_asset.upper()
            ),
            Decimal("0"),
        )
        return {
            "fill_count": str(len(rows)),
            "gross_quantity": str(gross),
            "net_base_quantity": str(gross - base_fee),
            "quote_quantity": str(quote),
            "base_commission": str(base_fee),
        }

    def set_commission_quote_value(self, symbol: str, trade_id: int, value: str) -> None:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0:
            raise OrderStoreError("Commission quote valuation must be a finite non-negative amount.")
        with self._connection() as db:
            result = db.execute(
                """UPDATE spot_trade_fills SET commission_quote_value=?
                   WHERE symbol=? AND trade_id=? AND commission_quote_value IS NULL""",
                (str(amount), symbol.upper(), int(trade_id)),
            )
            if result.rowcount != 1:
                row = db.execute(
                    "SELECT commission_quote_value FROM spot_trade_fills WHERE symbol=? AND trade_id=?",
                    (symbol.upper(), int(trade_id)),
                ).fetchone()
                if row is None or row["commission_quote_value"] is None:
                    raise OrderStoreError("Could not persist historical BNB commission valuation.")

    def realized_pnl_report(self, quote_asset: str = "USDT") -> dict[str, Any]:
        from mumin_trader.exchanges.binance.realized_pnl import build_realized_pnl_report

        with self._connection() as db:
            rows = db.execute(
                """SELECT symbol,trade_id,price,quantity,quote_quantity,commission,
                          commission_asset,is_buyer,is_maker,commission_quote_value,trade_time_ms
                   FROM spot_trade_fills ORDER BY trade_time_ms,trade_id"""
            ).fetchall()
        return build_realized_pnl_report([dict(row) for row in rows], quote_asset=quote_asset)

    def trade_asset_deltas(self, quote_asset: str) -> dict[str, Decimal]:
        quote = quote_asset.upper()
        deltas: dict[str, Decimal] = {}
        with self._connection() as db:
            rows = db.execute(
                """SELECT symbol,price,quantity,quote_quantity,commission,
                          commission_asset,is_buyer
                   FROM spot_trade_fills ORDER BY trade_time_ms,trade_id"""
            ).fetchall()
        for row in rows:
            symbol = str(row["symbol"])
            if not symbol.endswith(quote):
                raise OrderStoreError(f"Stored trade is outside the configured quote asset: {symbol}")
            base = symbol.removesuffix(quote)
            quantity = Decimal(row["quantity"])
            quote_quantity = Decimal(row["quote_quantity"])
            commission = Decimal(row["commission"])
            commission_asset = str(row["commission_asset"]).upper()
            if not all(value.is_finite() and value >= 0 for value in (quantity, quote_quantity, commission)):
                raise OrderStoreError("Stored trade contains invalid quantities or commission.")
            deltas[base] = deltas.get(base, Decimal("0")) + (quantity if row["is_buyer"] else -quantity)
            deltas[quote] = deltas.get(quote, Decimal("0")) + (-quote_quantity if row["is_buyer"] else quote_quantity)
            deltas[commission_asset] = deltas.get(commission_asset, Decimal("0")) - commission
        return deltas

    def reconcile_account_balances(
        self,
        balances: list[dict[str, Any]],
        *,
        quote_asset: str,
        tolerance: str = "0.00000001",
    ) -> list[str]:
        current: dict[str, Decimal] = {}
        for row in balances:
            asset = str(row["asset"]).upper()
            free = Decimal(str(row["free"]))
            locked = Decimal(str(row["locked"]))
            if not free.is_finite() or not locked.is_finite() or free < 0 or locked < 0:
                raise OrderStoreError("Exchange returned an invalid balance during reconciliation.")
            current[asset] = free + locked

        deltas = self.trade_asset_deltas(quote_asset)
        epsilon = Decimal(tolerance)
        discrepancies: list[str] = []
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            baseline_rows = {
                str(row["asset"]): Decimal(row["baseline_amount"])
                for row in db.execute("SELECT asset,baseline_amount FROM spot_balance_baseline")
            }
            for asset in set(current) | set(deltas) | set(baseline_rows):
                actual = current.get(asset, Decimal("0"))
                delta = deltas.get(asset, Decimal("0"))
                baseline = baseline_rows.get(asset)
                if baseline is None:
                    baseline = actual - delta
                    if baseline < -epsilon:
                        discrepancies.append(f"NEGATIVE_INFERRED_BASELINE:{asset}")
                        continue
                    baseline = max(Decimal("0"), baseline)
                    db.execute(
                        """INSERT INTO spot_balance_baseline(asset,baseline_amount,captured_at)
                           VALUES (?,?,?)""",
                        (asset, str(baseline), self._now()),
                    )
                expected = baseline + delta
                if abs(actual - expected) > epsilon:
                    discrepancies.append(
                        f"BALANCE_MISMATCH:{asset}:exchange={actual}:ledger={expected}"
                    )
        return discrepancies

    def order_fill_quantity_discrepancies(self, tolerance: str = "0.00000001") -> list[str]:
        epsilon = Decimal(tolerance)
        with self._connection() as db:
            orders = db.execute(
                """SELECT client_order_id,symbol,exchange_order_id,executed_quantity
                   FROM spot_order_intents WHERE exchange_order_id IS NOT NULL"""
            ).fetchall()
            has_oco_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='spot_oco_order_lists'"
            ).fetchone() is not None
            oco_columns = (
                {row["name"] for row in db.execute("PRAGMA table_info(spot_oco_order_lists)").fetchall()}
                if has_oco_table else set()
            )
            child_id_columns = [
                name for name in ("working_order_id", "above_order_id", "below_order_id")
                if name in oco_columns
            ]
            selected_oco_columns = [
                "list_client_order_id", "symbol", "response_json", *child_id_columns
            ]
            oco_rows = (
                db.execute(
                    "SELECT " + ", ".join(selected_oco_columns) + " FROM spot_oco_order_lists"
                ).fetchall()
                if has_oco_table else []
            )
            fills = db.execute(
                """SELECT symbol,order_id,quantity FROM spot_trade_fills"""
            ).fetchall()
        totals: dict[tuple[str, int], Decimal] = {}
        for fill in fills:
            key = (str(fill["symbol"]), int(fill["order_id"]))
            totals[key] = totals.get(key, Decimal("0")) + Decimal(fill["quantity"])
        discrepancies: list[str] = []
        for order in orders:
            key = (str(order["symbol"]), int(order["exchange_order_id"]))
            exchange_reported = Decimal(order["executed_quantity"])
            ledger_filled = totals.get(key, Decimal("0"))
            if abs(exchange_reported - ledger_filled) > epsilon:
                discrepancies.append(
                    f"ORDER_FILL_MISMATCH:{order['client_order_id']}:"
                    f"exchange={exchange_reported}:ledger={ledger_filled}"
                )
        for row in oco_rows:
            try:
                response = json.loads(str(row["response_json"]))
            except (TypeError, ValueError):
                discrepancies.append(f"OCO_RESPONSE_INVALID:{row['list_client_order_id']}")
                continue
            reports = response.get("orderReports", [])
            for child_id in (row[name] for name in child_id_columns):
                if child_id is None:
                    continue
                report = next(
                    (item for item in reports if str(item.get("orderId")) == str(child_id)),
                    None,
                )
                if report is None:
                    discrepancies.append(
                        f"OCO_CHILD_STATUS_MISSING:{row['list_client_order_id']}:{child_id}"
                    )
                    continue
                key = (str(row["symbol"]), int(child_id))
                exchange_reported = Decimal(str(report.get("executedQty", "0")))
                ledger_filled = totals.get(key, Decimal("0"))
                if abs(exchange_reported - ledger_filled) > epsilon:
                    discrepancies.append(
                        f"OCO_FILL_MISMATCH:{row['list_client_order_id']}:{child_id}:"
                        f"exchange={exchange_reported}:ledger={ledger_filled}"
                    )
        return discrepancies

    def unmatched_trade_order_ids(self) -> list[str]:
        with self._connection() as db:
            has_oco_table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='spot_oco_order_lists'"
            ).fetchone() is not None
            if has_oco_table:
                oco_columns = {
                    row["name"] for row in db.execute(
                        "PRAGMA table_info(spot_oco_order_lists)"
                    ).fetchall()
                }
                match_columns = [
                    name for name in ("working_order_id", "above_order_id", "below_order_id")
                    if name in oco_columns
                ]
                match_expression = " OR ".join(
                    "CAST(c." + name + " AS INTEGER)=f.order_id" for name in match_columns
                ) or "1=0"
                rows = db.execute(
                    """SELECT DISTINCT f.symbol || ':' || CAST(f.order_id AS TEXT) AS order_ref
                       FROM spot_trade_fills AS f
                       LEFT JOIN spot_order_intents AS o
                         ON o.symbol=f.symbol AND CAST(o.exchange_order_id AS INTEGER)=f.order_id
                       LEFT JOIN spot_oco_order_lists AS c
                         ON c.symbol=f.symbol AND (""" + match_expression + """)
                       WHERE o.client_order_id IS NULL AND c.list_client_order_id IS NULL"""
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT DISTINCT f.symbol || ':' || CAST(f.order_id AS TEXT) AS order_ref
                       FROM spot_trade_fills AS f
                       LEFT JOIN spot_order_intents AS o
                         ON o.symbol=f.symbol AND CAST(o.exchange_order_id AS INTEGER)=f.order_id
                       WHERE o.client_order_id IS NULL"""
                ).fetchall()
            return [str(row["order_ref"]) for row in rows]

    def trade_position_quantities(self, quote_asset: str) -> dict[str, Decimal]:
        quote = quote_asset.upper()
        deltas = self.trade_asset_deltas(quote)
        return {asset: amount for asset, amount in deltas.items() if asset != quote}

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
