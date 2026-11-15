"""Deterministic, auditable Paper-only strategy agents.

These agents do not learn from unvalidated outcomes or place exchange orders.
They expose independent votes so the engine can fail closed on disagreement.
"""
from __future__ import annotations

from decimal import Decimal


class MomentumAgent:
    name = "momentum"

    def evaluate(self, momentum_bps: Decimal) -> str:
        if momentum_bps >= Decimal("8"):
            return "BUY"
        if momentum_bps <= Decimal("-8"):
            return "SELL"
        return "HOLD"


class LiquidityAgent:
    name = "liquidity"

    def evaluate(self, bid: Decimal, ask: Decimal, max_spread_bps: Decimal) -> bool:
        bid, ask = Decimal(bid), Decimal(ask)
        if bid <= 0 or ask < bid:
            return False
        midpoint = (bid + ask) / Decimal("2")
        spread_bps = (ask - bid) / midpoint * Decimal("10000")
        return spread_bps <= Decimal(max_spread_bps)


class SignalFilterAgent:
    name = "signal_filter"

    def evaluate(self, momentum_vote: str, liquidity_ok: bool) -> str:
        if momentum_vote == "SELL":
            return "SELL"
        if momentum_vote == "BUY" and liquidity_ok:
            return "BUY"
        return "HOLD"


class PaperSwarm:
    """Three-agent deterministic consensus; no external model or hidden state."""

    def __init__(self) -> None:
        self.momentum = MomentumAgent()
        self.liquidity = LiquidityAgent()
        self.filter = SignalFilterAgent()

    def evaluate(
        self,
        momentum_bps: Decimal,
        bid: Decimal,
        ask: Decimal,
        max_spread_bps: Decimal,
    ) -> tuple[str, str, bool]:
        momentum_vote = self.momentum.evaluate(Decimal(momentum_bps))
        liquidity_ok = self.liquidity.evaluate(bid, ask, max_spread_bps)
        final_vote = self.filter.evaluate(momentum_vote, liquidity_ok)
        return final_vote, momentum_vote, liquidity_ok


def compound_allocation(
    equity: Decimal,
    cash: Decimal,
    reserve_cash: Decimal,
    *,
    initial_capital: Decimal = Decimal("70.00"),
    max_trade: Decimal = Decimal("5.00"),
) -> Decimal:
    """Reinvest Paper profits while preserving the 70-USDT deployed-capital ceiling.

    Position budget scales with current equity at the original 5/70 ratio and
    cannot consume the reserved cash balance. Portfolio exposure is capped by
    the engine's independent 70-USDT capital-allocation ceiling.
    """
    equity = max(Decimal("0"), Decimal(equity))
    cash_available = max(Decimal("0"), Decimal(cash) - Decimal(reserve_cash))
    if initial_capital <= 0:
        return Decimal("0")
    # max_trade is the base budget at the original 70-USDT equity. Realized
    # profits increase the percentage-based budget, while the caller's
    # portfolio exposure ceiling remains the hard limit on total deployed funds.
    proportional_budget = equity * Decimal(max_trade) / Decimal(initial_capital)
    return min(proportional_budget, cash_available)
