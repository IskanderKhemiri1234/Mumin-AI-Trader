from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class StrategyAssessment:
    signal: str
    regime: str
    strategy: str
    score: Decimal
    momentum_3_bps: Decimal
    momentum_12_bps: Decimal
    drawdown_12_bps: Decimal
    rebound_bps: Decimal
    rsi: Decimal
    estimated_cost_bps: Decimal
    expected_edge_bps: Decimal
    reasons: tuple[str, ...]


def _ema(values: list[Decimal], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError("Insufficient observations for EMA.")
    alpha = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], ZERO) / Decimal(period)
    for value in values[period:]:
        result += alpha * (value - result)
    return result


def _rsi(values: list[Decimal], period: int = 14) -> Decimal:
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    recent = changes[-period:]
    gains = sum((max(change, ZERO) for change in recent), ZERO) / Decimal(period)
    losses = sum((max(-change, ZERO) for change in recent), ZERO) / Decimal(period)
    if losses == ZERO:
        return Decimal("100") if gains > ZERO else Decimal("50")
    return Decimal("100") - Decimal("100") / (Decimal(1) + gains / losses)


class SmartSniperStrategy:
    """Deterministic, explainable dual-regime strategy research engine.

    Scores are heuristic rankings, not calibrated probabilities. No execution
    authority is granted here; portfolio risk and Paper execution remain separate.
    """

    def __init__(
        self,
        *,
        buy_fee_bps: Decimal = Decimal("10"),
        sell_fee_bps: Decimal = Decimal("10"),
        slippage_bps_per_side: Decimal = Decimal("2"),
        minimum_net_edge_bps: Decimal = Decimal("10"),
        minimum_score: Decimal = Decimal("75"),
    ) -> None:
        self.buy_fee_bps = Decimal(buy_fee_bps)
        self.sell_fee_bps = Decimal(sell_fee_bps)
        self.slippage_bps_per_side = Decimal(slippage_bps_per_side)
        self.minimum_net_edge_bps = Decimal(minimum_net_edge_bps)
        self.minimum_score = Decimal(minimum_score)
        if any(value < ZERO for value in (
            self.buy_fee_bps, self.sell_fee_bps, self.slippage_bps_per_side,
            self.minimum_net_edge_bps, self.minimum_score
        )):
            raise ValueError("Strategy cost and score thresholds must be non-negative.")
        if self.minimum_score > Decimal("100"):
            raise ValueError("minimum_score cannot exceed 100.")

    @property
    def estimated_cost_bps(self) -> Decimal:
        # Conservative estimate includes both fees, two-sided slippage and spread allowance.
        return (
            self.buy_fee_bps + self.sell_fee_bps
            + self.slippage_bps_per_side * Decimal("2") + Decimal("4")
        )

    def assess(self, closes: list[Decimal], spread_bps: Decimal = ZERO) -> StrategyAssessment:
        prices = [Decimal(value) for value in closes]
        if len(prices) < 30 or any(not value.is_finite() or value <= ZERO for value in prices):
            return StrategyAssessment(
                "HOLD", "UNKNOWN", "NONE", ZERO, ZERO, ZERO, ZERO, ZERO,
                Decimal("50"), self.estimated_cost_bps, ZERO,
                ("Insufficient valid closed-candle history.",),
            )
        spread = Decimal(spread_bps)
        if not spread.is_finite() or spread < ZERO:
            raise ValueError("Spread must be a finite non-negative value.")

        fast = _ema(prices[-40:], 9)
        slow = _ema(prices[-40:], 21)
        prior_fast = _ema(prices[-41:-1], 9) if len(prices) >= 41 else _ema(prices[-40:-1], 9)
        rsi = _rsi(prices)
        momentum_3 = (prices[-1] / prices[-4] - Decimal("1")) * BPS
        momentum_12 = (prices[-1] / prices[-13] - Decimal("1")) * BPS
        peak = max(prices[-13:])
        trough = min(prices[-13:])
        drawdown = (trough / peak - Decimal("1")) * BPS
        rebound = (prices[-1] / trough - Decimal("1")) * BPS if trough > ZERO else ZERO
        trend_up = fast > slow and fast > prior_fast
        trend_down = fast < slow and fast < prior_fast
        regime = "BULL" if trend_up else ("BEAR" if trend_down else "RANGE")
        estimated_cost = self.estimated_cost_bps + spread
        reasons: list[str] = []

        # Existing positions may be exited on a confirmed weakening regime.
        if trend_down and momentum_3 < Decimal("-8") and rsi < Decimal("48"):
            return StrategyAssessment(
                "SELL", regime, "RISK_EXIT", Decimal("80"), momentum_3,
                momentum_12, drawdown, rebound, rsi, estimated_cost,
                momentum_12 - estimated_cost,
                ("Downtrend confirmed by EMA structure and negative short momentum.",),
            )

        momentum_score = ZERO
        if trend_up:
            momentum_score += Decimal("30")
            reasons.append("EMA trend and fast-EMA slope are positive.")
        if momentum_3 > Decimal("5"):
            momentum_score += Decimal("15")
            reasons.append("Short-horizon momentum is positive.")
        if Decimal("48") <= rsi <= Decimal("68"):
            momentum_score += Decimal("15")
            reasons.append("RSI confirms strength without an overbought reading.")
        if momentum_12 > estimated_cost + self.minimum_net_edge_bps:
            momentum_score += Decimal("25")
            reasons.append("12-candle move clears estimated round-trip costs and edge buffer.")
        if spread <= Decimal("12"):
            momentum_score += Decimal("15")
            reasons.append("Bid/ask spread is within the liquidity-quality limit.")

        recovery_score = ZERO
        recovery_setup = drawdown <= Decimal("-150") and rebound >= Decimal("60")
        if recovery_setup:
            recovery_score += Decimal("25")
            reasons.append("A material pullback is followed by a measurable rebound.")
        if fast > prior_fast and prices[-1] > fast:
            recovery_score += Decimal("20")
            reasons.append("Price has reclaimed a rising fast EMA.")
        if momentum_3 > ZERO and momentum_12 > ZERO:
            recovery_score += Decimal("15")
            reasons.append("Both short and medium lookback momentum have turned positive.")
        if Decimal("35") <= rsi <= Decimal("62"):
            recovery_score += Decimal("15")
            reasons.append("Recovery RSI is constructive without chasing extreme strength.")
        if momentum_12 > estimated_cost + self.minimum_net_edge_bps:
            recovery_score += Decimal("15")
            reasons.append("Recovery move clears estimated costs and the edge buffer.")
        if spread <= Decimal("12"):
            recovery_score += Decimal("10")
            reasons.append("Spread passes the execution-quality filter.")

        if momentum_score >= recovery_score:
            chosen_score, chosen_strategy = momentum_score, "MOMENTUM"
            expected_move = momentum_12
            eligible = (
                trend_up and momentum_3 > Decimal("5")
                and Decimal("48") <= rsi <= Decimal("68")
            )
        else:
            chosen_score, chosen_strategy = recovery_score, "RECOVERY"
            expected_move = min(rebound, momentum_12)
            eligible = recovery_setup and fast > prior_fast and prices[-1] > fast

        net_edge = expected_move - estimated_cost
        if not eligible:
            reasons.append("No complete entry setup; remain in HOLD.")
        if net_edge <= self.minimum_net_edge_bps:
            reasons.append("Expected move does not clear costs plus the required net-edge buffer.")
        if chosen_score < self.minimum_score:
            reasons.append("Quality score is below the configured acceptance threshold.")

        signal = "BUY" if (
            eligible and net_edge > self.minimum_net_edge_bps
            and chosen_score >= self.minimum_score
        ) else "HOLD"
        if not reasons:
            reasons.append("No actionable signal.")
        return StrategyAssessment(
            signal, regime, chosen_strategy, chosen_score, momentum_3,
            momentum_12, drawdown, rebound, rsi, estimated_cost, net_edge,
            tuple(reasons),
        )
