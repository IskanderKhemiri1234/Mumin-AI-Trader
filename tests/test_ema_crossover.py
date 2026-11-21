from decimal import Decimal

import pytest

from mumin_trader.ai_engine.ema_crossover import (
    calculate_ema,
    evaluate_ema_crossover,
)


def test_calculate_ema_uses_decimal_and_first_close_seed():
    result = calculate_ema(
        [Decimal("10"), Decimal("12"), Decimal("14")],
        period=2,
    )
    assert result == (
        Decimal("10"),
        Decimal("11.33333333333333333333333333"),
        Decimal("13.11111111111111111111111111"),
    )


def test_crossover_returns_hold_until_enough_closed_candles():
    with pytest.raises(ValueError, match="closed candles"):
        evaluate_ema_crossover([Decimal("100")] * 50)


def test_crossover_validates_period_order():
    with pytest.raises(ValueError, match="smaller"):
        evaluate_ema_crossover(
            [Decimal("100")] * 60,
            fast_period=50,
            slow_period=20,
        )


def test_crossover_rejects_invalid_prices():
    with pytest.raises(ValueError, match="positive finite"):
        calculate_ema([Decimal("10"), Decimal("0"), Decimal("11")], period=2)


def test_golden_cross_emits_buy_only_on_crossing_candle():
    result = evaluate_ema_crossover(
        [Decimal("100")] * 30 + [Decimal("200")],
        fast_period=5,
        slow_period=12,
    )
    assert result.signal == "BUY"
    assert result.fast_previous <= result.slow_previous
    assert result.fast_current > result.slow_current


def test_death_cross_emits_sell_only_on_crossing_candle():
    result = evaluate_ema_crossover(
        [Decimal("100")] * 30 + [Decimal("50")],
        fast_period=5,
        slow_period=12,
    )
    assert result.signal == "SELL"
    assert result.fast_previous >= result.slow_previous
    assert result.fast_current < result.slow_current


def test_flat_series_has_no_crossover():
    result = evaluate_ema_crossover(
        [Decimal("100")] * 80,
        fast_period=5,
        slow_period=12,
    )
    assert result.signal == "HOLD"
    assert result.fast_current == result.slow_current


def test_fast_period_must_be_at_least_two():
    with pytest.raises(ValueError, match="at least 2"):
        evaluate_ema_crossover(
            [Decimal("100")] * 60,
            fast_period=1,
            slow_period=20,
        )
