from decimal import Decimal

import pytest

from mumin_trader.strategy.smart_sniper import (
    evaluate_entry,
    evaluate_exit,
    order_book_imbalance,
    volume_surge_ratio,
)


def test_entry_requires_momentum_volume_and_bid_imbalance_together():
    accepted = evaluate_entry(Decimal("12"), Decimal("2.1"), Decimal("0.24"))
    rejected = evaluate_entry(Decimal("12"), Decimal("2.1"), Decimal("-0.05"))
    assert accepted.accepted is True
    assert accepted.score == 3
    assert set(accepted.reasons) == {"MOMENTUM", "VOLUME_SURGE", "BID_IMBALANCE"}
    assert rejected.accepted is False


def test_volume_surge_uses_latest_closed_candle_against_prior_mean():
    ratio = volume_surge_ratio([
        Decimal("10"), Decimal("10"), Decimal("10"), Decimal("20")
    ])
    assert ratio == Decimal("2")


def test_order_book_imbalance_is_normalized_and_signed():
    assert order_book_imbalance(Decimal("75"), Decimal("25")) == Decimal("0.5")
    assert order_book_imbalance(Decimal("25"), Decimal("75")) == Decimal("-0.5")
    assert order_book_imbalance(Decimal("0"), Decimal("0")) == Decimal("0")


def test_trailing_stop_ratcheting_and_holds_while_trend_remains_intact():
    first = evaluate_exit(
        Decimal("100"), Decimal("100"), Decimal("100.50"),
        Decimal("10"), Decimal("0.20"),
    )
    next_step = evaluate_exit(
        Decimal("100"), first.high_water_price, Decimal("101.00"),
        Decimal("12"), Decimal("0.25"),
    )
    pullback = evaluate_exit(
        Decimal("100"), next_step.high_water_price, Decimal("100.70"),
        Decimal("2"), Decimal("0.10"),
    )
    assert first.should_exit is False
    assert next_step.high_water_price == Decimal("101.00")
    assert next_step.trailing_stop_price > first.trailing_stop_price
    assert pullback.should_exit is False


def test_trailing_stop_exits_after_retracement_from_peak():
    decision = evaluate_exit(
        Decimal("100"), Decimal("101"), Decimal("100.50"),
        Decimal("0"), Decimal("0.05"),
    )
    assert decision.should_exit is True
    assert decision.reason == "TRAILING_STOP"


def test_confirmed_momentum_reversal_exits_before_trailing_stop():
    decision = evaluate_exit(
        Decimal("100"), Decimal("101"), Decimal("100.90"),
        Decimal("-12"), Decimal("-0.20"),
    )
    assert decision.should_exit is True
    assert decision.reason == "CONFIRMED_MOMENTUM_REVERSAL"


def test_invalid_exit_prices_are_rejected():
    with pytest.raises(ValueError):
        evaluate_exit(
            Decimal("0"), Decimal("100"), Decimal("100"),
            Decimal("0"), Decimal("0"),
        )
