from decimal import Decimal

from mumin_trader.strategy_swarm import PaperSwarm, compound_allocation


def test_swarm_requires_momentum_and_liquidity_consensus():
    swarm = PaperSwarm()
    assert swarm.evaluate(Decimal("10"), Decimal("99.99"), Decimal("100.01"), Decimal("12"))[0] == "BUY"
    blocked = swarm.evaluate(Decimal("10"), Decimal("99.80"), Decimal("100.20"), Decimal("12"))
    assert blocked == ("HOLD", "BUY", False)
    assert swarm.evaluate(Decimal("-10"), Decimal("99.80"), Decimal("100.20"), Decimal("12"))[0] == "SELL"


def test_compounder_scales_with_equity_and_respects_cap_and_reserve():
    assert compound_allocation(Decimal("70"), Decimal("60"), Decimal("14")) == Decimal("5")
    assert compound_allocation(Decimal("35"), Decimal("40"), Decimal("14")) == Decimal("2.5")
    assert compound_allocation(Decimal("90"), Decimal("80"), Decimal("14")) == Decimal("5")
    assert compound_allocation(Decimal("70"), Decimal("18"), Decimal("14")) == Decimal("4")
