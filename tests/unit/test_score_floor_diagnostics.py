from decimal import Decimal

from mumin_trader.research.score_floor_diagnostics import (
    is_score_only_near_miss,
    rejection_categories,
)
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy, StrategyAssessment


def _assessment(signal: str, reasons: tuple[str, ...]) -> StrategyAssessment:
    return StrategyAssessment(
        signal=signal,
        regime="BULL",
        strategy="MOMENTUM",
        score=Decimal("72"),
        momentum_3_bps=Decimal("6"),
        momentum_12_bps=Decimal("100"),
        drawdown_12_bps=Decimal("0"),
        rebound_bps=Decimal("0"),
        rsi=Decimal("55"),
        estimated_cost_bps=Decimal("30"),
        expected_edge_bps=Decimal("70"),
        risk_reward_ratio=Decimal("2.5"),
        target_percent=Decimal("1.25"),
        atr_percent=Decimal("0.5"),
        mtf_confluence=True,
        reasons=reasons,
    )


def test_production_strategy_default_score_floor_remains_75() -> None:
    assert SmartSniperStrategy().minimum_score == Decimal("75")


def test_only_score_floor_near_miss_is_counted() -> None:
    baseline = _assessment(
        "HOLD",
        ("Quality score is below the configured acceptance threshold.",),
    )
    candidate = _assessment("BUY", ())
    assert is_score_only_near_miss(baseline, candidate)


def test_candidate_does_not_rescue_a_hard_gate_failure() -> None:
    baseline = _assessment(
        "HOLD",
        (
            "Quality score is below the configured acceptance threshold.",
            "Volatility is outside the accepted ATR band.",
        ),
    )
    candidate = _assessment("HOLD", ("Volatility is outside the accepted ATR band.",))
    assert not is_score_only_near_miss(baseline, candidate)
    assert "atr" in rejection_categories(candidate)


def test_rejection_categories_keep_mtf_and_cost_vetoes_visible() -> None:
    assessment = _assessment(
        "HOLD",
        (
            "Missing or non-confluent 5m/15m trend confirmation.",
            "Expected move does not cover the asymmetric target plus all estimated costs.",
        ),
    )
    assert rejection_categories(assessment) == ("mtf", "expected_move_cost")
