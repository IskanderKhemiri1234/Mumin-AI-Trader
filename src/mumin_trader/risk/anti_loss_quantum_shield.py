from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ShieldAssessment:
    mode: str
    block_entries: bool
    exit_fraction: Decimal
    reasons: tuple[str, ...]


class AntiLossQuantumShield:
    """Fail-closed liquidity and abnormal-flow guard for Paper execution.

    This deterministic risk filter is not a guarantee against losses. Missing
    liquidity measurements block new entries rather than assuming conditions
    are safe.
    """

    def assess(
        self,
        *,
        spread_bps: Decimal,
        quote_volume_ratio: Decimal | None,
        trade_count_ratio: Decimal | None,
        price_change_bps: Decimal,
        candle_age_ms: int,
    ) -> ShieldAssessment:
        spread = Decimal(spread_bps)
        move = Decimal(price_change_bps)
        if (
            not spread.is_finite() or spread < ZERO
            or not move.is_finite() or candle_age_ms < 0
        ):
            return ShieldAssessment(
                "LOCKDOWN", True, ZERO,
                ("Invalid market-quality measurement; fail-closed entry lock.",),
            )

        if quote_volume_ratio is None or trade_count_ratio is None:
            return ShieldAssessment(
                "DATA_GUARD", True, ZERO,
                ("Volume/trade-flow baseline unavailable; new entries are blocked.",),
            )

        volume = Decimal(quote_volume_ratio)
        trades = Decimal(trade_count_ratio)
        if (
            not volume.is_finite() or not trades.is_finite()
            or volume <= ZERO or trades <= ZERO
        ):
            return ShieldAssessment(
                "LOCKDOWN", True, ZERO,
                ("Invalid volume or trade-count ratio; fail-closed entry lock.",),
            )

        reasons: list[str] = []
        critical = False
        warnings = 0

        if spread >= Decimal("30"):
            critical = True
            reasons.append("Critical bid/ask spread expansion.")
        elif spread >= Decimal("12"):
            warnings += 1
            reasons.append("Bid/ask spread is materially wider than normal.")

        for label, ratio in (("quote volume", volume), ("trade count", trades)):
            if ratio <= Decimal("0.15") or ratio >= Decimal("6"):
                critical = True
                reasons.append(f"Critical {label} anomaly versus rolling baseline.")
            elif ratio <= Decimal("0.50") or ratio >= Decimal("3"):
                warnings += 1
                reasons.append(f"Abnormal {label} versus rolling baseline.")

        if abs(move) >= Decimal("180"):
            critical = True
            reasons.append("Critical one-candle price shock.")
        elif abs(move) >= Decimal("80"):
            warnings += 1
            reasons.append("Unusually large one-candle price move.")

        if candle_age_ms > 180_000:
            critical = True
            reasons.append("Market candle is stale.")
        elif candle_age_ms > 120_000:
            warnings += 1
            reasons.append("Market candle freshness is degraded.")

        if critical:
            # Do not dump into a severely widened spread.
            fraction = Decimal("0.50") if spread <= Decimal("20") else ZERO
            return ShieldAssessment("CRITICAL", True, fraction, tuple(reasons))
        if warnings >= 2:
            fraction = Decimal("0.25") if spread <= Decimal("15") else ZERO
            return ShieldAssessment("DEFENSIVE", True, fraction, tuple(reasons))
        if warnings == 1:
            return ShieldAssessment("CAUTION", True, ZERO, tuple(reasons))
        return ShieldAssessment("NORMAL", False, ZERO, ("Market quality is within guardrails.",))
