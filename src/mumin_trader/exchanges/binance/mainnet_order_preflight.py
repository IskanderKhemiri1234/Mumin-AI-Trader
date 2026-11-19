from __future__ import annotations

import hashlib
import hmac
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx

from config.config import MAINNET_CONFIRMATION_PHRASE, Settings, TradingEnvironment, TradingMode


MAINNET_REST_BASE_URL = "https://api.binance.com"
_CLIENT_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


class MainnetPreflightError(RuntimeError):
    """Raised when a Spot Mainnet order fails a local safety or exchange check."""

    __test__ = False


class BinanceSpotMainnetOrderPreflight:
    """Validate a Spot LIMIT order and call Binance's non-executing /order/test endpoint.

    This class cannot submit a matching-engine order: its host is pinned to Spot
    Mainnet and its only POST path is /api/v3/order/test.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._assert_safety_gate(settings)
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=MAINNET_REST_BASE_URL,
            timeout=settings.BINANCE_TIMEOUT_SECONDS,
            headers={"X-MBX-APIKEY": settings.BINANCE_MAINNET_API_KEY.get_secret_value()},
            transport=transport,
        )

    @staticmethod
    def _assert_safety_gate(settings: Settings) -> None:
        if settings.TRADING_ENV is not TradingEnvironment.MAINNET:
            raise MainnetPreflightError("Refusing non-Mainnet environment.")
        if settings.TRADING_MODE is not TradingMode.LIVE:
            raise MainnetPreflightError("TRADING_MODE=LIVE is required for Mainnet preflight.")
        if not settings.ALLOW_MAINNET or not settings.LIVE_TRADING_ENABLED:
            raise MainnetPreflightError("Both independent Mainnet/live gates must be enabled.")
        if not settings.MAINNET_LEGAL_ELIGIBILITY_CONFIRMED:
            raise MainnetPreflightError("Legal-eligibility confirmation is required.")
        if settings.MAINNET_CONFIRMATION != MAINNET_CONFIRMATION_PHRASE:
            raise MainnetPreflightError("The exact Mainnet real-money confirmation phrase is required.")
        if settings.TESTNET_ORDER_PREFLIGHT_ENABLED or settings.TESTNET_SPOT_EXECUTION_ENABLED:
            raise MainnetPreflightError("Mainnet and Testnet execution gates cannot be enabled together.")
        if not settings.MAINNET_ORDER_PREFLIGHT_ENABLED:
            raise MainnetPreflightError("Mainnet order preflight is disabled by default.")
        if not settings.BINANCE_MAINNET_API_KEY.get_secret_value() or not settings.BINANCE_MAINNET_API_SECRET.get_secret_value():
            raise MainnetPreflightError("Spot Mainnet API credentials are required.")

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(path, params=params)
        return self._decode_response(response)

    async def _signed_test_order(self, params: dict[str, str]) -> dict[str, Any]:
        query = dict(params)
        server_time = await self._get_json("/api/v3/time")
        query["timestamp"] = str(int(server_time["serverTime"]))
        query["recvWindow"] = str(self.settings.BINANCE_RECV_WINDOW_MS)
        encoded = urlencode(query)
        signature = hmac.new(
            self.settings.BINANCE_MAINNET_API_SECRET.get_secret_value().encode("utf-8"),
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        query["signature"] = signature
        response = await self._client.post("/api/v3/order/test", params=query)
        payload = self._decode_response(response)
        if not isinstance(payload, dict):
            raise MainnetPreflightError("Unexpected Binance Mainnet preflight response.")
        return payload

    @staticmethod
    def _decode_response(response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MainnetPreflightError("Binance Mainnet returned a non-JSON response.") from exc
        if response.is_error:
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("msg") if isinstance(payload, dict) else "request failed"
            raise MainnetPreflightError(f"Binance Spot Mainnet rejected the request ({code}): {message}")
        return payload

    @staticmethod
    def _decimal(value: Decimal | str | int, field: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise MainnetPreflightError(f"{field} must be a valid decimal.") from exc
        if not result.is_finite() or result <= 0:
            raise MainnetPreflightError(f"{field} must be greater than zero.")
        return result

    @staticmethod
    def _is_step_aligned(value: Decimal, step: Decimal) -> bool:
        return step == 0 or (step > 0 and value % step == 0)

    @staticmethod
    def _nonnegative_decimal(value: Decimal | str | int, field: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise MainnetPreflightError(f"{field} must be a valid decimal.") from exc
        if not result.is_finite() or result < 0:
            raise MainnetPreflightError(f"{field} must be zero or greater.")
        return result

    async def validate_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal | str,
        price: Decimal | str,
        client_order_id: str,
    ) -> dict[str, str]:
        normalized_symbol = symbol.strip().upper()
        normalized_side = side.strip().upper()
        if normalized_symbol not in self.settings.watchlist_symbols:
            raise MainnetPreflightError("Symbol is not in the configured Spot watchlist.")
        if normalized_side not in {"BUY", "SELL"}:
            raise MainnetPreflightError("Only BUY and SELL sides are supported.")
        if not _CLIENT_ORDER_ID.fullmatch(client_order_id):
            raise MainnetPreflightError("client_order_id must be 1-36 letters, digits, '_' or '-'.")
        qty = self._decimal(quantity, "quantity")
        limit_price = self._decimal(price, "price")

        info = await self._get_json("/api/v3/exchangeInfo", {"symbol": normalized_symbol})
        symbols = info.get("symbols", [])
        if len(symbols) != 1:
            raise MainnetPreflightError("Symbol metadata is missing or ambiguous.")
        metadata = symbols[0]
        if metadata.get("status") != "TRADING" or metadata.get("isSpotTradingAllowed") is not True:
            raise MainnetPreflightError("Symbol is not enabled for Spot trading on Mainnet.")

        filters = {item.get("filterType"): item for item in metadata.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER")
        lot_filter = filters.get("LOT_SIZE")
        if not price_filter or not lot_filter:
            raise MainnetPreflightError("Required PRICE_FILTER or LOT_SIZE metadata is missing.")

        tick_size = self._nonnegative_decimal(price_filter.get("tickSize", "0"), "tickSize")
        step_size = self._nonnegative_decimal(lot_filter.get("stepSize", "0"), "stepSize")
        min_price = self._nonnegative_decimal(price_filter.get("minPrice", "0"), "minPrice")
        max_price = self._nonnegative_decimal(price_filter.get("maxPrice", "0"), "maxPrice")
        min_qty = self._nonnegative_decimal(lot_filter.get("minQty", "0"), "minQty")
        max_qty = self._nonnegative_decimal(lot_filter.get("maxQty", "0"), "maxQty")

        if not self._is_step_aligned(limit_price, tick_size):
            raise MainnetPreflightError("price does not align with the symbol tickSize.")
        if not self._is_step_aligned(qty, step_size):
            raise MainnetPreflightError("quantity does not align with the symbol stepSize.")
        if limit_price < min_price or (max_price > 0 and limit_price > max_price):
            raise MainnetPreflightError("price is outside the symbol PRICE_FILTER range.")
        if qty < min_qty or (max_qty > 0 and qty > max_qty):
            raise MainnetPreflightError("quantity is outside the symbol LOT_SIZE range.")

        notional = qty * limit_price
        notional_filter = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
        if notional_filter:
            minimum = self._nonnegative_decimal(notional_filter.get("minNotional", "0"), "minNotional")
            maximum = self._nonnegative_decimal(notional_filter.get("maxNotional", "0"), "maxNotional")
            if minimum > 0 and notional < minimum:
                raise MainnetPreflightError("Order notional is below the exchange minimum.")
            if maximum > 0 and notional > maximum:
                raise MainnetPreflightError("Order notional exceeds the exchange maximum.")

        if normalized_side == "BUY" and notional < self.settings.MIN_TRADE_ALLOCATION_USD:
            raise MainnetPreflightError("BUY notional is below the configured minimum trade allocation.")
        if notional > self.settings.MAX_TRADE_ALLOCATION_USD:
            raise MainnetPreflightError("Order notional exceeds the configured per-trade ceiling.")
        return {
            "symbol": normalized_symbol,
            "side": normalized_side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": format(qty, "f"),
            "price": format(limit_price, "f"),
            "newClientOrderId": client_order_id,
            "newOrderRespType": "ACK",
        }

    async def preflight_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal | str,
        price: Decimal | str,
        client_order_id: str,
    ) -> dict[str, Any]:
        """Validate exchange filters and test signature/order parameters without placing an order."""
        params = await self.validate_limit_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            client_order_id=client_order_id,
        )
        return await self._signed_test_order(params)
