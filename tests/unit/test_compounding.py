from decimal import Decimal

import pytest

from mumin_trader.strategy.compounding import DynamicCompoundingMatrix


def test_reinvestment_uses_only_realized_profit_and_score_tiers():
    matrix = DynamicCompoundingMatrix()
    low = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("80"),
        drawdown_percent=Decimal("0.2"),
    )
    high = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("95"),
        drawdown_percent=Decimal("0.2"),
    )
    assert low.reinvestment_budget == Decimal("1.00000000")
    assert high.reinvestment_budget == Decimal("3.00000000")
    assert high.realized_profit_pool == Decimal("10")


def test_unrealized_profit_is_not_an_input_to_compounding():
    result = DynamicCompoundingMatrix().assess(
        realized_net_pnl=Decimal("-0.25"),
        momentum_score=Decimal("99"),
        drawdown_percent=Decimal("0"),
    )
    assert result.eligible is False
    assert result.realized_profit_pool == Decimal("0")
    assert result.reinvestment_budget == Decimal("0")


def test_drawdown_pauses_compounding():
    result = DynamicCompoundingMatrix().assess(
        realized_net_pnl=Decimal("20"),
        momentum_score=Decimal("99"),
        drawdown_percent=Decimal("1.0"),
    )
    assert result.eligible is False
    assert result.reinvestment_budget == Decimal("0")


def test_compounding_rejects_fraction_above_hard_cap():
    with pytest.raises(ValueError):
        DynamicCompoundingMatrix().assess(
            realized_net_pnl=Decimal("10"),
            momentum_score=Decimal("99"),
            drawdown_percent=Decimal("0"),
            maximum_fraction=Decimal("0.5"),
        )
