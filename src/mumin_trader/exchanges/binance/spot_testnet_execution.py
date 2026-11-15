from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import OrderStoreError, SQLiteOrderStore
from mumin_trader.exchanges.binance.testnet_order_preflight import (
    BinanceSpotTestnetOrderPreflight,
    TESTNET_REST_BASE_URL,
    TestnetPreflightError,
)


class SpotExecutionError(RuntimeError):
    pass


class SpotOrderOutcomeUnknown(SpotExecutionError):
    """The request may have reached Binance; automatic resubmission is forbidden."""


class BinanceSpotTestnetExecution:
    """Testnet-only LIMIT execution with durable intents, fail-closed retries and reconciliation.

    No configurable host is accepted. This adapter is not imported by the Paper engine
    and has no Mainnet, Margin, Futures, withdrawal, or market-order implementation.
    """

    ACTIVE_STATES = {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL", "CANCEL_PENDING"}

    def __init__(
        self,
        settings: Settings,
        store: SQLiteOrderStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._assert_safety_gate(settings)
        self.settings = settings
        self.store = store
        self._client = httpx.AsyncClient(
            base_url=TESTNET_REST_BASE_URL,
            timeout=httpx.Timeout(settings.BINANCE_TIMEOUT_SECONDS),
            headers={"X-MBX-APIKEY": settings.BINANCE_API_KEY.get_secret_value()},
            transport=transport,
        )
        self._validator = BinanceSpotTestnetOrderPreflight(settings, transport=transport)

    @staticmethod
    def _assert_safety_gate(settings: Settings) -> None:
        if settings.TRADING_ENV is not TradingEnvironment.TESTNET:
            raise SpotExecutionError("Spot execution is restricted to Binance TESTNET.")
        if settings.TRADING_MODE is not TradingMode.PAPER:
            raise SpotExecutionError("Application mode must remain PAPER for isolated Testnet execution.")
        if settings.ALLOW_MAINNET or settings.LIVE_TRADING_ENABLED:
            raise SpotExecutionError("Mainnet/live flags must remain false.")
        if not settings.TESTNET_ORDER_PREFLIGHT_ENABLED:
            raise SpotExecutionError("Testnet order preflight gate is disabled.")
        if not settings.TESTNET_SPOT_EXECUTION_ENABLED:
            raise SpotExecutionError("Spot Testnet execution gate is disabled by default.")
        if not settings.BINANCE_API_KEY.get_secret_value() or not settings.BINANCE_API_SECRET.get_secret_value():
            raise SpotExecutionError("Binance Spot Testnet credentials are required.")

    async def close(self) -> None:
        await self._validator.close()
        await self._client.aclose()

    async def _server_time(self) -> int:
        response = await self._client.get("/api/v3/time")
        payload = self._decode(response)
        return int(payload["serverTime"])

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpotExecutionError("Binance Spot Testnet returned invalid JSON.") from exc
        if response.is_error:
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("msg") if isinstance(payload, dict) else "request failed"
            raise SpotExecutionError(f"Binance Spot Testnet API error {response.status_code} ({code}): {message}")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        *,
        signed: bool = False,
    ) -> Any:
        values = dict(params or {})
        if signed:
            values["timestamp"] = str(await self._server_time())
            values["recvWindow"] = str(self.settings.BINANCE_RECV_WINDOW_MS)
            query = urlencode(values)
            signature = hmac.new(
                self.settings.BINANCE_API_SECRET.get_secret_value().encode("utf-8"),
                query.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            query = f"{query}&signature={signature}"
            response = await self._client.request(method, path, params=query)
        else:
            response = await self._client.request(method, path, params=values)
        return self._decode(response)

    async def _account(self) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v3/account", signed=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("balances"), list):
            raise SpotExecutionError("Account response is incomplete; refusing order.")
        if payload.get("canTrade") is not True:
            raise SpotExecutionError("Binance account does not explicitly confirm Spot trading permission.")
        if payload.get("canWithdraw") is not False:
            raise SpotExecutionError("API key must explicitly have withdrawals disabled.")
        return payload

    async def _exchange_symbol(self, symbol: str) -> dict[str, Any]:
        payload = await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        symbols = payload.get("symbols", [])
        if len(symbols) != 1:
            raise SpotExecutionError(f"Exchange metadata missing for {symbol}.")
        return symbols[0]

    async def _ticker_price(self, symbol: str) -> Decimal:
        payload = await self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        price = Decimal(str(payload["price"]))
        if not price.is_finite() or price <= 0:
            raise SpotExecutionError(f"Invalid mark price for {symbol}.")
        return price

    @staticmethod
    def _balance_map(account: dict[str, Any]) -> dict[str, tuple[Decimal, Decimal]]:
        result: dict[str, tuple[Decimal, Decimal]] = {}
        for row in account["balances"]:
            free = Decimal(str(row["free"]))
            locked = Decimal(str(row["locked"]))
            if not free.is_finite() or not locked.is_finite() or free < 0 or locked < 0:
                raise SpotExecutionError("Account contains an invalid asset balance.")
            result[str(row["asset"]).upper()] = (free, locked)
        return result

    async def _check_portfolio_risk(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        account: dict[str, Any],
        open_orders: list[dict[str, Any]],
        symbol_info: dict[str, Any],
    ) -> None:
        balances = self._balance_map(account)
        quote_asset = str(symbol_info.get("quoteAsset", self.settings.QUOTE_ASSET)).upper()
        base_asset = str(symbol_info.get("baseAsset", "")).upper()
        if not base_asset or quote_asset != self.settings.QUOTE_ASSET.upper():
            raise SpotExecutionError("Only configured USDT-quoted Spot symbols are supported.")
        notional = quantity * price
        free_quote = balances.get(quote_asset, (Decimal("0"), Decimal("0")))[0]
        free_base = balances.get(base_asset, (Decimal("0"), Decimal("0")))[0]

        if side == "SELL":
            if free_base < quantity:
                raise SpotExecutionError("Insufficient free base-asset balance for SELL.")
            return

        fee_buffer = Decimal("0.002")
        reserve = self.settings.MAX_CAPITAL_ALLOCATION_USD * self.settings.CAPITAL_RESERVE_PERCENT / Decimal("100")
        if free_quote - reserve < notional * (Decimal("1") + fee_buffer):
            raise SpotExecutionError("BUY would consume the configured quote reserve or exceed free USDT.")

        exposure = Decimal("0")
        occupied_symbols: set[str] = set()
        for watch_symbol in self.settings.watchlist_symbols:
            base = watch_symbol.removesuffix(quote_asset)
            free, locked = balances.get(base, (Decimal("0"), Decimal("0")))
            total = free + locked
            if total > 0:
                mark = price if watch_symbol == symbol else await self._ticker_price(watch_symbol)
                exposure += total * mark
                if total * mark >= Decimal("1.00"):
                    occupied_symbols.add(watch_symbol)

        for order in open_orders:
            if str(order.get("side", "")).upper() == "BUY":
                remaining = max(
                    Decimal("0"),
                    Decimal(str(order.get("origQty", "0"))) - Decimal(str(order.get("executedQty", "0"))),
                )
                exposure += remaining * Decimal(str(order.get("price", "0")))
                occupied_symbols.add(str(order.get("symbol", "")))

        ceiling = self.settings.MAX_CAPITAL_ALLOCATION_USD * (
            Decimal("100") - self.settings.CAPITAL_RESERVE_PERCENT
        ) / Decimal("100")
        if exposure + notional > ceiling:
            raise SpotExecutionError("Order would exceed the configured portfolio exposure ceiling.")
        if symbol not in occupied_symbols and len(occupied_symbols) >= self.settings.MAX_OPEN_POSITIONS:
            raise SpotExecutionError("Order would exceed the maximum number of open Spot positions.")

    @staticmethod
    def _status(response: dict[str, Any]) -> str:
        status = str(response.get("status", "UNKNOWN")).upper()
        return status if status in {
            "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED",
        } else "UNKNOWN"

    async def _query_by_client_id(self, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        try:
            response = await self._request(
                "GET", "/api/v3/order",
                {"symbol": symbol, "origClientOrderId": client_order_id},
                signed=True,
            )
        except SpotExecutionError as exc:
            if "API error 400" in str(exc) or "API error 404" in str(exc):
                return None
            raise
        return response if isinstance(response, dict) else None

    async def submit_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal | str,
        price: Decimal | str,
        client_order_id: str,
    ) -> dict[str, Any]:
        if self.store.is_halted()[0]:
            raise SpotExecutionError("Emergency stop is active; no new order may be submitted.")
        try:
            params = await self._validator.validate_limit_order(
                symbol=symbol, side=side, quantity=quantity, price=price,
                client_order_id=client_order_id,
            )
        except TestnetPreflightError as exc:
            raise SpotExecutionError(str(exc)) from exc

        intent, created = self.store.register_intent(params)
        if not created:
            if intent["state"] in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                return intent
            if intent["state"] in {"SUBMITTING", "UNKNOWN", "CANCEL_PENDING"}:
                observed = await self._query_by_client_id(params["symbol"], client_order_id)
                if observed is not None:
                    self.store.update_order(client_order_id, state=self._status(observed), response=observed)
                    return observed
                self.store.update_order(
                    client_order_id, state="UNKNOWN",
                    error="Previous submission outcome is unresolved; automatic resubmission is forbidden.",
                )
                raise SpotOrderOutcomeUnknown(
                    "Previous submission may have reached Binance. Reconcile manually; this clientOrderId will not be resubmitted."
                )

        if not self.store.claim_submission(client_order_id):
            raise SpotOrderOutcomeUnknown("Another worker claimed this clientOrderId; reconcile before retry.")

        try:
            account = await self._account()
            open_orders = await self._request("GET", "/api/v3/openOrders", signed=True)
            symbol_info = await self._exchange_symbol(params["symbol"])
            await self._check_portfolio_risk(
                symbol=params["symbol"], side=params["side"],
                quantity=Decimal(params["quantity"]), price=Decimal(params["price"]),
                account=account, open_orders=open_orders, symbol_info=symbol_info,
            )
        except Exception as exc:
            self.store.update_order(client_order_id, state="REJECTED", error=f"Pre-submit risk gate: {exc}")
            raise

        try:
            response = await self._request(
                "POST", "/api/v3/order",
                {key: value for key, value in params.items()},
                signed=True,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self.store.update_order(
                client_order_id, state="UNKNOWN",
                error="Transport failure after submission attempt; exchange outcome requires reconciliation.",
            )
            raise SpotOrderOutcomeUnknown(
                "Network failure during order submission. The order may exist; do not retry with a new ID."
            ) from exc
        except SpotExecutionError as exc:
            if "API error 5" in str(exc):
                self.store.update_order(client_order_id, state="UNKNOWN", error=str(exc))
                raise SpotOrderOutcomeUnknown(
                    "Binance returned a server error after submission; order outcome is unknown."
                ) from exc
            self.store.update_order(client_order_id, state="REJECTED", error=str(exc))
            raise

        state = self._status(response)
        self.store.update_order(client_order_id, state=state, response=response)
        return response

    async def cancel_order(self, *, symbol: str, client_order_id: str) -> dict[str, Any]:
        local = self.store.get_order(client_order_id)
        if local is None or local["symbol"] != symbol.upper():
            raise SpotExecutionError("Cancellation requires a matching persisted local order intent.")
        if local["state"] in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            return local
        self.store.update_order(client_order_id, state="CANCEL_PENDING")
        try:
            response = await self._request(
                "DELETE", "/api/v3/order",
                {"symbol": symbol.upper(), "origClientOrderId": client_order_id},
                signed=True,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            self.store.update_order(
                client_order_id, state="UNKNOWN",
                error="Cancel request transport failure; reconcile exchange order status.",
            )
            raise SpotOrderOutcomeUnknown("Cancellation outcome is unknown; query order status before retry.") from exc
        state = self._status(response)
        self.store.update_order(client_order_id, state=state, response=response)
        return response

    async def get_order(self, *, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        response = await self._query_by_client_id(symbol.upper(), client_order_id)
        if response is not None:
            self.store.update_order(client_order_id, state=self._status(response), response=response)
        return response

    async def emergency_stop(self, reason: str = "MANUAL_EMERGENCY_STOP") -> dict[str, Any]:
        self.store.halt(reason)
        try:
            open_orders = await self._request("GET", "/api/v3/openOrders", signed=True)
            cancelled: list[dict[str, Any]] = []
            failures: list[str] = []
            for order in open_orders:
                symbol = str(order.get("symbol", ""))
                client_id = str(order.get("clientOrderId", ""))
                try:
                    result = await self._request(
                        "DELETE", "/api/v3/order",
                        {"symbol": symbol, "origClientOrderId": client_id},
                        signed=True,
                    )
                    cancelled.append(result)
                    if client_id:
                        self.store.update_order(client_id, state=self._status(result), response=result)
                except Exception as exc:
                    failures.append(f"{symbol}:{client_id}:{type(exc).__name__}")
            return {"halted": True, "cancelled": cancelled, "failures": failures}
        except Exception as exc:
            return {"halted": True, "cancelled": [], "failures": [type(exc).__name__]}

    async def reconcile(self) -> dict[str, Any]:
        account = await self._account()
        exchange_open = await self._request("GET", "/api/v3/openOrders", signed=True)
        exchange_by_id = {
            str(order.get("clientOrderId", "")): order for order in exchange_open
        }
        discrepancies: list[str] = []
        local_unresolved = self.store.unresolved_orders()
        local_ids = {str(order["client_order_id"]) for order in local_unresolved}

        for local in local_unresolved:
            client_id = str(local["client_order_id"])
            observed = await self._query_by_client_id(str(local["symbol"]), client_id)
            if observed is None:
                if local["state"] in {"SUBMITTING", "UNKNOWN", "CANCEL_PENDING"}:
                    discrepancies.append(f"UNRESOLVED_ORDER_OUTCOME:{client_id}")
                    self.store.update_order(
                        client_id, state="UNKNOWN",
                        error="Order absent from query; outcome remains unresolved and cannot be resubmitted.",
                    )
                continue
            state = self._status(observed)
            self.store.update_order(client_id, state=state, response=observed)

        for client_id, order in exchange_by_id.items():
            if client_id not in local_ids:
                discrepancies.append(f"EXCHANGE_ORDER_WITHOUT_LOCAL_INTENT:{client_id}")
        balances = account.get("balances", [])
        self.store.record_reconciliation(balances, exchange_open, discrepancies)
        if discrepancies:
            self.store.halt("RECONCILIATION_MISMATCH")
        return {
            "balances": balances,
            "open_orders": exchange_open,
            "discrepancies": discrepancies,
            "halted": self.store.is_halted()[0],
        }
