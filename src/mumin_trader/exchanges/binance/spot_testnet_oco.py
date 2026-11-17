from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from mumin_trader.exchanges.binance.spot_testnet_execution import (
    SpotExecutionError,
    SpotOrderOutcomeUnknown,
)


def validate_oco_leg_notionals(
    quantity: Decimal,
    take_profit_price: Decimal,
    stop_price: Decimal,
    minimum: Decimal,
    maximum: Decimal = Decimal("0"),
) -> None:
    """Validate both OCO child notionals independently against symbol limits."""
    for leg_name, price in (("take-profit", take_profit_price), ("stop", stop_price)):
        notional = quantity * price
        if minimum > 0 and notional < minimum:
            raise SpotExecutionError(
                f"OCO {leg_name} leg notional is below the symbol minimum-notional filter."
            )
        if maximum > 0 and notional > maximum:
            raise SpotExecutionError(
                f"OCO {leg_name} leg notional exceeds the symbol maximum-notional filter."
            )


class SQLiteOCOOrderStore:
    """Durable OCO list intents, exchange IDs, and lifecycle state."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS spot_oco_order_lists (
                    list_client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    state TEXT NOT NULL,
                    order_list_id TEXT,
                    above_order_id TEXT,
                    below_order_id TEXT,
                    working_order_id TEXT,
                    working_client_order_id TEXT,
                    above_client_order_id TEXT,
                    below_client_order_id TEXT,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(spot_oco_order_lists)").fetchall()
            }
            for name in (
                "above_order_id", "below_order_id", "working_order_id",
                "working_client_order_id", "above_client_order_id", "below_client_order_id",
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE spot_oco_order_lists ADD COLUMN {name} TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_spot_oco_state ON spot_oco_order_lists(state, updated_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def register(self, request: dict[str, str]) -> tuple[dict[str, Any], bool]:
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM spot_oco_order_lists WHERE list_client_order_id=?",
                (request["listClientOrderId"],),
            ).fetchone()
            if row:
                if row["request_json"] != encoded:
                    raise SpotExecutionError("OCO listClientOrderId collision with different parameters.")
                return dict(row), False
            db.execute(
                """INSERT INTO spot_oco_order_lists
                   (list_client_order_id,symbol,side,quantity,state,request_json,created_at,updated_at)
                   VALUES (?,?,?,?, 'PREPARED',?,?,?)""",
                (
                    request["listClientOrderId"], request["symbol"], request["side"],
                    request["quantity"], encoded, now, now,
                ),
            )
            return dict(db.execute(
                "SELECT * FROM spot_oco_order_lists WHERE list_client_order_id=?",
                (request["listClientOrderId"],),
            ).fetchone()), True

    def claim(self, list_client_order_id: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                """UPDATE spot_oco_order_lists SET state='SUBMITTING',updated_at=?
                   WHERE list_client_order_id=? AND state='PREPARED'""",
                (self._now(), list_client_order_id),
            )
            return result.rowcount == 1

    def update(
        self, list_client_order_id: str, *, state: str,
        response: dict[str, Any] | None = None, error: str = "",
    ) -> None:
        allowed = {
            "SUBMITTING", "UNKNOWN", "EXECUTING", "ALL_DONE", "CANCEL_PENDING",
            "CANCELED", "REJECT", "PREPARED", "PARTIAL_FILL_REVIEW",
            "WORKING", "PROTECTION_ARMED", "COMPLETE",
        }
        if state not in allowed:
            raise SpotExecutionError(f"Unsupported OCO list state: {state}")
        payload = response or {}
        list_id = payload.get("orderListId")
        orders = payload.get("orders", [])
        local = self.get(list_client_order_id)
        request = json.loads(local["request_json"]) if local else {}
        if "workingClientOrderId" in request:
            by_client_id = {
                str(item.get("clientOrderId", "")): item
                for item in orders if isinstance(item, dict)
            }
            working = by_client_id.get(request.get("workingClientOrderId", ""), {})
            above = by_client_id.get(request.get("pendingAboveClientOrderId", ""), {})
            below = by_client_id.get(request.get("pendingBelowClientOrderId", ""), {})
        else:
            working = {}
            above = orders[0] if len(orders) > 0 else {}
            below = orders[1] if len(orders) > 1 else {}
        with self._connect() as db:
            db.execute(
                """UPDATE spot_oco_order_lists SET state=?,
                   order_list_id=COALESCE(?,order_list_id),
                   working_order_id=COALESCE(?,working_order_id),
                   working_client_order_id=COALESCE(?,working_client_order_id),
                   above_order_id=COALESCE(?,above_order_id),
                   below_order_id=COALESCE(?,below_order_id),
                   above_client_order_id=COALESCE(?,above_client_order_id),
                   below_client_order_id=COALESCE(?,below_client_order_id),
                   response_json=?,last_error=?,updated_at=?
                   WHERE list_client_order_id=?""",
                (
                    state, None if list_id is None else str(list_id),
                    None if working.get("orderId") is None else str(working["orderId"]),
                    working.get("clientOrderId"),
                    None if above.get("orderId") is None else str(above["orderId"]),
                    None if below.get("orderId") is None else str(below["orderId"]),
                    above.get("clientOrderId"), below.get("clientOrderId"),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    error[:1000], self._now(), list_client_order_id,
                ),
            )

    def get(self, list_client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM spot_oco_order_lists WHERE list_client_order_id=?",
                (list_client_order_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_by_child_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM spot_oco_order_lists
                   WHERE working_client_order_id=?
                      OR above_client_order_id=?
                      OR below_client_order_id=?
                   LIMIT 1""",
                (client_order_id, client_order_id, client_order_id),
            ).fetchone()
            return dict(row) if row else None

    def child_client_order_ids(self) -> set[str]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT working_client_order_id,above_client_order_id,below_client_order_id
                   FROM spot_oco_order_lists"""
            ).fetchall()
        return {
            str(value)
            for row in rows
            for value in (row["working_client_order_id"], row["above_client_order_id"], row["below_client_order_id"])
            if value
        }


class BinanceSpotTestnetOCO:
    """Narrow, fail-closed SELL protective OCO adapter for Binance Spot Testnet."""

    ABOVE_TYPES = {"LIMIT_MAKER", "TAKE_PROFIT_LIMIT"}
    BELOW_TYPES = {"STOP_LOSS", "STOP_LOSS_LIMIT"}

    def __init__(self, execution: Any) -> None:
        self.execution = execution
        self.store = SQLiteOCOOrderStore(execution.store.database_path)

    @staticmethod
    def _decimal(value: Decimal | str, field: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise SpotExecutionError(f"{field} must be a valid decimal.") from exc
        if not number.is_finite() or number <= 0:
            raise SpotExecutionError(f"{field} must be finite and greater than zero.")
        return number

    @staticmethod
    def _aligned(value: Decimal, increment: Decimal, field: str) -> bool:
        return increment > 0 and value % increment == 0

    async def _validate_request(
        self, *, symbol: str, quantity: Decimal | str, take_profit_price: Decimal | str,
        stop_price: Decimal | str, stop_limit_price: Decimal | str | None,
        stop_type: str,
    ) -> dict[str, str]:
        api = self.execution
        symbol = symbol.upper()
        if symbol not in {item.upper() for item in api.settings.watchlist_symbols}:
            raise SpotExecutionError("OCO symbol is outside the configured Testnet watchlist.")
        qty = self._decimal(quantity, "quantity")
        tp = self._decimal(take_profit_price, "take_profit_price")
        stop = self._decimal(stop_price, "stop_price")
        stop_type = stop_type.upper()
        if stop_type not in self.BELOW_TYPES:
            raise SpotExecutionError("Protective stop type must be STOP_LOSS or STOP_LOSS_LIMIT.")
        stop_limit = None if stop_limit_price is None else self._decimal(stop_limit_price, "stop_limit_price")
        if stop_type == "STOP_LOSS_LIMIT" and stop_limit is None:
            raise SpotExecutionError("STOP_LOSS_LIMIT requires stop_limit_price.")
        if stop_type == "STOP_LOSS" and stop_limit is not None:
            raise SpotExecutionError("STOP_LOSS does not accept stop_limit_price.")
        symbol_info = await api._exchange_symbol(symbol)
        filters = {item.get("filterType"): item for item in symbol_info.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        min_qty = Decimal(str(lot.get("minQty", "0")))
        max_qty = Decimal(str(lot.get("maxQty", "0")))
        step = Decimal(str(lot.get("stepSize", "0")))
        tick = Decimal(str(price_filter.get("tickSize", "0")))
        if qty < min_qty or (max_qty > 0 and qty > max_qty) or not self._aligned(qty, step, "quantity"):
            raise SpotExecutionError("OCO quantity violates LOT_SIZE min/max/step filters.")
        for name, price in (("take_profit_price", tp), ("stop_price", stop)):
            if not self._aligned(price, tick, name):
                raise SpotExecutionError(f"{name} violates PRICE_FILTER tickSize.")
        if stop_limit is not None and not self._aligned(stop_limit, tick, "stop_limit_price"):
            raise SpotExecutionError("stop_limit_price violates PRICE_FILTER tickSize.")
        mark = await api._ticker_price(symbol)
        if not (tp > mark > stop):
            raise SpotExecutionError("SELL OCO requires take-profit price > last price > stop trigger.")
        if stop_limit is not None and stop_limit > stop:
            raise SpotExecutionError("SELL stop-limit price must not exceed its stop trigger.")
        # Validate both child legs independently, conservatively applying the
        # minimum even when the stop leg is a stop-market order.
        notional_filters = [
            item for item in (filters.get("NOTIONAL"), filters.get("MIN_NOTIONAL"))
            if isinstance(item, dict)
        ]
        for notional_filter in notional_filters:
            validate_oco_leg_notionals(
                qty,
                tp,
                stop_limit or stop,
                Decimal(str(notional_filter.get("minNotional", "0"))),
                Decimal(str(notional_filter.get("maxNotional", "0"))),
            )
        if stop_type == "TAKE_PROFIT_LIMIT":
            raise SpotExecutionError("The supported protective OCO uses LIMIT_MAKER for the take-profit leg.")
        return {
            "symbol": symbol,
            "side": "SELL",
            "quantity": str(qty),
            "listClientOrderId": "",
            "aboveType": "LIMIT_MAKER",
            "abovePrice": str(tp),
            "belowType": stop_type,
            "belowStopPrice": str(stop),
            **({"belowPrice": str(stop_limit), "belowTimeInForce": "GTC"} if stop_limit is not None else {}),
            "newOrderRespType": "RESULT",
        }

    async def place_sell_oco(
        self, *, symbol: str, quantity: Decimal | str, take_profit_price: Decimal | str,
        stop_price: Decimal | str, list_client_order_id: str,
        stop_limit_price: Decimal | str | None = None,
    ) -> dict[str, Any]:
        api = self.execution
        api._assert_safety_gate(api.settings)
        if not list_client_order_id or len(list_client_order_id) > 36:
            raise SpotExecutionError("list_client_order_id must contain 1 to 36 characters.")
        halted, _ = api.store.is_halted()
        if halted:
            raise SpotExecutionError("Execution halt is active; OCO placement is blocked.")
        if not api.store.has_risk_state():
            raise SpotExecutionError("A successful account/trade reconciliation is required before OCO placement.")
        report = api.store.realized_pnl_report(api.settings.QUOTE_ASSET)
        if not report["complete"]:
            api.store.halt("PNL_LEDGER_INCOMPLETE")
            raise SpotExecutionError("FIFO/fee ledger is incomplete; OCO placement is blocked.")
        params = await self._validate_request(
            symbol=symbol, quantity=quantity, take_profit_price=take_profit_price,
            stop_price=stop_price, stop_limit_price=stop_limit_price,
            stop_type="STOP_LOSS_LIMIT" if stop_limit_price is not None else "STOP_LOSS",
        )
        params["listClientOrderId"] = list_client_order_id
        intent, created = self.store.register(params)
        if not created:
            if intent["state"] in {
                "EXECUTING", "ALL_DONE", "CANCELED", "SUBMITTING",
                "UNKNOWN", "CANCEL_PENDING", "PARTIAL_FILL_REVIEW",
            }:
                observed = await self.get_order_list(
                    symbol=symbol, list_client_order_id=list_client_order_id,
                )
                if observed is not None:
                    state = self._state(observed)
                    self.store.update(list_client_order_id, state=state, response=observed)
                    if state == "PARTIAL_FILL_REVIEW":
                        api.store.halt("OCO_PARTIAL_FILL_REVIEW")
                    return observed
                if intent["state"] in {"ALL_DONE", "CANCELED"}:
                    return json.loads(intent["response_json"])
                self.store.update(
                    list_client_order_id, state="UNKNOWN",
                    error="Existing OCO list is not visible; automatic resubmission forbidden.",
                )
                raise SpotOrderOutcomeUnknown(
                    "OCO outcome is unresolved; it will not be resubmitted.",
                )
        if not self.store.claim(list_client_order_id):
            raise SpotOrderOutcomeUnknown("OCO submission is already claimed; reconcile before retry.")
        try:
            account = await api._account()
            open_orders = await api._request("GET", "/api/v3/openOrders", signed=True)
            symbol_info = await api._exchange_symbol(symbol)
            await api._check_portfolio_risk(
                symbol=symbol, side="SELL", quantity=Decimal(params["quantity"]),
                price=Decimal(params["abovePrice"]), account=account,
                open_orders=open_orders, symbol_info=symbol_info,
            )
        except Exception as exc:
            self.store.update(list_client_order_id, state="REJECT", error=f"Pre-submit gate: {exc}")
            raise
        try:
            response = await api._request("POST", "/api/v3/orderList/oco", params, signed=True)
        except Exception as exc:
            self.store.update(list_client_order_id, state="UNKNOWN",
                              error="OCO submission outcome uncertain; query list before retry.")
            raise SpotOrderOutcomeUnknown("OCO submission outcome is unknown; do not resubmit.") from exc
        if not isinstance(response, dict) or not response.get("orderListId") or len(response.get("orders", [])) != 2:
            self.store.update(list_client_order_id, state="UNKNOWN", response=response if isinstance(response, dict) else {},
                              error="OCO response omitted order-list or child-order identifiers.")
            raise SpotOrderOutcomeUnknown("OCO response is incomplete; reconcile order list before any retry.")
        state = self._state(response)
        self.store.update(list_client_order_id, state=state, response=response)
        for order in response.get("orderReports", []):
            await api._sync_fills_if_needed(order)
        if state == "PARTIAL_FILL_REVIEW":
            api.store.halt("OCO_PARTIAL_FILL_REVIEW")
        return response

    @staticmethod
    def _state(response: dict[str, Any]) -> str:
        reports = response.get("orderReports", [])
        # A partial execution changes the amount of base asset still exposed.
        # Do not label the remainder protected until both child orders and the
        # exchange account have been reconciled after the partial fill.
        if any(
            (
                str(item.get("status", "")).upper() == "PARTIALLY_FILLED"
                or (
                    Decimal(str(item.get("executedQty", "0"))) > 0
                    and str(item.get("status", "")).upper() != "FILLED"
                )
            )
            for item in reports
        ):
            return "PARTIAL_FILL_REVIEW"

        status = str(response.get("listOrderStatus", "")).upper()
        if status == "ALL_DONE":
            child_states = {str(item.get("status", "")).upper() for item in reports}
            if child_states and child_states <= {"CANCELED", "EXPIRED", "REJECTED"}:
                return "CANCELED"
            return "ALL_DONE"
        return "EXECUTING" if status in {"EXECUTING", "EXEC_STARTED"} else "UNKNOWN"

    async def get_order_list(
        self, *, symbol: str, list_client_order_id: str,
    ) -> dict[str, Any] | None:
        try:
            response = await self.execution._request(
                "GET", "/api/v3/orderList",
                {"origClientOrderId": list_client_order_id},
                signed=True,
            )
        except SpotExecutionError as exc:
            if "(-2013)" in str(exc):
                return None
            raise
        if not isinstance(response, dict):
            return None

        # GET /api/v3/orderList returns the parent list and child order IDs,
        # but not live child execution quantities/statuses. Query each child
        # order independently before deciding whether the residual is protected.
        child_reports: list[dict[str, Any]] = []
        for child in response.get("orders", []):
            order_id = child.get("orderId")
            if order_id is None:
                raise SpotOrderOutcomeUnknown(
                    "OCO list response omitted a child orderId; protection state is unresolved.",
                )
            report = await self.execution._request(
                "GET", "/api/v3/order",
                {"symbol": symbol.upper(), "orderId": str(order_id)},
                signed=True,
            )
            if not isinstance(report, dict):
                raise SpotOrderOutcomeUnknown(
                    "OCO child order query returned an invalid response.",
                )
            child_reports.append(report)
            await self.execution._sync_fills_if_needed(report)
        response["orderReports"] = child_reports

        state = self._state(response)
        local = self.store.get(list_client_order_id)
        if local is not None:
            self.store.update(
                list_client_order_id, state=state, response=response,
            )
        if state == "PARTIAL_FILL_REVIEW":
            self.execution.store.halt("OCO_PARTIAL_FILL_REVIEW")
        return response

    async def cancel_order_list(
        self, *, symbol: str, list_client_order_id: str,
    ) -> dict[str, Any]:
        local = self.store.get(list_client_order_id)
        if local is None or local["symbol"] != symbol.upper():
            raise SpotExecutionError("OCO cancellation requires a matching persisted list intent.")
        if local["state"] in {"ALL_DONE", "CANCELED", "REJECT"}:
            return json.loads(local["response_json"])
        if local["state"] in {"UNKNOWN", "CANCEL_PENDING", "SUBMITTING"}:
            observed = await self.get_order_list(
                symbol=symbol, list_client_order_id=list_client_order_id,
            )
            if observed is None:
                self.store.update(
                    list_client_order_id, state="UNKNOWN",
                    error="Order list not visible during recovery; cancellation is not resubmitted.",
                )
                raise SpotOrderOutcomeUnknown(
                    "OCO list outcome remains unknown; no cancellation retry was sent.",
                )
            observed_state = self._state(observed)
            if observed_state in {"ALL_DONE", "CANCELED"}:
                return observed
        self.store.update(list_client_order_id, state="CANCEL_PENDING")
        try:
            response = await self.execution._request(
                "DELETE", "/api/v3/orderList",
                {"symbol": symbol.upper(), "listClientOrderId": list_client_order_id},
                signed=True,
            )
        except Exception as exc:
            self.store.update(list_client_order_id, state="UNKNOWN",
                              error="OCO cancel outcome uncertain; query order list before retry.")
            raise SpotOrderOutcomeUnknown("OCO cancellation outcome is unknown; query before retry.") from exc
        state = self._state(response)
        self.store.update(list_client_order_id, state=state, response=response)
        for order in response.get("orderReports", []):
            await self.execution._sync_fills_if_needed(order)
        if state == "PARTIAL_FILL_REVIEW":
            self.execution.store.halt("OCO_PARTIAL_FILL_REVIEW")
        return response
