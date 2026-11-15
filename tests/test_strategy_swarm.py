from decimal import Decimal

from mumin_trader.strategy_swarm import PaperSwarm, compound_allocation, compoundable_equity


def test_swarm_requires_momentum_and_liquidity_consensus():
    swarm = PaperSwarm()
    assert swarm.evaluate(Decimal("10"), Decimal("99.99"), Decimal("100.01"), Decimal("12"))[0] == "BUY"
    blocked = swarm.evaluate(Decimal("10"), Decimal("99.80"), Decimal("100.20"), Decimal("12"))
    assert blocked == ("HOLD", "BUY", False)
    assert swarm.evaluate(Decimal("-10"), Decimal("99.80"), Decimal("100.20"), Decimal("12"))[0] == "SELL"


def test_compounder_scales_with_equity_and_respects_cap_and_reserve():
    assert compound_allocation(Decimal("70"), Decimal("60"), Decimal("14")) == Decimal("5")
    assert compound_allocation(Decimal("35"), Decimal("40"), Decimal("14")) == Decimal("2.5")
    assert compound_allocation(Decimal("90"), Decimal("80"), Decimal("14")) == Decimal("90") * Decimal("5") / Decimal("70")
    assert compound_allocation(Decimal("70"), Decimal("18"), Decimal("14")) == Decimal("4")
    assert compound_allocation(Decimal("77"), Decimal("60"), Decimal("14")) == Decimal("5.5")



def test_compounder_uses_realized_profit_and_not_unrealized_gains():
    assert compoundable_equity(Decimal("90"), Decimal("10")) == Decimal("80")
    assert compoundable_equity(Decimal("65"), Decimal("10")) == Decimal("65")
    assert compoundable_equity(Decimal("80"), Decimal("-5")) == Decimal("65")
