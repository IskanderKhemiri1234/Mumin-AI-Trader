from decimal import Decimal

import pytest

from mumin_trader.balanced_spot_policy import (
    DEFAULT_BALANCED_SPOT_POLICY,
    BalancedSpotPolicy,
)


def test_default_policy_matches_approved_isolated_spot_configuration():
    policy = DEFAULT_BALANCED_SPOT_POLICY

    assert policy.symbol == "BTCUSDT"
    assert policy.entry_interval == "15m"
    assert policy.initial_capital_usdt == Decimal("70.00")
    assert policy.risk_budget_per_trade_usdt == Decimal("0.35")
    assert policy.max_open_positions == 1
    assert policy.trading_mode == "PAPER"
    assert policy.trading_environment == "TESTNET"
    assert policy.spot_only is True
    assert policy.leverage == Decimal("0")
    assert policy.mainnet_enabled is False


def test_position_sizing_respects_risk_budget_and_existing_five_usdt_cap():
    policy = DEFAULT_BALANCED_SPOT_POLICY

    # At a 0.5% stop plus 0.24% estimated round-trip costs,
    # the risk budget permits more than 5 USDT, so the legacy cap wins.
    assert policy.position_quote_for_risk(
        Decimal("0.5"), Decimal("70.00")
    ) == Decimal("5.00")


def test_position_sizing_respects_available_cash():
    policy = DEFAULT_BALANCED_SPOT_POLICY

    assert policy.position_quote_for_risk(
        Decimal("0.5"), Decimal("4.99")
    ) == Decimal("0")


def test_position_sizing_returns_zero_when_risk_budget_cannot_meet_minimum():
    policy = DEFAULT_BALANCED_SPOT_POLICY

    assert policy.position_quote_for_risk(
        Decimal("10"), Decimal("70.00")
    ) == Decimal("0")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"symbol": "ETHUSDT"}, "restricted to BTCUSDT"),
        ({"entry_interval": "1m"}, "requires 15m"),
        ({"initial_capital_usdt": Decimal("50")}, "fixed at 70.00"),
        ({"risk_budget_per_trade_usdt": Decimal("0.5")}, "fixed at 0.35"),
        ({"max_position_quote_usdt": Decimal("17.50")}, "5.00 USDT per-trade"),
        ({"max_open_positions": 2}, "one open position"),
        ({"trading_mode": "LIVE"}, "TESTNET/PAPER"),
        ({"spot_only": False}, "unleveraged Spot"),
        ({"leverage": Decimal("2")}, "unleveraged Spot"),
        ({"mainnet_enabled": True}, "Mainnet must remain disabled"),
    ],
)
def test_policy_rejects_unapproved_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        BalancedSpotPolicy(**kwargs)


@pytest.mark.parametrize("stop_pct", [Decimal("0"), Decimal("-0.1"), Decimal("100"), Decimal("NaN")])
def test_position_sizing_rejects_invalid_stop_percentage(stop_pct):
    with pytest.raises(ValueError, match="stop_loss_pct"):
        DEFAULT_BALANCED_SPOT_POLICY.position_quote_for_risk(
            stop_pct, Decimal("70.00")
        )


def test_position_sizing_rejects_invalid_available_cash():
    with pytest.raises(ValueError, match="available_cash_usdt"):
        DEFAULT_BALANCED_SPOT_POLICY.position_quote_for_risk(
            Decimal("0.5"), Decimal("-1")
        )
