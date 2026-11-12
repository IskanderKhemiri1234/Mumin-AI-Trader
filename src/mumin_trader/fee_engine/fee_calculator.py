from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Any

ZERO = Decimal("0")


@dataclass(frozen=True)
class FeeQuote:
    symbol: str
    side: str
    notional: Decimal
    fee_rate: Decimal
    fee_amount_quote: Decimal
    fee_asset: str
    bnb_discount_requested: bool
    bnb_discount_confirmed: bool
    net_pnl_quote: Decimal | None = None


class FeeCalculator:
    """Fee estimator. BNB discount is applied only when exchange flags and balance confirm it."""

    def __init__(self, quote_asset: str = "USDT", bnb_asset: str = "BNB"):
        self.quote_asset = quote_asset.upper()
        self.bnb_asset = bnb_asset.upper()

    @staticmethod
    def _d(value: Any) -> Decimal:
        return Decimal(str(value))

    def commission_rate(self, commission_payload: dict[str, Any], liquidity: str = "taker") -> Decimal:
        section = commission_payload.get("standardCommission")
        if not isinstance(section, dict):
            raise ValueError("Missing standardCommission in exchange response.")
        rate = self._d(section["taker" if liquidity.lower() == "taker" else "maker"])
        discount = commission_payload.get("discount", {})
        if (bool(discount.get("enabledForAccount")) and bool(discount.get("enabledForSymbol"))
                and str(discount.get("discountAsset", "")).upper() == self.bnb_asset
                and self._d(discount.get("discount", "0")) > ZERO):
            rate *= Decimal("1") - self._d(discount["discount"])
        return rate

    def estimate(self, symbol: str, side: str, notional: Decimal,
                 commission_payload: dict[str, Any], liquidity: str = "taker",
                 bnb_discount_requested: bool = True, bnb_balance_sufficient: bool = False) -> FeeQuote:
        if notional <= ZERO:
            raise ValueError("Notional must be positive.")
        discount = commission_payload.get("discount", {})
        confirmed = (bool(discount.get("enabledForAccount")) and bool(discount.get("enabledForSymbol"))
                     and str(discount.get("discountAsset", "")).upper() == self.bnb_asset
                     and self._d(discount.get("discount", "0")) > ZERO)
        applied = bnb_discount_requested and confirmed and bnb_balance_sufficient
        key = "taker" if liquidity.lower() == "taker" else "maker"
        rate = self.commission_rate(commission_payload, liquidity) if applied else self._d(commission_payload["standardCommission"][key])
        fee = (notional * rate).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
        return FeeQuote(symbol.upper(), side.upper(), notional, rate, fee, self.quote_asset, bnb_discount_requested, applied)

    def round_trip_cost(self, buy_notional: Decimal, sell_notional: Decimal,
                        buy_rate: Decimal, sell_rate: Decimal) -> Decimal:
        if min(buy_notional, sell_notional, buy_rate, sell_rate) < ZERO:
            raise ValueError("Amounts and rates cannot be negative.")
        return buy_notional * buy_rate + sell_notional * sell_rate

    def net_pnl(self, entry_notional: Decimal, exit_notional: Decimal,
                entry_fee: Decimal, exit_fee: Decimal, other_costs: Decimal = ZERO) -> Decimal:
        return exit_notional - entry_notional - entry_fee - exit_fee - other_costs
