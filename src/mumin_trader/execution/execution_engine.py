from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
from enum import Enum
from typing import Any
from uuid import uuid4

ZERO = Decimal("0")
BPS = Decimal("10000")


class ExecutionDisabledError(RuntimeError):
    """Raised whenever a caller attempts to submit an exchange order."""


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class FeeModel:
    maker_rate: Decimal
    taker_rate: Decimal
    selected_rate: Decimal
    fee_asset: str
    bnb_discount_requested: bool
    bnb_discount_exchange_eligible: bool
    bnb_balance_confirmed: bool
    bnb_discount_applied: bool
    source: str = "BINANCE_ACCOUNT_COMMISSION"
    estimated: bool = True


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    fill_price: Decimal
    gross_quote: Decimal
    fee_quote: Decimal
    net_quote_flow: Decimal
    status: str = "FILLED_PAPER"
    fee_rate: Decimal = Decimal("0.001")
    fee_asset: str = "USDT"
    fee_source: str = "CONFIG_FALLBACK"
    bnb_discount_applied: bool = False


@dataclass(frozen=True, slots=True)
class ProfitabilityCheck:
    is_eligible: bool
    expected_gross_edge_bps: Decimal
    estimated_round_trip_cost_bps: Decimal
    expected_net_edge_bps: Decimal
    reason: str


class ExecutionEngine:
    """Paper-only execution simulator with account-specific commission support.

    Live exchange order submission is deliberately hard-disabled. The engine
    never creates an exchange order, even if passed credentials or flags.
    """

    def __init__(
        self,
        fee_rate: Decimal = Decimal("0.001"),
        slippage_bps: Decimal = Decimal("5"),
        *,
        quote_asset: str = "USDT",
        bnb_asset: str = "BNB",
        require_verified_commission: bool = False,
    ):
        fee_rate = Decimal(fee_rate)
        slippage_bps = Decimal(slippage_bps)
        if not ZERO <= fee_rate < Decimal(1):
            raise ValueError("fee_rate must be in [0, 1)")
        if not ZERO <= slippage_bps < BPS:
            raise ValueError("slippage_bps must be in [0, 10000)")
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps
        self.quote_asset = quote_asset.upper()
        self.bnb_asset = bnb_asset.upper()
        self.require_verified_commission = require_verified_commission
        self._paper_orders: dict[str, PaperOrder] = {}

    async def submit_order(self, *args: Any, **kwargs: Any) -> None:
        raise ExecutionDisabledError(
            "LIVE order submission is hard-disabled. This engine supports PAPER fills only."
        )

    def resolve_fee_model(
        self,
        commission_payload: dict[str, Any],
        *,
        liquidity: str = "taker",
        bnb_balance_sufficient: bool = False,
        bnb_discount_requested: bool = True,
    ) -> FeeModel:
        """Resolve the rate from Binance's signed account commission response.

        BNB discount is applied only when Binance says it is enabled for both
        account and symbol AND the caller has independently verified sufficient
        free BNB for the estimated fee. No local setting alone can activate it.
        """
        standard = commission_payload.get("standardCommission")
        if not isinstance(standard, dict):
            raise ValueError("Missing standardCommission in Binance commission response.")
        maker = Decimal(str(standard["maker"]))
        taker = Decimal(str(standard["taker"]))
        if not (ZERO <= maker < Decimal(1) and ZERO <= taker < Decimal(1)):
            raise ValueError("Binance returned an invalid commission rate.")
        discount = commission_payload.get("discount", {})
        exchange_eligible = (
            bool(discount.get("enabledForAccount"))
            and bool(discount.get("enabledForSymbol"))
            and str(discount.get("discountAsset", "")).upper() == self.bnb_asset
            and Decimal(str(discount.get("discount", "0"))) > ZERO
        )
        applied = bool(bnb_discount_requested and exchange_eligible and bnb_balance_sufficient)
        selected = taker if liquidity.lower() == "taker" else maker
        if applied:
            selected *= Decimal(1) - Decimal(str(discount["discount"]))
        fee_asset = self.bnb_asset if applied else self.quote_asset
        return FeeModel(
            maker_rate=maker,
            taker_rate=taker,
            selected_rate=selected,
            fee_asset=fee_asset,
            bnb_discount_requested=bnb_discount_requested,
            bnb_discount_exchange_eligible=exchange_eligible,
            bnb_balance_confirmed=bnb_balance_sufficient,
            bnb_discount_applied=applied,
        )

    def check_round_trip_profitability(
        self,
        *,
        entry_price: Decimal,
        target_exit_price: Decimal,
        buy_fee_rate: Decimal,
        sell_fee_rate: Decimal,
        spread_bps: Decimal,
        slippage_bps: Decimal | None = None,
        safety_margin_bps: Decimal = Decimal("10"),
    ) -> ProfitabilityCheck:
        values = [entry_price, target_exit_price, buy_fee_rate, sell_fee_rate, spread_bps, safety_margin_bps]
        if any(Decimal(value) < ZERO for value in values) or Decimal(entry_price) <= ZERO:
            raise ValueError("Prices and costs must be non-negative; entry price must be positive.")
        entry = Decimal(entry_price)
        exit_price = Decimal(target_exit_price)
        gross_edge = (exit_price / entry - Decimal(1)) * BPS
        slip = self.slippage_bps if slippage_bps is None else Decimal(slippage_bps)
        # Conservative round trip: pay spread once in full and slippage on both legs.
        costs = (
            Decimal(buy_fee_rate) * BPS
            + Decimal(sell_fee_rate) * BPS
            + Decimal(spread_bps)
            + Decimal(2) * slip
        )
        net = gross_edge - costs
        eligible = net > Decimal(safety_margin_bps)
        reason = (
            "Expected edge clears estimated fees, spread, slippage and safety margin."
            if eligible
            else "Rejected: expected edge does not clear estimated round-trip costs and safety margin."
        )
        return ProfitabilityCheck(eligible, gross_edge, costs, net, reason)

    async def submit_paper_order_with_account_fees(
        self,
        client: Any,
        *,
        symbol: str,
        side: OrderSide | str,
        quantity: Decimal,
        reference_price: Decimal,
        quantity_step: Decimal = Decimal("0.00000001"),
        min_notional: Decimal = Decimal("5"),
        liquidity: str = "taker",
        bnb_discount_requested: bool = True,
    ) -> PaperOrder:
        """Fetch real account commission and balances, then create a PAPER fill.

        Requires a Binance API key with read-only account permissions. It never
        submits an exchange order. If commission data cannot be read, it fails
        closed rather than silently assuming a fee tier.
        """
        settings = getattr(client, "settings", None)
        if settings is None or getattr(settings.TRADING_ENV, "value", None) != "TESTNET":
            raise RuntimeError("Account-fee paper execution requires Binance Spot TESTNET.")
        if getattr(settings.TRADING_MODE, "value", None) != "PAPER":
            raise RuntimeError("Account-fee paper execution requires TRADING_MODE=PAPER.")

        commission_payload = await client.account_commission(symbol)
        account_payload = await client.account()
        balances = {
            str(item.get("asset", "")).upper(): Decimal(str(item.get("free", "0")))
            + Decimal(str(item.get("locked", "0")))
            for item in account_payload.get("balances", [])
        }
        bnb_available = balances.get(self.bnb_asset, ZERO)
        bnb_price: Decimal | None = None
        if bnb_discount_requested:
            try:
                bnb_ticker = await client.ticker_24h(f"{self.bnb_asset}{self.quote_asset}")
                bnb_price = Decimal(str(bnb_ticker["lastPrice"]))
                if not bnb_price.is_finite() or bnb_price <= ZERO:
                    bnb_price = None
            except (KeyError, TypeError, ValueError, RuntimeError):
                bnb_price = None

        standard = commission_payload.get("standardCommission", {})
        discount = commission_payload.get("discount", {})
        base_rate = Decimal(str(standard.get("taker" if liquidity.lower() == "taker" else "maker", "0")))
        discount_rate = Decimal(str(discount.get("discount", "0")))
        exchange_eligible = (
            bool(discount.get("enabledForAccount"))
            and bool(discount.get("enabledForSymbol"))
            and str(discount.get("discountAsset", "")).upper() == self.bnb_asset
            and discount_rate > ZERO
        )
        notional = Decimal(quantity) * Decimal(reference_price)
        required_bnb = ZERO
        if exchange_eligible and bnb_price is not None:
            discounted_fee_quote = notional * base_rate * (Decimal(1) - discount_rate)
            required_bnb = discounted_fee_quote / bnb_price
        bnb_sufficient = (
            exchange_eligible
            and bnb_price is not None
            and required_bnb > ZERO
            and bnb_available >= required_bnb
        )
        return await self.submit_paper_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
            quantity_step=quantity_step,
            min_notional=min_notional,
            commission_payload=commission_payload,
            liquidity=liquidity,
            bnb_balance_sufficient=bnb_sufficient,
            bnb_discount_requested=bnb_discount_requested,
        )

    async def submit_paper_order(
        self,
        *,
        symbol: str,
        side: OrderSide | str,
        quantity: Decimal,
        reference_price: Decimal,
        quantity_step: Decimal = Decimal("0.00000001"),
        min_notional: Decimal = Decimal("5"),
        commission_payload: dict[str, Any] | None = None,
        liquidity: str = "taker",
        bnb_balance_sufficient: bool = False,
        bnb_discount_requested: bool = True,
        fee_rate_override: Decimal | None = None,
    ) -> PaperOrder:
        side = OrderSide(side)
        quantity = Decimal(quantity)
        reference_price = Decimal(reference_price)
        quantity_step = Decimal(quantity_step)
        min_notional = Decimal(min_notional)
        if not symbol or not symbol.isalnum():
            raise ValueError("Invalid symbol")
        if quantity <= ZERO or reference_price <= ZERO or quantity_step <= ZERO:
            raise ValueError("Quantity, price and quantity step must be positive")
        if min_notional < ZERO:
            raise ValueError("Minimum notional cannot be negative")

        fee_model: FeeModel | None = None
        if commission_payload is not None:
            fee_model = self.resolve_fee_model(
                commission_payload,
                liquidity=liquidity,
                bnb_balance_sufficient=bnb_balance_sufficient,
                bnb_discount_requested=bnb_discount_requested,
            )
            effective_fee_rate = fee_model.selected_rate
        elif fee_rate_override is not None:
            effective_fee_rate = Decimal(fee_rate_override)
        else:
            if self.require_verified_commission:
                raise RuntimeError(
                    "No verified Binance commission payload supplied; refusing PAPER fill."
                )
            effective_fee_rate = self.fee_rate
        if not ZERO <= effective_fee_rate < Decimal(1):
            raise ValueError("Effective fee rate must be in [0, 1).")

        rounded_quantity = (
            (quantity / quantity_step).to_integral_value(rounding=ROUND_DOWN)
            * quantity_step
        )
        if rounded_quantity <= ZERO:
            raise ValueError("Quantity is below the permitted step size")
        reference_notional = rounded_quantity * reference_price
        if reference_notional < min_notional:
            raise ValueError("Order is below the configured minimum notional")

        slippage = self.slippage_bps / BPS
        fill_price = reference_price * (
            Decimal(1) + slippage if side is OrderSide.BUY else Decimal(1) - slippage
        )
        filled_gross = rounded_quantity * fill_price
        fee = (filled_gross * effective_fee_rate).quantize(
            Decimal("0.00000001"), rounding=ROUND_CEILING
        )
        net_flow = filled_gross + fee if side is OrderSide.BUY else filled_gross - fee

        order = PaperOrder(
            order_id=str(uuid4()),
            symbol=symbol.upper(),
            side=side,
            quantity=rounded_quantity,
            reference_price=reference_price,
            fill_price=fill_price,
            gross_quote=filled_gross,
            fee_quote=fee,
            net_quote_flow=net_flow,
            fee_rate=effective_fee_rate,
            fee_asset=fee_model.fee_asset if fee_model else self.quote_asset,
            fee_source=fee_model.source if fee_model else (
                "EXPLICIT_OVERRIDE" if fee_rate_override is not None else "CONFIG_FALLBACK"
            ),
            bnb_discount_applied=fee_model.bnb_discount_applied if fee_model else False,
        )
        self._paper_orders[order.order_id] = order
        return order

    def get_paper_order(self, order_id: str) -> PaperOrder | None:
        return self._paper_orders.get(order_id)

    def list_paper_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(self._paper_orders.values())
