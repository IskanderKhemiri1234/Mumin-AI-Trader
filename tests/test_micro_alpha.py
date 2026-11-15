from decimal import Decimal

from mumin_trader.strategy_micro_alpha import OrderBookImbalanceAgent, PaperMicroAlphaSwarm, atr_adaptive_targets, atr_percent


def test_atr_targets_adapt_to_volatility_without_exceeding_caps():
    calm = atr_adaptive_targets(Decimal("0.15"))
    active = atr_adaptive_targets(Decimal("0.80"))
    extreme = atr_adaptive_targets(Decimal("2.50"))
    assert calm == (Decimal("0.20"), Decimal("0.40"))
    assert active == (Decimal("0.50"), Decimal("1.25"))
    assert extreme[0] <= Decimal("0.50") and extreme[1] <= Decimal("1.25")


def test_atr_requires_full_window_and_uses_true_range():
    highs, lows, closes = [Decimal("101")] * 15, [Decimal("99")] * 15, [Decimal("100")] * 15
    assert atr_percent(highs, lows, closes) == Decimal("2")
    assert atr_percent(highs[:10], lows[:10], closes[:10]) == 0


def test_depth_imbalance_agent_fails_closed_on_missing_side():
    agent = OrderBookImbalanceAgent()
    assert agent.evaluate(Decimal("70"), Decimal("30")) == "BUY"
    assert agent.evaluate(Decimal("30"), Decimal("70")) == "SELL"
    assert agent.evaluate(Decimal("0"), Decimal("70")) == "HOLD"


def test_micro_alpha_requires_sweep_and_depth_consensus():
    closes = [Decimal("100")] * 14 + [Decimal("100.2")]
    highs = [Decimal("100.2")] * 14 + [Decimal("100.3")]
    lows = [Decimal("99.8")] * 14 + [Decimal("99.0")]
    volumes = [Decimal("10")] * 14 + [Decimal("15")]
    signal, atr, stop, target, reason = PaperMicroAlphaSwarm().evaluate(
        closes=closes, highs=highs, lows=lows, volumes=volumes,
        bid_depth=Decimal("80"), ask_depth=Decimal("20"),
        bid=Decimal("100.1"), ask=Decimal("100.2"), max_spread_bps=Decimal("12"),
    )
    assert signal == "BUY" and atr > 0
    assert stop <= Decimal("0.50") and target <= Decimal("1.25")
    assert reason == "SWEEP_DEPTH_CONSENSUS"


def test_micro_alpha_fails_closed_on_depth_disagreement():
    closes = [Decimal("100")] * 14 + [Decimal("100.2")]
    highs = [Decimal("100.2")] * 14 + [Decimal("100.3")]
    lows = [Decimal("99.8")] * 14 + [Decimal("99.0")]
    volumes = [Decimal("10")] * 14 + [Decimal("15")]
    signal, *_ = PaperMicroAlphaSwarm().evaluate(
        closes=closes, highs=highs, lows=lows, volumes=volumes,
        bid_depth=Decimal("20"), ask_depth=Decimal("80"),
        bid=Decimal("100.1"), ask=Decimal("100.2"), max_spread_bps=Decimal("12"),
    )
    assert signal == "HOLD"



def test_detailed_agent_log_explains_votes_spread_and_atr():
    closes = [Decimal("100")] * 14 + [Decimal("100.2")]
    highs = [Decimal("100.2")] * 14 + [Decimal("100.3")]
    lows = [Decimal("99.8")] * 14 + [Decimal("99.0")]
    volumes = [Decimal("10")] * 14 + [Decimal("15")]
    details = PaperMicroAlphaSwarm().evaluate_detailed(
        closes=closes, highs=highs, lows=lows, volumes=volumes,
        bid_depth=Decimal("80"), ask_depth=Decimal("20"),
        bid=Decimal("100.1"), ask=Decimal("100.2"),
        max_spread_bps=Decimal("12"),
    )
    assert details["sweep_vote"] == "BUY"
    assert details["sweep_reason"] == "LOW_SWEPT_AND_RECLAIMED_WITH_STRONG_CLOSE"
    assert details["depth_vote"] == "BUY"
    assert details["depth_reason"] == "BID_DEPTH_IMBALANCE_AT_LEAST_12_PERCENT"
    assert details["filter_vote"] == "BUY"
    assert details["filter_reason"] == "SWEEP_AND_DEPTH_CONSENSUS_PASSED"
    assert Decimal(details["spread_bps"]) > 0
    assert Decimal(details["atr_pct"]) > 0
