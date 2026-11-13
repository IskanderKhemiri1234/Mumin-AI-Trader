from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class CompoundingAssessment:
    eligible: bool
    reinvestment_fraction: Decimal
    realized_profit_pool: Decimal
    reinvestment_budget: Decimal
    volatility_multiplier: Decimal
    reason: str


class DynamicCompoundingMatrix:
    """Profit-only reinvestment, gated by trend, liquidity and ATR regime."""

    def assess(
        self,
        *,
        realized_net_pnl: Decimal,
        momentum_score: Decimal,
        drawdown_percent: Decimal,
        atr_percent: Decimal | None = None,
        trend_clear: bool = False,
        liquidity_high: bool = False,
        maximum_fraction: Decimal = Decimal("0.30"),
    ) -> CompoundingAssessment:
        pnl = Decimal(realized_net_pnl)
        score = Decimal(momentum_score)
        drawdown = Decimal(drawdown_percent)
        maximum = Decimal(maximum_fraction)
        if atr_percent is None:
            atr = None
        else:
            atr = Decimal(atr_percent)
        values = [pnl, score, drawdown, maximum]
        if atr is not None:
            values.append(atr)
        if any(not value.is_finite() for value in values):
            raise ValueError("Compounding inputs must be finite.")
        if drawdown < ZERO or maximum < ZERO or maximum > Decimal("0.30"):
            raise ValueError("Invalid drawdown or compounding fraction.")

        profit_pool = max(ZERO, pnl)
        def paused(reason: str) -> CompoundingAssessment:
            return CompoundingAssessment(False, ZERO, profit_pool, ZERO, ZERO, reason)

        if drawdown >= Decimal("1.0"):
            return paused("Compounding paused while portfolio drawdown is at least 1%.")
        if profit_pool <= ZERO:
            return paused("No reinvestment: no cumulative realized net profit exists.")
        if score < Decimal("75"):
            return paused("No reinvestment: momentum score is below threshold.")
        if not trend_clear:
            return paused("No reinvestment: higher-timeframe trend is not clear.")
        if not liquidity_high:
            return paused("No reinvestment: liquidity quality is below the required threshold.")
        if atr is None or atr < Decimal("0.15") or atr > Decimal("1.20"):
            return paused("No reinvestment: ATR is outside the accepted volatility regime.")

        if score < Decimal("85"):
            momentum_fraction = Decimal("0.10")
        elif score < Decimal("93"):
            momentum_fraction = Decimal("0.20")
        else:
            momentum_fraction = maximum

        if atr <= Decimal("0.35"):
            volatility_multiplier = Decimal("1.00")
        elif atr <= Decimal("0.75"):
            volatility_multiplier = Decimal("0.75")
        else:
            volatility_multiplier = Decimal("0.40")

        fraction = min(maximum, momentum_fraction) * volatility_multiplier
        budget = (profit_pool * fraction).quantize(Decimal("0.00000001"))
        return CompoundingAssessment(
            budget > ZERO, fraction, profit_pool, budget, volatility_multiplier,
            "Reinvestment uses realized net profit, clear trend, liquid market and ATR-adjusted sizing.",
        )
