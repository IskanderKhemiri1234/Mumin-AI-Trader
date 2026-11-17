from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
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
        from mumin_trader.exchanges.binance.spot_testnet_oco import BinanceSpotTestnetOCO
        from mumin_trader.exchanges.binance.spot_testnet_opoco import BinanceSpotTestnetOPOCO

        self.oco = BinanceSpotTestnetOCO(self)
        self.opoco = BinanceSpotTestnetOPOCO(self)

    @staticmethod
    def _assert_safety_gate(settings: Settings) -> None:
        if settings.TRADING_ENV is not TradingEnvironment.TESTNET:
            raise SpotExecutionError("Spot execution is restricted to Binance TESTNET.")
        if settings.TRADING_MODE is not TradingMode.TESTNET:
            raise SpotExecutionError("TRADING_MODE=TESTNET is required for exchange order execution.")
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
        if payload.get("accountType") != "SPOT":
            raise SpotExecutionError("Binance account type is missing or is not explicitly SPOT.")
        if payload.get("canTrade") is not True:
            raise SpotExecutionError("Binance account does not explicitly confirm Spot trading permission.")
        # canWithdraw is an account capability, not a reliable API-key scope check.
        # This adapter has no withdrawal endpoint; key scopes are verified separately.
        return payload

    async def _bnb_fee_readiness(
        self,
        *,
        symbol: str,
        buy_notional: Decimal,
        sell_notional: Decimal,
    ) -> dict[str, str]:
        """Fail closed unless BNB discount and its conservative fee reserve are proven."""
        unavailable = "TESTNET_FEE_DATA_UNAVAILABLE"

        if not self.settings.ENABLE_BNB_FEE_DISCOUNT:
            raise SpotExecutionError("BNB fee discount is disabled in configuration.")
        if not self.settings.REQUIRE_BNB_BALANCE_FOR_DISCOUNT:
            raise SpotExecutionError("BNB fee balance is not mandatory; refusing lifecycle execution.")

        bnb_asset = self.settings.BNB_FEE_ASSET.upper()
        if bnb_asset != "BNB" or self.settings.QUOTE_ASSET.upper() != "USDT":
            raise SpotExecutionError("Conservative BNB fee preflight currently requires BNB and USDT.")

        if (
            not buy_notional.is_finite() or buy_notional <= 0
            or not sell_notional.is_finite() or sell_notional <= 0
        ):
            raise SpotExecutionError("BUY and protective OCO notionals must be finite and positive.")

        commission = await self._request(
            "GET", "/api/v3/account/commission", {"symbol": symbol.upper()}, signed=True,
        )
        if (
            not isinstance(commission, dict)
            or str(commission.get("symbol", "")).upper() != symbol.upper()
        ):
            raise SpotExecutionError(
                f"{unavailable}: Binance commission response is missing or for a different symbol."
            )

        discount = commission.get("discount")
        if not isinstance(discount, dict):
            raise SpotExecutionError(
                f"{unavailable}: Binance did not return symbol-specific BNB discount settings."
            )

        account_enabled = discount.get("enabledForAccount")
        symbol_enabled = discount.get("enabledForSymbol")

        if account_enabled is False or symbol_enabled is False:
            raise SpotExecutionError(
                "BNB fee discount is not explicitly enabled for this account and symbol."
            )
        if account_enabled is not True or symbol_enabled is not True:
            raise SpotExecutionError(
                f"{unavailable}: Binance did not explicitly confirm both BNB discount flags."
            )

        discount_asset = discount.get("discountAsset")
        if not isinstance(discount_asset, str) or discount_asset.upper() != bnb_asset:
            raise SpotExecutionError(
                f"{unavailable}: Binance did not confirm BNB as the discount asset."
            )

        def parse_commission_decimal(raw: Any, field: str) -> Decimal:
            try:
                value = Decimal(str(raw))
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise SpotExecutionError(
                    f"{unavailable}: Binance returned a non-numeric {field} value."
                ) from exc
            if not value.is_finite() or value < 0:
                raise SpotExecutionError(
                    f"{unavailable}: Binance returned an invalid {field} value."
                )
            return value

        discount_multiplier = parse_commission_decimal(
            discount.get("discount"), "discount multiplier",
        )
        if not (Decimal("0") < discount_multiplier <= Decimal("1")):
            raise SpotExecutionError(
                f"{unavailable}: Binance returned a zero or out-of-range BNB discount multiplier."
            )

        def conservative_component(name: str) -> Decimal:
            rates = commission.get(name)
            if not isinstance(rates, dict):
                raise SpotExecutionError(
                    f"{unavailable}: Binance commission response is missing {name}."
                )

            values: dict[str, Decimal] = {}
            for key in ("maker", "taker", "buyer", "seller"):
                if key not in rates or rates[key] is None:
                    raise SpotExecutionError(
                        f"{unavailable}: Binance commission response is missing {name}.{key}."
                    )
                values[key] = parse_commission_decimal(rates[key], f"{name}.{key}")

            return max(values["maker"], values["taker"]) + values["buyer"] + values["seller"]

        standard_rate = conservative_component("standardCommission")
        tax_rate = conservative_component("taxCommission")
        special_rate = conservative_component("specialCommission")
        effective_rate = standard_rate * discount_multiplier + tax_rate + special_rate

        if not effective_rate.is_finite() or effective_rate <= 0:
            raise SpotExecutionError(
                f"{unavailable}: Commission rates do not provide a provable positive BNB fee reserve."
            )

        bnb_price = await self._ticker_price("BNBUSDT")
        account = await self._account()
        balances = self._balance_map(account)
        bnb_free = balances.get(bnb_asset, (Decimal("0"), Decimal("0")))[0]

        # Cover the BUY and the worst-priced single OCO exit, with an additional 50% reserve.
        reserve_factor = Decimal("1.50")
        required_bnb = (
            (buy_notional + sell_notional) * effective_rate / bnb_price
        ) * reserve_factor

        if not required_bnb.is_finite() or required_bnb <= 0:
            raise SpotExecutionError(
                f"{unavailable}: The calculated BNB fee reserve is invalid."
            )
        if bnb_free < required_bnb:
            raise SpotExecutionError(
                "Insufficient free BNB for the conservative BUY plus protective OCO commission reserve."
            )

        return {
            "symbol": symbol.upper(),
            "discount_asset": bnb_asset,
            "discount_enabled_for_account": "true",
            "discount_enabled_for_symbol": "true",
            "discount_multiplier": str(discount_multiplier),
            "effective_fee_rate_upper_bound": str(effective_rate),
            "bnbusdt_price": str(bnb_price),
            "buy_notional_usdt": str(buy_notional),
            "worst_oco_exit_notional_usdt": str(sell_notional),
            "reserve_factor": str(reserve_factor),
            "required_bnb": str(required_bnb),
            "free_bnb": str(bnb_free),
        }

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

    async def _historical_bnb_quote_mark(self, trade_time_ms: int) -> Decimal:
        quote = self.settings.QUOTE_ASSET.upper()
        if quote != "USDT":
            raise SpotExecutionError("Historical BNB fee valuation currently requires a USDT quote ledger.")
        candle_start = (int(trade_time_ms) // 60_000) * 60_000
        candles = await self._request(
            "GET",
            "/api/v3/klines",
            {
                "symbol": "BNBUSDT",
                "interval": "1m",
                "startTime": str(candle_start),
                "endTime": str(candle_start + 59_999),
                "limit": "1",
            },
        )
        if not isinstance(candles, list) or len(candles) != 1:
            raise SpotExecutionError(
                f"Historical BNB/USDT candle unavailable for trade timestamp {trade_time_ms}."
            )
        candle = candles[0]
        if int(candle[0]) != candle_start:
            raise SpotExecutionError("Historical BNB/USDT candle timestamp does not match the trade minute.")
        mark = Decimal(str(candle[4]))
        if not mark.is_finite() or mark <= 0:
            raise SpotExecutionError("Historical BNB/USDT candle close is invalid.")
        return mark

    async def _backfill_bnb_fee_valuations(self) -> None:
        for fill in self.store.unvalued_bnb_fills():
            try:
                mark = await self._historical_bnb_quote_mark(int(fill["trade_time_ms"]))
                fee_quote_value = Decimal(str(
                    self._commission_amount_for_fill(fill["symbol"], fill["trade_id"])
                )) * mark
                self.store.set_commission_quote_value(
                    str(fill["symbol"]), int(fill["trade_id"]), str(fee_quote_value)
                )
            except Exception as exc:
                self.store.halt("BNB_FEE_VALUATION_UNAVAILABLE")
                raise SpotExecutionError(
                    "Historical BNB commission could not be valued; fee ledger remains incomplete."
                ) from exc

    def _commission_amount_for_fill(self, symbol: str, trade_id: int) -> str:
        fill = self.store.get_trade_fill(symbol, trade_id)
        if fill is None:
            raise SpotExecutionError("Trade fill disappeared while valuing BNB commission.")
        return str(fill["commission"])

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

    async def sync_trade_history(self) -> dict[str, Decimal]:
        """Page and persist every Spot trade for the configured symbols."""
        for symbol in self.settings.watchlist_symbols:
            last_id = self.store.last_trade_id(symbol)
            from_id = 0 if last_id is None else last_id + 1
            for page in range(100):
                fills = await self._request(
                    "GET", "/api/v3/myTrades",
                    {"symbol": symbol, "limit": "1000", "fromId": str(from_id)},
                    signed=True,
                )
                if not isinstance(fills, list):
                    raise SpotExecutionError(f"Trade-history response is malformed for {symbol}.")
                if not fills:
                    break
                ids = [int(fill["id"]) for fill in fills]
                if ids != sorted(ids) or ids[-1] < from_id:
                    raise SpotExecutionError(f"Trade-history pagination is inconsistent for {symbol}.")
                bnb_values: dict[int, str] = {}
                for fill in fills:
                    if str(fill.get("commissionAsset", "")).upper() == "BNB" and Decimal(str(fill["commission"])) > 0:
                        mark = await self._historical_bnb_quote_mark(int(fill["time"]))
                        bnb_values[int(fill["id"])] = str(Decimal(str(fill["commission"])) * mark)
                self.store.record_trade_fills(
                    symbol, fills, commission_quote_values=bnb_values
                )
                if len(fills) < 1000:
                    break
                from_id = ids[-1] + 1
            else:
                raise SpotExecutionError(
                    f"Trade history for {symbol} exceeded the 100-page safety bound; reconciliation is incomplete."
                )
        await self._backfill_bnb_fee_valuations()
        return self.store.trade_position_quantities(self.settings.QUOTE_ASSET)

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
        free_quote, locked_quote = balances.get(quote_asset, (Decimal("0"), Decimal("0")))
        free_base = balances.get(base_asset, (Decimal("0"), Decimal("0")))[0]

        fee_buffer = Decimal("0.002")
        reserve = self.settings.MAX_CAPITAL_ALLOCATION_USD * self.settings.CAPITAL_RESERVE_PERCENT / Decimal("100")
        if side == "BUY" and free_quote - reserve < notional * (Decimal("1") + fee_buffer):
            raise SpotExecutionError("BUY would consume the configured quote reserve or exceed free USDT.")

        exposure = Decimal("0")
        marked_assets = Decimal("0")
        occupied_symbols: set[str] = set()
        bnb_asset = self.settings.BNB_FEE_ASSET.upper()
        known_assets = {
            watch_symbol.removesuffix(quote_asset)
            for watch_symbol in self.settings.watchlist_symbols
        }
        if bnb_asset != quote_asset:
            known_assets.add(bnb_asset)
        for asset, (free, locked) in balances.items():
            if asset != quote_asset and free + locked > 0 and asset not in known_assets:
                raise SpotExecutionError(f"Non-zero {asset} balance is outside the configured valuation watchlist.")
        for watch_symbol in self.settings.watchlist_symbols:
            base = watch_symbol.removesuffix(quote_asset)
            free, locked = balances.get(base, (Decimal("0"), Decimal("0")))
            total = free + locked
            if total > 0:
                mark = price if watch_symbol == symbol else await self._ticker_price(watch_symbol)
                marked_value = total * mark
                exposure += marked_value
                marked_assets += marked_value
                if marked_value >= Decimal("1.00"):
                    occupied_symbols.add(watch_symbol)

        if bnb_asset != quote_asset:
            bnb_free, bnb_locked = balances.get(
                bnb_asset, (Decimal("0"), Decimal("0"))
            )
            if bnb_free + bnb_locked > 0:
                marked_assets += (
                    (bnb_free + bnb_locked)
                    * await self._ticker_price(f"{bnb_asset}{quote_asset}")
                )

        equity = free_quote + locked_quote + marked_assets
        risk_equity = min(equity, self.settings.MAX_CAPITAL_ALLOCATION_USD)
        risk_reason = self.store.update_risk_state(
            equity=str(risk_equity),
            trading_day=datetime.now(timezone.utc).date().isoformat(),
            max_daily_loss_percent=str(self.settings.MAX_DAILY_LOSS_PERCENT),
            max_drawdown_percent=str(self.settings.MAX_DRAWDOWN_PERCENT),
        )
        if risk_reason and side == "BUY":
            raise SpotExecutionError(f"Risk limit reached: {risk_reason}; new BUY entries are halted.")
        if side == "SELL":
            if free_base < quantity:
                raise SpotExecutionError("Insufficient free base-asset balance for SELL.")
            return

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
            if "(-2013)" in str(exc):
                return None
            raise
        return response if isinstance(response, dict) else None

    async def _sync_fills_if_needed(self, response: dict[str, Any]) -> None:
        executed = Decimal(str(response.get("executedQty", "0")))
        if executed <= 0:
            return
        try:
            await self.sync_trade_history()
        except Exception as exc:
            self.store.halt("TRADE_LEDGER_SYNC_UNAVAILABLE")
            raise SpotOrderOutcomeUnknown(
                "The exchange reports an execution but trade fills could not be persisted; all new orders are halted pending reconciliation."
            ) from exc

    async def submit_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Decimal | str,
        price: Decimal | str,
        client_order_id: str,
    ) -> dict[str, Any]:
        self._assert_safety_gate(self.settings)
        halted, halt_reason = self.store.is_halted()
        if halted and not (side.strip().upper() == "SELL" and halt_reason in {"MAX_DAILY_LOSS", "MAX_DRAWDOWN"}):
            raise SpotExecutionError("Execution halt is active; new orders are blocked.")
        if not self.store.has_risk_state():
            raise SpotExecutionError("A successful account/trade reconciliation is required before the first order.")
        pnl_report = self.store.realized_pnl_report(self.settings.QUOTE_ASSET)
        if not pnl_report["complete"]:
            self.store.halt("PNL_LEDGER_INCOMPLETE")
            raise SpotExecutionError(
                "Fee and FIFO cost-basis ledger is incomplete; reconcile missing historical inventory/fee valuations before any order."
            )
        try:
            params = await self._validator.validate_limit_order(
                symbol=symbol, side=side, quantity=quantity, price=price,
                client_order_id=client_order_id,
            )
        except TestnetPreflightError as exc:
            raise SpotExecutionError(str(exc)) from exc

        intent, created = self.store.register_intent(params)
        if not created:
            if intent["state"] in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                return intent
            if intent["state"] in {"NEW", "PARTIALLY_FILLED", "SUBMITTING", "UNKNOWN", "CANCEL_PENDING"}:
                observed = await self._query_by_client_id(params["symbol"], client_order_id)
                if observed is not None:
                    self.store.update_order(client_order_id, state=self._status(observed), response=observed)
                    await self._sync_fills_if_needed(observed)
                    return observed
                self.store.update_order(
                    client_order_id, state="UNKNOWN",
                    error="Previously submitted order is absent from exchange query; automatic resubmission is forbidden.",
                )
                raise SpotOrderOutcomeUnknown(
                    "Existing clientOrderId has an unresolved exchange outcome; it will not be resubmitted."
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
            self.store.update_order(
                client_order_id, state="UNKNOWN",
                error="Exchange response after submission attempt requires reconciliation: " + str(exc),
            )
            raise SpotOrderOutcomeUnknown(
                "Binance returned an error after the submission attempt; outcome is treated as unknown and will not be retried automatically."
            ) from exc

        state = self._status(response)
        self.store.update_order(client_order_id, state=state, response=response)
        await self._sync_fills_if_needed(response)
        return response

    async def cancel_order(self, *, symbol: str, client_order_id: str) -> dict[str, Any]:
        self._assert_safety_gate(self.settings)
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
        except Exception as exc:
            self.store.update_order(
                client_order_id, state="UNKNOWN",
                error="Cancel request outcome is uncertain; reconcile exchange order status.",
            )
            raise SpotOrderOutcomeUnknown(
                "Cancellation outcome is unknown; query order status before retry."
            ) from exc
        state = self._status(response)
        self.store.update_order(client_order_id, state=state, response=response)
        await self._sync_fills_if_needed(response)
        return response

    async def get_order(self, *, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        response = await self._query_by_client_id(symbol.upper(), client_order_id)
        if response is not None:
            self.store.update_order(client_order_id, state=self._status(response), response=response)
            await self._sync_fills_if_needed(response)
        return response

    async def emergency_stop(self, reason: str = "MANUAL_EMERGENCY_STOP") -> dict[str, Any]:
        self._assert_safety_gate(self.settings)
        self.store.halt(reason)
        cancelled: list[dict[str, Any]] = []
        preserved_protective_orders: list[dict[str, Any]] = []
        failures: list[str] = []
        manual_review_required = False
        try:
            open_orders = await self._request("GET", "/api/v3/openOrders", signed=True)
            if not isinstance(open_orders, list):
                raise SpotExecutionError("Emergency-stop open-order response is malformed.")
            for order in open_orders:
                symbol = str(order.get("symbol", "")).upper()
                client_id = str(order.get("clientOrderId", ""))
                side = str(order.get("side", "")).upper()
                if side == "SELL":
                    preserved_protective_orders.append({
                        "symbol": symbol,
                        "clientOrderId": client_id,
                        "reason": "Protective SELL order preserved; emergency stop does not remove exit protection.",
                    })
                    continue
                if side != "BUY":
                    failures.append(f"{symbol}:{client_id}:UNKNOWN_ORDER_SIDE")
                    continue
                try:
                    order_list = (
                        self.oco.store.get_by_child_client_id(client_id)
                        if client_id else None
                    )
                    if (
                        order_list
                        and order_list.get("working_client_order_id") == client_id
                    ):
                        observed = await self.opoco.get_order_list(
                            symbol=symbol,
                            list_client_order_id=order_list["list_client_order_id"],
                        )
                        if observed is None:
                            raise SpotOrderOutcomeUnknown(
                                "OPOCO working order is not queryable; no replacement request was sent."
                            )
                        working = next(
                            (
                                item for item in observed.get("orderReports", [])
                                if item.get("clientOrderId") == client_id
                            ),
                            {},
                        )
                        if str(working.get("status", "")).upper() == "FILLED":
                            preserved_protective_orders.append({
                                "symbol": symbol,
                                "clientOrderId": client_id,
                                "listClientOrderId": order_list["list_client_order_id"],
                                "reason": "OPOCO BUY is filled; protective SELL legs must remain active.",
                            })
                            continue
                        cancelled_list = await self.opoco.cancel_order_list(
                            symbol=symbol,
                            list_client_order_id=order_list["list_client_order_id"],
                        )
                        cancelled.append({
                            "type": "OPOCO_LIST",
                            "symbol": symbol,
                            "listClientOrderId": order_list["list_client_order_id"],
                            "result": cancelled_list,
                        })
                        if self.opoco._state(cancelled_list) == "PARTIAL_FILL_REVIEW":
                            manual_review_required = True
                            failures.append(
                                f"{symbol}:{client_id}:PARTIAL_BUY_CANCELED_WITH_UNPROTECTED_INVENTORY_REVIEW_REQUIRED"
                            )
                        continue

                    result = await self._request(
                        "DELETE",
                        "/api/v3/order",
                        {"symbol": symbol, "origClientOrderId": client_id},
                        signed=True,
                    )
                    cancelled.append(result)
                    if client_id and self.store.get_order(client_id) is not None:
                        self.store.update_order(
                            client_id, state=self._status(result), response=result
                        )
                    await self._sync_fills_if_needed(result)
                except Exception as exc:
                    failures.append(f"{symbol}:{client_id}:{type(exc).__name__}")
            return {
                "halted": True,
                "cancelled": cancelled,
                "preserved_protective_orders": preserved_protective_orders,
                "failures": failures,
                "manual_review_required": manual_review_required,
                "exposure_closed": False,
            }
        except Exception as exc:
            return {
                "halted": True,
                "cancelled": cancelled,
                "preserved_protective_orders": preserved_protective_orders,
                "failures": failures + [type(exc).__name__],
                "manual_review_required": manual_review_required,
                "exposure_closed": False,
            }

    async def reconcile(self) -> dict[str, Any]:
        try:
            account = await self._account()
            exchange_open = await self._request("GET", "/api/v3/openOrders", signed=True)
            if not isinstance(exchange_open, list):
                raise SpotExecutionError("Open-order reconciliation response is malformed.")
            exchange_by_id = {
                str(order.get("clientOrderId", "")): order for order in exchange_open
            }
            discrepancies: list[str] = []
            balances_map = self._balance_map(account)
            quote_asset = self.settings.QUOTE_ASSET.upper()
            quote_free, quote_locked = balances_map.get(quote_asset, (Decimal("0"), Decimal("0")))
            bnb_asset = self.settings.BNB_FEE_ASSET.upper()
            known_assets = {
                watch_symbol.removesuffix(quote_asset)
                for watch_symbol in self.settings.watchlist_symbols
            }
            if bnb_asset != quote_asset:
                known_assets.add(bnb_asset)
            marked_assets = Decimal("0")
            for asset, (free, locked) in balances_map.items():
                if asset != quote_asset and free + locked > 0 and asset not in known_assets:
                    raise SpotExecutionError(
                        f"Non-zero {asset} balance is outside the configured valuation watchlist."
                    )
            for watch_symbol in self.settings.watchlist_symbols:
                base = watch_symbol.removesuffix(quote_asset)
                free, locked = balances_map.get(base, (Decimal("0"), Decimal("0")))
                if free + locked > 0:
                    marked_assets += (free + locked) * await self._ticker_price(watch_symbol)
            if bnb_asset != quote_asset:
                bnb_free, bnb_locked = balances_map.get(
                    bnb_asset, (Decimal("0"), Decimal("0"))
                )
                if bnb_free + bnb_locked > 0:
                    marked_assets += (
                        (bnb_free + bnb_locked)
                        * await self._ticker_price(f"{bnb_asset}{quote_asset}")
                    )
            account_equity = quote_free + quote_locked + marked_assets
            risk_equity = min(account_equity, self.settings.MAX_CAPITAL_ALLOCATION_USD)
            risk_reason = self.store.update_risk_state(
                equity=str(risk_equity),
                trading_day=datetime.now(timezone.utc).date().isoformat(),
                max_daily_loss_percent=str(self.settings.MAX_DAILY_LOSS_PERCENT),
                max_drawdown_percent=str(self.settings.MAX_DRAWDOWN_PERCENT),
            )
            if risk_reason:
                discrepancies.append(f"RISK_LIMIT:{risk_reason}")
            trade_positions = await self.sync_trade_history()
            pnl_report = self.store.realized_pnl_report(self.settings.QUOTE_ASSET)
            if not pnl_report["complete"]:
                discrepancies.extend(
                    f"PNL_ACCOUNTING_INCOMPLETE:{item}" for item in pnl_report["unresolved"]
                )
            local_unresolved = self.store.unresolved_orders()
            local_ids = {str(order["client_order_id"]) for order in local_unresolved}
            local_ids.update(self.oco.store.child_client_order_ids())

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

            for trade_ref in self.store.unmatched_trade_order_ids():
                discrepancies.append(f"EXCHANGE_TRADE_WITHOUT_LOCAL_INTENT:{trade_ref}")
            discrepancies.extend(self.store.order_fill_quantity_discrepancies())

            for client_id in exchange_by_id:
                if client_id not in local_ids:
                    discrepancies.append(f"EXCHANGE_ORDER_WITHOUT_LOCAL_INTENT:{client_id}")
            balances = account.get("balances", [])
            balance_discrepancies = self.store.reconcile_account_balances(
                balances, quote_asset=self.settings.QUOTE_ASSET,
            )
            discrepancies.extend(balance_discrepancies)
            self.store.record_reconciliation(balances, exchange_open, discrepancies)
            if any(not item.startswith("RISK_LIMIT:") for item in discrepancies):
                self.store.halt("RECONCILIATION_MISMATCH")
            elif risk_reason:
                self.store.halt(risk_reason)
            return {
                "balances": balances,
                "open_orders": exchange_open,
                "discrepancies": discrepancies,
                "risk_reason": risk_reason,
                "trade_positions": {asset: str(quantity) for asset, quantity in trade_positions.items()},
                "realized_pnl": pnl_report,
                "halted": self.store.is_halted()[0],
            }
        except Exception:
            self.store.halt("RECONCILIATION_UNAVAILABLE")
            raise
