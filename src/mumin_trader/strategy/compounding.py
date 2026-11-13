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
    reason: str


class DynamicCompoundingMatrix:
    """Profit-only reinvestment governor.

    Unrealized gains and the original principal are excluded from the profit
    pool. The returned amount is only a ceiling; execution must still enforce
    independent per-trade, exposure, reserve and drawdown limits.
    """

    def assess(
        self,
        *,
        realized_net_pnl: Decimal,
        momentum_score: Decimal,
        drawdown_percent: Decimal,
        maximum_fraction: Decimal = Decimal("0.30"),
    ) -> CompoundingAssessment:
        pnl = Decimal(realized_net_pnl)
        score = Decimal(momentum_score)
        drawdown = Decimal(drawdown_percent)
        maximum = Decimal(maximum_fraction)
        if any(not value.is_finite() for value in (pnl, score, drawdown, maximum)):
            raise ValueError("Compounding inputs must be finite.")
        if drawdown < ZERO or maximum < ZERO or maximum > Decimal("0.30"):
            raise ValueError("Invalid drawdown or compounding fraction.")

        profit_pool = max(ZERO, pnl)
        if drawdown >= Decimal("1.0"):
            return CompoundingAssessment(
                False, ZERO, profit_pool, ZERO,
                "Compounding paused while portfolio drawdown is at least 1%.",
            )
        if score < Decimal("75") or profit_pool <= ZERO:
            return CompoundingAssessment(
                False, ZERO, profit_pool, ZERO,
                "No reinvestment: score is below threshold or no realized net profit exists.",
            )

        if score < Decimal("85"):
            fraction = min(maximum, Decimal("0.10"))
        elif score < Decimal("93"):
            fraction = min(maximum, Decimal("0.20"))
        else:
            fraction = maximum

        budget = (profit_pool * fraction).quantize(Decimal("0.00000001"))
        return CompoundingAssessment(
            budget > ZERO, fraction, profit_pool, budget,
            "Reinvestment budget is derived only from cumulative realized net profit.",
        )
