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
    """Map the strategy's existing rejection explanations to frozen diagnostic buckets."""
    reason_text = " ".join(assessment.reasons).lower()
    categories: list[str] = []
    checks = (
        ("mtf", "missing or non-confluent 5m/15m trend confirmation"),
        ("momentum", "short-horizon momentum is positive"),
        ("rsi", "rsi confirms strength without an overbought reading"),
        ("atr", "volatility is outside the accepted atr band"),
        ("spread", "spread exceeds the hard execution-quality ceiling"),
        ("expected_move_cost", "expected move does not cover the asymmetric target"),
        ("score_floor", "quality score is below the configured acceptance threshold"),
    )
    for category, marker in checks:
        if marker in reason_text:
            categories.append(category)
    if assessment.signal == "HOLD" and not categories:
        categories.append("other_or_not_buy_setup")
    return tuple(categories)
