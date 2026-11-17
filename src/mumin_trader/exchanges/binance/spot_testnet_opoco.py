from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from mumin_trader.exchanges.binance.spot_testnet_execution import (
    SpotExecutionError,
    SpotOrderOutcomeUnknown,
)
from mumin_trader.exchanges.binance.spot_testnet_oco import (
    SQLiteOCOOrderStore,
    validate_fixed_exit_risk,
    validate_oco_leg_notionals,
)


class BinanceSpotTestnetOPOCO:
    """Fail-closed BUY + pending protective OCO list for Binance Spot Testnet."""

    def __init__(self, execution: Any) -> None:
        self.execution = execution
        self.store: SQLiteOCOOrderStore = execution.oco.store

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
    def _aligned(value: Decimal, increment: Decimal) -> bool:
        return increment > 0 and value % increment == 0

    @staticmethod
    def _validate_client_ids(values: dict[str, str]) -> None:
        if any(not value or len(value) > 36 for value in values.values()):
            raise SpotExecutionError("Every OPOCO client ID must contain 1 to 36 characters.")
        if len(set(values.values())) != len(values):
            raise SpotExecutionError("OPOCO list, working and pending client IDs must be unique.")

    async def _validate_request(
        self,
        *,
        symbol: str,
        quantity: Decimal | str,
        working_price: Decimal | str,
        take_profit_price: Decimal | str,
        stop_price: Decimal | str,
        stop_limit_price: Decimal | str,
        list_client_order_id: str,
        working_client_order_id: str,
        pending_above_client_order_id: str,
        pending_below_client_order_id: str,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        api = self.execution
        api._assert_safety_gate(api.settings)
        symbol = symbol.upper()
        if symbol not in {item.upper() for item in api.settings.watchlist_symbols}:
            raise SpotExecutionError("OPOCO symbol is outside the configured Testnet watchlist.")

        ids = {
            "listClientOrderId": list_client_order_id,
            "workingClientOrderId": working_client_order_id,
            "pendingAboveClientOrderId": pending_above_client_order_id,
            "pendingBelowClientOrderId": pending_below_client_order_id,
        }
        self._validate_client_ids(ids)

        qty = self._decimal(quantity, "quantity")
        working = self._decimal(working_price, "working_price")
        take_profit = self._decimal(take_profit_price, "take_profit_price")
        stop = self._decimal(stop_price, "stop_price")
        stop_limit = self._decimal(stop_limit_price, "stop_limit_price")

        metadata = await api._exchange_symbol(symbol)
        capability = {
            "otoAllowed": metadata.get("otoAllowed") is True,
            "opoAllowed": metadata.get("opoAllowed") is True,
            "ocoAllowed": metadata.get("ocoAllowed") is True,
        }
        if not all(capability.values()):
            raise SpotExecutionError(
                "OPOCO is not enabled by Exchange Information for this symbol; no order was sent."
            )
        if metadata.get("status") != "TRADING" or metadata.get("isSpotTradingAllowed") is not True:
            raise SpotExecutionError("OPOCO requires an actively tradable Spot symbol.")

        filters = {
            item.get("filterType"): item
            for item in metadata.get("filters", [])
            if isinstance(item, dict)
        }
        lot = filters.get("LOT_SIZE", {})
        price_filter = filters.get("PRICE_FILTER", {})
        min_qty = Decimal(str(lot.get("minQty", "0")))
        max_qty = Decimal(str(lot.get("maxQty", "0")))
        step = Decimal(str(lot.get("stepSize", "0")))
        tick = Decimal(str(price_filter.get("tickSize", "0")))
        if (
            qty < min_qty
            or (max_qty > 0 and qty > max_qty)
            or not self._aligned(qty, step)
        ):
            raise SpotExecutionError("OPOCO working quantity violates LOT_SIZE min/max/step filters.")
        for name, price in (
            ("working_price", working),
            ("take_profit_price", take_profit),
            ("stop_price", stop),
            ("stop_limit_price", stop_limit),
        ):
            if not self._aligned(price, tick):
                raise SpotExecutionError(f"{name} violates PRICE_FILTER tickSize.")

        last_price = await api._ticker_price(symbol)
        if not (working < last_price and take_profit > last_price > stop):
            raise SpotExecutionError(
                "OPOCO requires BUY working price below last price and SELL exits straddling last price."
            )
        if stop_limit > stop:
            raise SpotExecutionError("SELL stop-limit price must not exceed its stop trigger.")
        validate_fixed_exit_risk(working, take_profit, stop)

        quote_notional = qty * working
        if (
            quote_notional < api.settings.MIN_TRADE_ALLOCATION_USD
            or quote_notional > api.settings.MAX_TRADE_ALLOCATION_USD
        ):
            raise SpotExecutionError("OPOCO working BUY notional is outside configured trade limits.")
        notional_filters = [
            item for item in (
                filters.get("NOTIONAL"),
                filters.get("MIN_NOTIONAL"),
                filters.get("MAX_NOTIONAL"),
            )
            if isinstance(item, dict)
        ]
        for item in notional_filters:
            minimum = Decimal(str(item.get("minNotional", "0")))
            maximum = Decimal(str(item.get("maxNotional", "0")))
            if minimum > 0 and quote_notional < minimum:
                raise SpotExecutionError("OPOCO working BUY is below the symbol minimum notional.")
            if maximum > 0 and quote_notional > maximum:
                raise SpotExecutionError("OPOCO working BUY exceeds the symbol maximum notional.")
            validate_oco_leg_notionals(qty, take_profit, stop_limit, minimum, maximum)

        params = {
            "symbol": symbol,
            "listClientOrderId": list_client_order_id,
            "workingType": "LIMIT",
            "workingSide": "BUY",
            "workingPrice": str(working),
            "workingQuantity": str(qty),
            "workingTimeInForce": "GTC",
            "workingClientOrderId": working_client_order_id,
            "pendingSide": "SELL",
            "pendingAboveType": "LIMIT_MAKER",
            "pendingAbovePrice": str(take_profit),
            "pendingAboveClientOrderId": pending_above_client_order_id,
            "pendingBelowType": "STOP_LOSS_LIMIT",
            "pendingBelowStopPrice": str(stop),
            "pendingBelowPrice": str(stop_limit),
            "pendingBelowTimeInForce": "GTC",
            "pendingBelowClientOrderId": pending_below_client_order_id,
            "newOrderRespType": "RESULT",
        }
        return params, metadata

    async def place_buy_opoco(
        self,
        *,
        symbol: str,
        quantity: Decimal | str,
        working_price: Decimal | str,
        take_profit_price: Decimal | str,
        stop_price: Decimal | str,
        stop_limit_price: Decimal | str,
        list_client_order_id: str,
        working_client_order_id: str,
        pending_above_client_order_id: str,
        pending_below_client_order_id: str,
    ) -> dict[str, Any]:
        api = self.execution
        api._assert_safety_gate(api.settings)
        halted, _ = api.store.is_halted()
        if halted:
            raise SpotExecutionError("Execution halt is active; OPOCO placement is blocked.")
        if not api.store.has_risk_state():
            raise SpotExecutionError("A successful account/trade reconciliation is required before OPOCO.")
        pnl = api.store.realized_pnl_report(api.settings.QUOTE_ASSET)
        if not pnl.get("complete"):
            api.store.halt("PNL_LEDGER_INCOMPLETE")
            raise SpotExecutionError("FIFO/fee ledger is incomplete; OPOCO placement is blocked.")

        params, metadata = await self._validate_request(
            symbol=symbol,
            quantity=quantity,
            working_price=working_price,
            take_profit_price=take_profit_price,
            stop_price=stop_price,
            stop_limit_price=stop_limit_price,
            list_client_order_id=list_client_order_id,
            working_client_order_id=working_client_order_id,
            pending_above_client_order_id=pending_above_client_order_id,
            pending_below_client_order_id=pending_below_client_order_id,
        )
        qty = Decimal(params["workingQuantity"])
        buy_notional = qty * Decimal(params["workingPrice"])
        sell_notional = qty * max(
            Decimal(params["pendingAbovePrice"]),
            Decimal(params["pendingBelowPrice"]),
        )
        fee_preflight = await api._bnb_fee_readiness(
            symbol=symbol,
            buy_notional=buy_notional,
            sell_notional=sell_notional,
        )
        account = await api._account()
        open_orders = await api._request("GET", "/api/v3/openOrders", signed=True)
        symbol_filters = {
            item.get("filterType"): item
            for item in metadata.get("filters", [])
            if isinstance(item, dict)
        }
        symbol_order_limit = symbol_filters.get("MAX_NUM_ORDERS", {}).get("maxNumOrders")
        exchange_order_limit = symbol_filters.get("EXCHANGE_MAX_NUM_ORDERS", {}).get("maxNumOrders")
        symbol_open_count = sum(
            1 for item in open_orders if str(item.get("symbol", "")).upper() == symbol.upper()
        )
        if symbol_order_limit is not None and symbol_open_count + 3 > int(symbol_order_limit):
            raise SpotExecutionError("OPOCO would exceed the symbol MAX_NUM_ORDERS filter.")
        if exchange_order_limit is not None and len(open_orders) + 3 > int(exchange_order_limit):
            raise SpotExecutionError("OPOCO would exceed the exchange EXCHANGE_MAX_NUM_ORDERS filter.")
        algo_limit = symbol_filters.get("MAX_NUM_ALGO_ORDERS", {}).get("maxNumAlgoOrders")
        algo_types = {"STOP_LOSS", "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"}
        symbol_algo_count = sum(
            1 for item in open_orders
            if str(item.get("symbol", "")).upper() == symbol.upper()
            and str(item.get("type", "")).upper() in algo_types
        )
        if algo_limit is not None and symbol_algo_count + 2 > int(algo_limit):
            raise SpotExecutionError("OPOCO would exceed the symbol MAX_NUM_ALGO_ORDERS filter.")
        await api._check_portfolio_risk(
            symbol=symbol.upper(),
            side="BUY",
            quantity=qty,
            price=Decimal(params["workingPrice"]),
            account=account,
            open_orders=open_orders,
            symbol_info=metadata,
        )

        record = {
            **params,
            "side": "BUY",
            "quantity": str(qty),
            "protection_mode": "OPOCO",
        }
        intent, created = self.store.register(record)
        if not created and intent["state"] != "PREPARED":
            if intent["state"] in {"COMPLETE", "CANCELED", "REJECT"}:
                return json.loads(intent["response_json"])
            observed = await self.get_order_list(
                symbol=symbol,
                list_client_order_id=list_client_order_id,
            )
            if observed is not None:
                return observed
            self.store.update(
                list_client_order_id,
                state="UNKNOWN",
                error="Existing OPOCO list is not visible; automatic resubmission forbidden.",
            )
            raise SpotOrderOutcomeUnknown("OPOCO outcome is unresolved; it will not be resubmitted.")
        if not self.store.claim(list_client_order_id):
            raise SpotOrderOutcomeUnknown("OPOCO submission is already claimed; reconcile before retry.")

        try:
            response = await api._request("POST", "/api/v3/orderList/opoco", params, signed=True)
        except Exception as exc:
            self.store.update(
                list_client_order_id,
                state="UNKNOWN",
                error="OPOCO submission outcome uncertain; query the original list client ID.",
            )
            raise SpotOrderOutcomeUnknown(
                "OPOCO submission outcome is unknown; do not submit a replacement list."
            ) from exc

        orders = response.get("orders", []) if isinstance(response, dict) else []
        order_ids = [str(item.get("orderId", "")) for item in orders if isinstance(item, dict)]
        actual_client_ids = {
            str(item.get("clientOrderId", ""))
            for item in orders if isinstance(item, dict)
        }
        expected_client_ids = {
            working_client_order_id,
            pending_above_client_order_id,
            pending_below_client_order_id,
        }
        if (
            not isinstance(response, dict)
            or response.get("orderListId") is None
            or len(orders) != 3
            or len(order_ids) != 3
            or any(not item or item == "0" for item in order_ids)
            or len(set(order_ids)) != 3
            or actual_client_ids != expected_client_ids
        ):
            self.store.update(
                list_client_order_id,
                state="UNKNOWN",
                response=response if isinstance(response, dict) else {},
                error="OPOCO response omitted the parent or three distinct child order IDs.",
            )
            raise SpotOrderOutcomeUnknown(
                "OPOCO response is incomplete; reconcile the original list before any retry."
            )
        state = self._state(response)
        self.store.update(list_client_order_id, state=state, response=response)
        for report in response.get("orderReports", []):
            if str(report.get("status", "")).upper() != "PENDING_NEW":
                await api._sync_fills_if_needed(report)
        if state == "PARTIAL_FILL_REVIEW":
            api.store.halt("OPOCO_PARTIAL_FILL_REVIEW")
        response["fee_preflight"] = fee_preflight
        return response

    @staticmethod
    def _state(response: dict[str, Any]) -> str:
        reports = response.get("orderReports", [])
        if not isinstance(reports, list):
            return "UNKNOWN"
        by_client = {
            str(item.get("clientOrderId", "")): item
            for item in reports if isinstance(item, dict)
        }
        # The client IDs are persisted with the request and used as the authoritative leg mapping.
        # This method is static so callers must supply the normalized report roles explicitly.
        if len(by_client) != 3 or not all(by_client):
            return "UNKNOWN"
        order_ids = {str(item.get("orderId", "")) for item in by_client.values()}
        if len(order_ids) != 3 or "" in order_ids or "0" in order_ids:
            return "UNKNOWN"
        if response.get("orderListId") is None or not response.get("listClientOrderId"):
            return "UNKNOWN"
        values = list(by_client.values())
        working = next((item for item in values if str(item.get("type", "")).upper() == "LIMIT"), None)
        if working is None:
            # A query response may omit type on pending children; identify the working order by BUY side.
            working = next((item for item in values if str(item.get("side", "")).upper() == "BUY"), None)
        if working is None:
            return "UNKNOWN"
        above = next((item for item in values if str(item.get("type", "")).upper() == "LIMIT_MAKER"), None)
        below = next((item for item in values if item is not working and item is not above), None)
        if above is None or below is None:
            return "UNKNOWN"
        if (
            str(working.get("side", "")).upper() != "BUY"
            or str(above.get("side", "")).upper() != "SELL"
            or str(below.get("side", "")).upper() != "SELL"
            or str(below.get("type", "")).upper() != "STOP_LOSS_LIMIT"
        ):
            return "UNKNOWN"
        working_status = str(working.get("status", "")).upper()
        try:
            working_filled = Decimal(str(working.get("executedQty", "0")))
            above_filled = Decimal(str(above.get("executedQty", "0")))
            below_filled = Decimal(str(below.get("executedQty", "0")))
        except (InvalidOperation, ValueError):
            return "UNKNOWN"
        if working_filled < 0 or above_filled < 0 or below_filled < 0:
            return "UNKNOWN"
        if working_status == "PARTIALLY_FILLED" or (
            working_filled > 0 and working_status != "FILLED"
        ):
            return "PARTIAL_FILL_REVIEW"
        if working_status in {"CANCELED", "EXPIRED", "REJECTED"}:
            return "PARTIAL_FILL_REVIEW" if working_filled > 0 else "CANCELED"
        list_status = str(response.get("listOrderStatus", "")).upper()
        if working_status != "FILLED":
            if working_status == "NEW" and working_filled == 0:
                return "CANCELED" if list_status == "ALL_DONE" else "WORKING"
            return "UNKNOWN"
        above_status = str(above.get("status", "")).upper()
        below_status = str(below.get("status", "")).upper()
        if any(
            status == "PARTIALLY_FILLED"
            or (amount > 0 and status != "FILLED")
            for status, amount in ((above_status, above_filled), (below_status, below_filled))
        ):
            return "PARTIAL_FILL_REVIEW"
        if list_status == "ALL_DONE":
            if (
                above_status == "FILLED"
                and below_status in {"CANCELED", "EXPIRED"}
            ) or (
                below_status == "FILLED"
                and above_status in {"CANCELED", "EXPIRED"}
            ):
                return "COMPLETE"
            if above_status in {"CANCELED", "EXPIRED", "REJECTED"} and below_status in {
                "CANCELED", "EXPIRED", "REJECTED"
            }:
                return "CANCELED"
            return "UNKNOWN"
        if above_status in {"NEW", "PENDING_NEW"} and below_status in {"NEW", "PENDING_NEW"}:
            return "PROTECTION_ARMED"
        return "UNKNOWN"

    async def get_order_list(
        self, *, symbol: str, list_client_order_id: str,
    ) -> dict[str, Any] | None:
        local = self.store.get(list_client_order_id)
        if local is None or local["symbol"] != symbol.upper():
            raise SpotExecutionError("OPOCO recovery requires a matching persisted list intent.")
        request = json.loads(local["request_json"])
        try:
            response = await self.execution._request(
                "GET",
                "/api/v3/orderList",
                {"origClientOrderId": list_client_order_id},
                signed=True,
            )
        except SpotExecutionError as exc:
            if "(-2013)" in str(exc):
                return None
            raise
        if not isinstance(response, dict):
            return None
        if str(response.get("listClientOrderId", "")) != list_client_order_id:
            raise SpotOrderOutcomeUnknown("OPOCO parent list identity does not match the persisted client ID.")
        parent_orders = {
            str(item.get("clientOrderId", "")): item
            for item in response.get("orders", []) if isinstance(item, dict)
        }
        required_ids = (
            request["workingClientOrderId"],
            request["pendingAboveClientOrderId"],
            request["pendingBelowClientOrderId"],
        )
        if any(client_id not in parent_orders for client_id in required_ids):
            raise SpotOrderOutcomeUnknown("OPOCO parent response omitted one or more child order IDs.")

        async def query_child(client_order_id: str) -> dict[str, Any] | None:
            try:
                report = await self.execution._request(
                    "GET",
                    "/api/v3/order",
                    {"symbol": symbol.upper(), "origClientOrderId": client_order_id},
                    signed=True,
                )
            except SpotExecutionError as exc:
                if "(-2013)" in str(exc):
                    return None
                raise
            if not isinstance(report, dict):
                return None
            await self.execution._sync_fills_if_needed(report)
            return report

        working = await query_child(request["workingClientOrderId"])
        if working is None:
            raise SpotOrderOutcomeUnknown("OPOCO working BUY is not queryable; state remains unresolved.")
        if (
            str(working.get("clientOrderId", "")) != request["workingClientOrderId"]
            or str(working.get("symbol", "")).upper() != symbol.upper()
            or str(working.get("orderId", "")) != str(
                parent_orders[request["workingClientOrderId"]].get("orderId", "")
            )
        ):
            raise SpotOrderOutcomeUnknown("OPOCO working order identity does not match its persisted intent.")
        working_status = str(working.get("status", "")).upper()
        if working_status == "FILLED":
            above = await query_child(request["pendingAboveClientOrderId"])
            below = await query_child(request["pendingBelowClientOrderId"])
            if above is None:
                above_parent = parent_orders[request["pendingAboveClientOrderId"]]
                above = {
                    **above_parent,
                    "status": "UNKNOWN",
                    "executedQty": "0",
                    "origQty": "0",
                    "type": "LIMIT_MAKER",
                    "side": "SELL",
                }
            if below is None:
                below_parent = parent_orders[request["pendingBelowClientOrderId"]]
                below = {
                    **below_parent,
                    "status": "UNKNOWN",
                    "executedQty": "0",
                    "origQty": "0",
                    "type": "STOP_LOSS_LIMIT",
                    "side": "SELL",
                }
            if (
                str(above.get("clientOrderId", "")) != request["pendingAboveClientOrderId"]
                or str(below.get("clientOrderId", "")) != request["pendingBelowClientOrderId"]
                or str(above.get("symbol", "")).upper() != symbol.upper()
                or str(below.get("symbol", "")).upper() != symbol.upper()
                or str(above.get("orderId", "")) != str(
                    parent_orders[request["pendingAboveClientOrderId"]].get("orderId", "")
                )
                or str(below.get("orderId", "")) != str(
                    parent_orders[request["pendingBelowClientOrderId"]].get("orderId", "")
                )
            ):
                raise SpotOrderOutcomeUnknown("OPOCO pending leg identities do not match the persisted intent.")
        else:
            above_parent = parent_orders[request["pendingAboveClientOrderId"]]
            below_parent = parent_orders[request["pendingBelowClientOrderId"]]
            above = {
                **above_parent,
                "status": "PENDING_NEW",
                "executedQty": "0",
                "origQty": "0",
                "type": "LIMIT_MAKER",
                "side": "SELL",
            }
            below = {
                **below_parent,
                "status": "PENDING_NEW",
                "executedQty": "0",
                "origQty": "0",
                "type": "STOP_LOSS_LIMIT",
                "side": "SELL",
            }
        response["orderReports"] = [working, above, below]
        state = self._state(response)
        self.store.update(list_client_order_id, state=state, response=response)
        if state == "PARTIAL_FILL_REVIEW":
            self.execution.store.halt("OPOCO_PARTIAL_FILL_REVIEW")
        return response

    async def cancel_order_list(
        self, *, symbol: str, list_client_order_id: str,
    ) -> dict[str, Any]:
        local = self.store.get(list_client_order_id)
        if local is None or local["symbol"] != symbol.upper():
            raise SpotExecutionError("OPOCO cancellation requires a matching persisted list intent.")
        if local["state"] in {"COMPLETE", "CANCELED", "REJECT"}:
            return json.loads(local["response_json"])
        observed = await self.get_order_list(
            symbol=symbol,
            list_client_order_id=list_client_order_id,
        )
        if observed is None:
            self.store.update(
                list_client_order_id,
                state="UNKNOWN",
                error="OPOCO list is not visible; cancellation was not retried.",
            )
            raise SpotOrderOutcomeUnknown("OPOCO list outcome is unresolved; no cancel was sent.")
        self.store.update(list_client_order_id, state="CANCEL_PENDING", response=observed)
        try:
            response = await self.execution._request(
                "DELETE",
                "/api/v3/orderList",
                {"symbol": symbol.upper(), "listClientOrderId": list_client_order_id},
                signed=True,
            )
        except Exception as exc:
            self.store.update(
                list_client_order_id,
                state="UNKNOWN",
                error="OPOCO cancel outcome uncertain; query the original list before retry.",
            )
            raise SpotOrderOutcomeUnknown("OPOCO cancellation outcome is unknown.") from exc
        refreshed = await self.get_order_list(
            symbol=symbol,
            list_client_order_id=list_client_order_id,
        )
        if refreshed is None:
            self.store.update(
                list_client_order_id,
                state="UNKNOWN",
                response=response if isinstance(response, dict) else {},
                error="OPOCO cancellation response received but list could not be queried.",
            )
            raise SpotOrderOutcomeUnknown("OPOCO cancel response could not be independently verified.")
        return refreshed
