from __future__ import annotations
from mumin_trader.strategy.smart_sniper import StrategyAssessment


def is_score_only_near_miss(
    baseline: StrategyAssessment,
    candidate: StrategyAssessment,
) -> bool:
    """True only when lowering the score floor alone changes HOLD into BUY."""
    return (
        baseline.signal == "HOLD"
        and candidate.signal == "BUY"
        and baseline.score == candidate.score
        and "Quality score is below the configured acceptance threshold." in baseline.reasons
    )


def rejection_categories(assessment: StrategyAssessment) -> tuple[str, ...]:
    """Attribute all observable hard-gate failures, independently of score rejection."""
    reason_text = " ".join(assessment.reasons).lower()
    categories: list[str] = []

    if not assessment.mtf_confluence:
        categories.append("mtf")

    if assessment.strategy == "MOMENTUM":
        trend_reason = "local ema trend and fast-ema slope are positive"
        if assessment.momentum_3_bps <= 5 or trend_reason not in reason_text:
            categories.append("momentum")
        if not Decimal("48") <= assessment.rsi <= Decimal("68"):
            categories.append("rsi")
    elif assessment.strategy == "RECOVERY":
        if assessment.momentum_3_bps <= 0 or assessment.momentum_12_bps <= 0:
            categories.append("momentum")
        if not Decimal("35") <= assessment.rsi <= Decimal("62"):
            categories.append("rsi")

    if assessment.atr_percent < Decimal("0.08") or assessment.atr_percent > Decimal("1.20"):
        categories.append("atr")
    if "spread exceeds the hard execution-quality ceiling" in reason_text:
        categories.append("spread")
    if "expected move does not cover the asymmetric target" in reason_text:
        categories.append("expected_move_cost")
    if "quality score is below the configured acceptance threshold" in reason_text:
        categories.append("score_floor")
    if assessment.signal == "HOLD" and not categories:
        categories.append("other_or_not_buy_setup")
    return tuple(categories)


from decimal import Decimal
