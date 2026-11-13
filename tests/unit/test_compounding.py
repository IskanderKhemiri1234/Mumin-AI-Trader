from decimal import Decimal

import pytest

from mumin_trader.strategy.compounding import DynamicCompoundingMatrix


def test_reinvestment_uses_only_realized_profit_and_score_tiers():
    matrix = DynamicCompoundingMatrix()
    low = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("80"),
        drawdown_percent=Decimal("0.2"),
        atr_percent=Decimal("0.30"),
        trend_clear=True,
        liquidity_high=True,
    )
    high = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("95"),
        drawdown_percent=Decimal("0.2"),
        atr_percent=Decimal("0.30"),
        trend_clear=True,
        liquidity_high=True,
    )
    assert low.reinvestment_budget == Decimal("1.00000000")
    assert high.reinvestment_budget == Decimal("3.00000000")
    assert high.realized_profit_pool == Decimal("10")


def test_unrealized_profit_is_not_an_input_to_compounding():
    result = DynamicCompoundingMatrix().assess(
        realized_net_pnl=Decimal("-0.25"),
        momentum_score=Decimal("99"),
        drawdown_percent=Decimal("0"),
        atr_percent=Decimal("0.30"),
        trend_clear=True,
        liquidity_high=True,
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


def test_volatility_adjustment_scales_reinvestment_down():
    matrix = DynamicCompoundingMatrix()
    stable = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("95"),
        drawdown_percent=Decimal("0"),
        atr_percent=Decimal("0.30"),
        trend_clear=True,
        liquidity_high=True,
    )
    elevated = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("95"),
        drawdown_percent=Decimal("0"),
        atr_percent=Decimal("0.90"),
        trend_clear=True,
        liquidity_high=True,
    )
    assert stable.reinvestment_budget == Decimal("3.00000000")
    assert elevated.reinvestment_budget == Decimal("1.20000000")
    assert elevated.volatility_multiplier == Decimal("0.40")


def test_compounding_pauses_without_clear_trend_or_liquidity():
    matrix = DynamicCompoundingMatrix()
    no_trend = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("99"),
        drawdown_percent=Decimal("0"),
        atr_percent=Decimal("0.30"),
        trend_clear=False,
        liquidity_high=True,
    )
    poor_liquidity = matrix.assess(
        realized_net_pnl=Decimal("10"),
        momentum_score=Decimal("99"),
        drawdown_percent=Decimal("0"),
        atr_percent=Decimal("0.30"),
        trend_clear=True,
        liquidity_high=False,
    )
    assert no_trend.reinvestment_budget == 0
    assert poor_liquidity.reinvestment_budget == 0
