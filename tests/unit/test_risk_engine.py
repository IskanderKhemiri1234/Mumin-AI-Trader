from decimal import Decimal

from mumin_trader.risk_manager.risk_engine import RiskEngine


def make_engine():
    return RiskEngine(
        max_trade_allocation=Decimal("5"), min_trade_allocation=Decimal("1"),
        risk_per_trade_percent=Decimal("0.5"), max_daily_loss_percent=Decimal("2"),
        reserve_percent=Decimal("20"), max_open_positions=2,
    )


def test_risk_engine_approves_bounded_position():
    decision = make_engine().evaluate(equity=Decimal("50"), available_quote_balance=Decimal("50"), stop_distance_percent=Decimal("2"))
    assert decision.approved and decision.quantity_quote <= Decimal("5")


def test_daily_loss_blocks_new_position():
    decision = make_engine().evaluate(equity=Decimal("50"), available_quote_balance=Decimal("50"),
                                      stop_distance_percent=Decimal("2"), daily_loss=Decimal("1"))
    assert not decision.approved
