from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from uuid import uuid4


class ExecutionDisabledError(RuntimeError):
    pass


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


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


class ExecutionEngine:
    """Paper-only simulator. Live order submission is hard-disabled."""

    def __init__(self, fee_rate: Decimal = Decimal("0.001"), slippage_bps: Decimal = Decimal("5")):
        if not Decimal(0) <= fee_rate < Decimal(1):
            raise ValueError("fee_rate must be in [0, 1)")
        if not Decimal(0) <= slippage_bps < Decimal("10000"):
            raise ValueError("slippage_bps must be in [0, 10000)")
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps
        self._paper_orders: dict[str, PaperOrder] = {}

    async def submit_order(self, *args, **kwargs):
        raise ExecutionDisabledError("LIVE order submission is disabled. Use paper simulation.")

    async def submit_paper_order(
        self, *, symbol: str, side: OrderSide | str, quantity: Decimal,
        reference_price: Decimal, quantity_step: Decimal = Decimal("0.00000001"),
        min_notional: Decimal = Decimal("5"),
    ) -> PaperOrder:
        side = OrderSide(side)
        quantity = Decimal(quantity)
        reference_price = Decimal(reference_price)
        quantity_step = Decimal(quantity_step)
        min_notional = Decimal(min_notional)
        if not symbol or not symbol.isalnum():
            raise ValueError("Invalid symbol")
        if quantity <= 0 or reference_price <= 0 or quantity_step <= 0:
            raise ValueError("Quantity, price and quantity step must be positive")
        rounded_quantity = (quantity / quantity_step).to_integral_value(rounding=ROUND_DOWN) * quantity_step
        if rounded_quantity <= 0:
            raise ValueError("Quantity is below the permitted step size")
        gross = rounded_quantity * reference_price
        if gross < min_notional:
            raise ValueError("Order is below the configured minimum notional")
        slippage = self.slippage_bps / Decimal("10000")
        fill_price = reference_price * (Decimal(1) + slippage if side is OrderSide.BUY else Decimal(1) - slippage)
        filled_gross = rounded_quantity * fill_price
        fee = filled_gross * self.fee_rate
        net_flow = filled_gross + fee if side is OrderSide.BUY else filled_gross - fee
        order = PaperOrder(str(uuid4()), symbol.upper(), side, rounded_quantity, reference_price, fill_price, filled_gross, fee, net_flow)
        self._paper_orders[order.order_id] = order
        return order

    def get_paper_order(self, order_id: str) -> PaperOrder | None:
        return self._paper_orders.get(order_id)
