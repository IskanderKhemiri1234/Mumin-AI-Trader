from decimal import Decimal

from mumin_trader.fee_engine.fee_calculator import FeeCalculator


def test_round_trip_cost():
    result = FeeCalculator().round_trip_cost(Decimal("50"), Decimal("51"), Decimal("0.001"), Decimal("0.001"))
    assert result == Decimal("0.101")


def test_net_pnl():
    result = FeeCalculator().net_pnl(Decimal("50"), Decimal("51"), Decimal("0.05"), Decimal("0.051"))
    assert result == Decimal("0.899")
