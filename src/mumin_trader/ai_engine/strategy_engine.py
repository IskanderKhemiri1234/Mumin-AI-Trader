from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CORE_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
)

ZERO = Decimal("0")
BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    approved: bool
    symbol: str
    score: int
    reason: str
    stop_loss_price: Decimal | None
    take_profit_price: Decimal | None


class StrategyEngine:
    """Deterministic long-only candidate policy; it has no exchange authority."""

    def __init__(
        self,
        *,
        minimum_score: int = 75,
        minimum_quote_volume: Decimal = Decimal("1000000"),
        maximum_spread_bps: Decimal = Decimal("12"),
        minimum_momentum_bps: Decimal = Decimal("5"),
        stop_loss_percent: Decimal = Decimal("0.50"),
        take_profit_percent: Decimal = Decimal("1.25"),
    ) -> None:
        if not 1 <= minimum_score <= 100:
            raise ValueError("minimum_score must be between 1 and 100.")
        if stop_loss_percent != Decimal("0.50"):
            raise ValueError("The fixed research stop loss is 0.50%.")
        if not Decimal("1.25") <= take_profit_percent <= Decimal("1.50"):
            raise ValueError("take_profit_percent must be between 1.25% and 1.50%.")
        if minimum_quote_volume <= ZERO or maximum_spread_bps <= ZERO:
            raise ValueError("Liquidity limits must be positive.")
        self.minimum_score = minimum_score
        self.minimum_quote_volume = Decimal(minimum_quote_volume)
        self.maximum_spread_bps = Decimal(maximum_spread_bps)
        self.minimum_momentum_bps = Decimal(minimum_momentum_bps)
        self.stop_loss_percent = Decimal(stop_loss_percent)
        self.take_profit_percent = Decimal(take_profit_percent)

    def evaluate(
        self,
        *,
        symbol: str,
        entry_price: Decimal,
        spread_bps: Decimal,
        quote_volume: Decimal,
        rsi: Decimal | None,
        momentum_bps: Decimal,
        timeframe_trends: dict[str, bool],
    ) -> StrategyDecision:
        symbol = symbol.upper()
        entry = Decimal(entry_price)
        spread = Decimal(spread_bps)
        volume = Decimal(quote_volume)
        momentum = Decimal(momentum_bps)

        def reject(reason: str, score: int = 0) -> StrategyDecision:
            return StrategyDecision(False, symbol, score, reason, None, None)

        if symbol not in CORE_SYMBOLS:
            return reject("Symbol is outside the fixed eight-asset universe.")
        if entry <= ZERO or spread < ZERO or volume < ZERO:
            return reject("Invalid market input.")
        if spread > self.maximum_spread_bps:
            return reject("Liquidity Shield: spread exceeds the configured ceiling.")
        if volume < self.minimum_quote_volume:
            return reject("Liquidity Shield: 24h quote volume is below the floor.")
        if rsi is None or not Decimal("45") <= Decimal(rsi) <= Decimal("68"):
            return reject("RSI gate rejected the candidate.")
        if momentum <= self.minimum_momentum_bps:
            return reject("Momentum gate rejected the candidate.")

        required_frames = ("fast", "15m", "1h")
        if any(frame not in timeframe_trends for frame in required_frames):
            return reject("MTF gate rejected: one or more timeframes are missing.")
        if not all(timeframe_trends[frame] for frame in required_frames):
            return reject("MTF gate rejected: trend alignment is not bullish.")

        score = 30
        score += 20 if momentum >= Decimal("30") else 12 if momentum >= Decimal("15") else 8
        score += 15 if Decimal("50") <= Decimal(rsi) <= Decimal("62") else 10
        score += 15 if volume >= Decimal("5000000") else 10 if volume >= Decimal("2000000") else 7
        score += 10 if spread <= Decimal("5") else 7 if spread <= Decimal("8") else 5
        score += 10

        if score < self.minimum_score:
            return reject(f"Score {score} is below the required floor {self.minimum_score}.", score)

        stop = entry * (Decimal("1") - self.stop_loss_percent / Decimal("100"))
        target = entry * (Decimal("1") + self.take_profit_percent / Decimal("100"))
        return StrategyDecision(
            True,
            symbol,
            score,
            "Approved: Score, liquidity Shield, MTF, RSI and momentum gates passed.",
            stop,
            target,
        )
