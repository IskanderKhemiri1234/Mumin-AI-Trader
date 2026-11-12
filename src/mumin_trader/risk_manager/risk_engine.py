from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    quantity_quote: Decimal
    risk_budget: Decimal
    reason: str


class RiskEngine:
    """Deterministic pre-trade risk gate. AI cannot override this class."""

    def __init__(self, max_trade_allocation: Decimal, min_trade_allocation: Decimal,
                 risk_per_trade_percent: Decimal, max_daily_loss_percent: Decimal,
                 reserve_percent: Decimal = Decimal("20"), max_open_positions: int = 2):
        self.max_trade_allocation = max_trade_allocation
        self.min_trade_allocation = min_trade_allocation
        self.risk_per_trade_percent = risk_per_trade_percent
        self.max_daily_loss_percent = max_daily_loss_percent
        self.reserve_percent = reserve_percent
        self.max_open_positions = max_open_positions

    def evaluate(self, equity: Decimal, available_quote_balance: Decimal,
                 stop_distance_percent: Decimal, daily_loss: Decimal = Decimal("0"),
                 open_positions: int = 0, estimated_round_trip_fee_percent: Decimal = Decimal("0"),
                 estimated_slippage_percent: Decimal = Decimal("0")) -> RiskDecision:
        values = (equity, available_quote_balance, stop_distance_percent, daily_loss,
                  estimated_round_trip_fee_percent, estimated_slippage_percent)
        if any(v < 0 for v in values):
            return RiskDecision(False, Decimal("0"), Decimal("0"), "Negative input rejected.")
        if equity <= 0 or available_quote_balance <= 0:
            return RiskDecision(False, Decimal("0"), Decimal("0"), "No available capital.")
        if stop_distance_percent <= 0:
            return RiskDecision(False, Decimal("0"), Decimal("0"), "A valid stop distance is required.")
        if open_positions >= self.max_open_positions:
            return RiskDecision(False, Decimal("0"), Decimal("0"), "Maximum open positions reached.")
        daily_limit = equity * self.max_daily_loss_percent / Decimal("100")
        if daily_loss >= daily_limit:
            return RiskDecision(False, Decimal("0"), Decimal("0"), "Daily loss limit reached.")
        risk_budget = equity * self.risk_per_trade_percent / Decimal("100")
        total_cost_percent = stop_distance_percent + estimated_round_trip_fee_percent + estimated_slippage_percent
        if total_cost_percent <= 0:
            return RiskDecision(False, Decimal("0"), risk_budget, "Invalid total risk estimate.")
        risk_sized_notional = risk_budget * Decimal("100") / total_cost_percent
        reserve_adjusted_balance = available_quote_balance * (Decimal("100") - self.reserve_percent) / Decimal("100")
        allocation = min(risk_sized_notional, self.max_trade_allocation, reserve_adjusted_balance).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        if allocation < self.min_trade_allocation:
            return RiskDecision(False, Decimal("0"), risk_budget, "Calculated position is below minimum configured trade allocation.")
        return RiskDecision(True, allocation, risk_budget, "Approved by deterministic risk checks.")
