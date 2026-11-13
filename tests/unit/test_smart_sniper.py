from decimal import Decimal

from mumin_trader.strategy.smart_sniper import SmartSniperStrategy


def rising_series():
    values = []
    price = Decimal("100")
    for _ in range(55):
        price *= Decimal("1.002")
        values.append(price)
    return values


def test_insufficient_history_fails_closed():
    result = SmartSniperStrategy().assess([Decimal("100")] * 12)
    assert result.signal == "HOLD"
    assert result.regime == "UNKNOWN"


def test_sustained_rise_is_assessed_without_claiming_probability():
    result = SmartSniperStrategy().assess(rising_series(), spread_bps=Decimal("2"))
    assert result.regime == "BULL"
    assert result.score <= Decimal("100")
    assert result.signal in {"BUY", "HOLD"}
    assert result.expected_edge_bps == result.momentum_12_bps - result.estimated_cost_bps


def test_wide_spread_blocks_new_entry():
    result = SmartSniperStrategy().assess(rising_series(), spread_bps=Decimal("30"))
    assert result.signal == "HOLD"
    assert result.estimated_cost_bps >= Decimal("58")


def test_flat_market_does_not_create_buy_signal():
    result = SmartSniperStrategy().assess([Decimal("100")] * 50)
    assert result.signal == "HOLD"
    assert result.regime == "RANGE"


def test_bad_prices_fail_closed():
    result = SmartSniperStrategy().assess([Decimal("0")] * 40)
    assert result.signal == "HOLD"
