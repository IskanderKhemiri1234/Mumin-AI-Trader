from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
BPS = Decimal("10000")
STOP_PERCENT = Decimal("0.50")


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
    risk_reward_ratio: Decimal
    target_percent: Decimal
    atr_percent: Decimal
    mtf_confluence: bool
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


def _close_volatility_percent(values: list[Decimal], period: int = 14) -> Decimal:
    changes = [
        abs(values[index] / values[index - 1] - Decimal("1")) * Decimal("100")
        for index in range(max(1, len(values) - period), len(values))
    ]
    return sum(changes, ZERO) / Decimal(len(changes)) if changes else ZERO


def _timeframe_bullish(values: list[Decimal]) -> bool:
    if len(values) < 30 or any(not value.is_finite() or value <= ZERO for value in values):
        return False
    fast = _ema(values[-40:], 9)
    slow = _ema(values[-40:], 21)
    prior_fast = _ema(values[-41:-1], 9) if len(values) >= 41 else _ema(values[-40:-1], 9)
    return values[-1] > fast > slow and fast > prior_fast


class SmartSniperStrategy:
    """Explainable entry filter with cost-aware asymmetric targets and MTF confirmation.

    Scores are heuristic rankings, not calibrated probabilities. Missing higher
    timeframe data fails closed; portfolio risk and Paper execution remain separate.
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
        return (
            self.buy_fee_bps + self.sell_fee_bps
            + self.slippage_bps_per_side * Decimal("2") + Decimal("4")
        )

    def assess(
        self,
        closes: list[Decimal],
        spread_bps: Decimal = ZERO,
        *,
        higher_timeframe_closes: dict[str, list[Decimal]] | None = None,
        atr_percent: Decimal | None = None,
    ) -> StrategyAssessment:
        prices = [Decimal(value) for value in closes]
        fallback = (
            "HOLD", "UNKNOWN", "NONE", ZERO, ZERO, ZERO, ZERO, ZERO,
            Decimal("50"), self.estimated_cost_bps, ZERO, Decimal("0"),
            Decimal("0"), ZERO, False,
        )
        if len(prices) < 30 or any(not value.is_finite() or value <= ZERO for value in prices):
            return StrategyAssessment(*fallback, ("Insufficient valid closed-candle history.",))
        spread = Decimal(spread_bps)
        if not spread.is_finite() or spread < ZERO:
            raise ValueError("Spread must be a finite non-negative value.")
        mtf = higher_timeframe_closes or {}
        mtf_confluence = (
            _timeframe_bullish([Decimal(v) for v in mtf.get("higher_1", [])])
            and _timeframe_bullish([Decimal(v) for v in mtf.get("higher_2", [])])
        )
        volatility = (
            _close_volatility_percent(prices)
            if atr_percent is None else Decimal(atr_percent)
        )
        if not volatility.is_finite() or volatility < ZERO:
            raise ValueError("ATR/volatility must be finite and non-negative.")

        fast = _ema(prices[-40:], 9)
        slow = _ema(prices[-40:], 21)
        prior_fast = _ema(prices[-41:-1], 9) if len(prices) >= 41 else _ema(prices[-40:-1], 9)
        rsi = _rsi(prices)
        momentum_3 = (prices[-1] / prices[-4] - Decimal("1")) * BPS
        momentum_12 = (prices[-1] / prices[-13] - Decimal("1")) * BPS
        recovery_window = prices[-13:]
        peak_index = max(range(len(recovery_window)), key=recovery_window.__getitem__)
        peak = recovery_window[peak_index]
        post_peak = recovery_window[peak_index + 1:]
        if post_peak:
            trough = min(post_peak)
            drawdown = (trough / peak - Decimal("1")) * BPS
            rebound = (prices[-1] / trough - Decimal("1")) * BPS
        else:
            drawdown = rebound = ZERO
        trend_up = fast > slow and fast > prior_fast
        trend_down = fast < slow and fast < prior_fast
        regime = "BULL" if trend_up else ("BEAR" if trend_down else "RANGE")
        estimated_cost = self.estimated_cost_bps + spread
        reasons: list[str] = []

        if trend_down and momentum_3 < Decimal("-8") and rsi < Decimal("48"):
            return StrategyAssessment(
                "SELL", regime, "RISK_EXIT", Decimal("80"), momentum_3,
                momentum_12, drawdown, rebound, rsi, estimated_cost,
                momentum_12 - estimated_cost, Decimal("0"), Decimal("0"),
                volatility, mtf_confluence,
                ("Downtrend confirmed by EMA structure and negative short momentum.",),
            )

        momentum_score = ZERO
        if trend_up:
            momentum_score += Decimal("25")
            reasons.append("Local EMA trend and fast-EMA slope are positive.")
        if momentum_3 > Decimal("5"):
            momentum_score += Decimal("10")
            reasons.append("Short-horizon momentum is positive.")
        if Decimal("48") <= rsi <= Decimal("68"):
            momentum_score += Decimal("10")
            reasons.append("RSI confirms strength without an overbought reading.")
        if momentum_12 > estimated_cost + self.minimum_net_edge_bps:
            momentum_score += Decimal("20")
            reasons.append("12-candle move clears estimated round-trip costs and edge buffer.")
        if spread <= Decimal("12"):
            momentum_score += Decimal("10")
            reasons.append("Bid/ask spread is within the liquidity-quality limit.")
        if mtf_confluence:
            momentum_score += Decimal("25")
            reasons.append("5m and 15m trends confirm the local bullish setup.")
        else:
            reasons.append("Missing or non-confluent 5m/15m trend confirmation.")

        recovery_score = ZERO
        recovery_setup = drawdown <= Decimal("-150") and rebound >= Decimal("60")
        if recovery_setup:
            recovery_score += Decimal("20")
            reasons.append("A material pullback is followed by a measurable rebound.")
        if fast > prior_fast and prices[-1] > fast:
            recovery_score += Decimal("15")
            reasons.append("Price has reclaimed a rising fast EMA.")
        if momentum_3 > ZERO and momentum_12 > ZERO:
            recovery_score += Decimal("10")
            reasons.append("Both short and medium lookback momentum have turned positive.")
        if Decimal("35") <= rsi <= Decimal("62"):
            recovery_score += Decimal("10")
            reasons.append("Recovery RSI is constructive.")
        if momentum_12 > estimated_cost + self.minimum_net_edge_bps:
            recovery_score += Decimal("15")
            reasons.append("Recovery move clears estimated costs and edge buffer.")
        if spread <= Decimal("12"):
            recovery_score += Decimal("10")
            reasons.append("Spread passes the execution-quality filter.")
        if mtf_confluence:
            recovery_score += Decimal("20")
            reasons.append("Higher timeframes confirm the recovery direction.")

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

        # Stop remains the existing 0.50% risk limit. Target is selected at
        # 2.5R or 3R; higher score selects 3R. The existing account-level
        # daily-loss and drawdown guards remain independent.
        risk_reward = Decimal("2.5")
        target_percent = STOP_PERCENT * risk_reward
        target_bps = target_percent * Decimal("100")
        net_edge = expected_move - estimated_cost
        if volatility < Decimal("0.08") or volatility > Decimal("1.20"):
            eligible = False
            reasons.append("Volatility is outside the accepted ATR band.")
        if not mtf_confluence:
            eligible = False
        if spread > Decimal("12"):
            eligible = False
            reasons.append("Spread exceeds the hard execution-quality ceiling.")
        if expected_move < target_bps + estimated_cost + self.minimum_net_edge_bps:
            eligible = False
            reasons.append("Expected move does not cover the asymmetric target plus all estimated costs.")
        if chosen_score < self.minimum_score:
            eligible = False
            reasons.append("Quality score is below the configured acceptance threshold.")

        signal = "BUY" if eligible else "HOLD"
        if not eligible:
            reasons.append("Asymmetric Alpha Filter rejected the entry; remain in HOLD.")
        return StrategyAssessment(
            signal, regime, chosen_strategy, chosen_score, momentum_3,
            momentum_12, drawdown, rebound, rsi, estimated_cost, net_edge,
            risk_reward, target_percent, volatility, mtf_confluence,
            tuple(reasons) or ("No actionable signal.",),
        )
