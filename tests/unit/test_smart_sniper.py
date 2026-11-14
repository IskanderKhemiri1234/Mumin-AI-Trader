from decimal import Decimal

from mumin_trader.strategy.smart_sniper import SmartSniperStrategy


def rising_series():
    values = []
    price = Decimal("100")
    for index in range(55):
        price *= Decimal("1.006") if index % 2 == 0 else Decimal("0.997")
        values.append(price)
    return values


def test_insufficient_history_fails_closed():
    result = SmartSniperStrategy().assess([Decimal("100")] * 12)
    assert result.signal == "HOLD"
    assert result.regime == "UNKNOWN"


def stronger_rising_series():
    values = []
    price = Decimal("100")
    for index in range(55):
        price *= Decimal("1.012") if index % 2 == 0 else Decimal("0.993")
        values.append(price)
    return values


def bullish_timeframes():
    return {"higher_1": rising_series(), "higher_2": rising_series()}


def stronger_bullish_timeframes():
    return {
        "higher_1": stronger_rising_series(),
        "higher_2": stronger_rising_series(),
    }


def test_sustained_rise_requires_multi_timeframe_confluence_and_asymmetric_target():
    result = SmartSniperStrategy().assess(
        rising_series(), spread_bps=Decimal("2"),
        higher_timeframe_closes=bullish_timeframes(),
    )
    assert result.regime == "BULL"
    assert result.score <= Decimal("100")
    assert result.signal == "BUY"
    assert result.mtf_confluence is True
    assert result.risk_reward_ratio in {Decimal("2.5"), Decimal("3.0")}
    assert result.target_percent == Decimal("0.50") * result.risk_reward_ratio
    assert result.expected_edge_bps == result.momentum_12_bps - result.estimated_cost_bps


def test_high_conviction_momentum_can_select_three_r_only_when_cost_covered():
    result = SmartSniperStrategy().assess(
        stronger_rising_series(),
        spread_bps=Decimal("2"),
        higher_timeframe_closes=stronger_bullish_timeframes(),
    )
    assert result.signal == "BUY"
    assert result.score >= Decimal("90")
    assert result.risk_reward_ratio == Decimal("3.0")
    assert result.target_percent == Decimal("1.50")
    assert result.momentum_12_bps >= (
        Decimal("150") + result.estimated_cost_bps + Decimal("10")
    )


def test_wide_spread_blocks_new_entry():
    result = SmartSniperStrategy().assess(
        rising_series(), spread_bps=Decimal("30"),
        higher_timeframe_closes=bullish_timeframes(),
    )
    assert result.signal == "HOLD"
    assert result.estimated_cost_bps >= Decimal("58")


def test_flat_market_does_not_create_buy_signal():
    result = SmartSniperStrategy().assess([Decimal("100")] * 50)
    assert result.signal == "HOLD"
    assert result.regime == "RANGE"


def test_bad_prices_fail_closed():
    result = SmartSniperStrategy().assess([Decimal("0")] * 40)
    assert result.signal == "HOLD"


def test_missing_higher_timeframe_data_fails_closed():
    result = SmartSniperStrategy().assess(rising_series(), spread_bps=Decimal("2"))
    assert result.signal == "HOLD"
    assert result.mtf_confluence is False


def test_sideways_higher_timeframe_blocks_local_momentum():
    flat = [Decimal("100")] * 55
    result = SmartSniperStrategy().assess(
        rising_series(),
        spread_bps=Decimal("2"),
        higher_timeframe_closes={"higher_1": rising_series(), "higher_2": flat},
    )
    assert result.signal == "HOLD"
    assert result.mtf_confluence is False


def test_final_momentum_rsi_band_is_research_override_only(monkeypatch):
    import mumin_trader.strategy.smart_sniper as strategy_module

    monkeypatch.setattr(strategy_module, "_rsi", lambda values, period=14: Decimal("46"))
    closes = stronger_rising_series()
    baseline = SmartSniperStrategy().assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=stronger_bullish_timeframes()
    )
    candidate = SmartSniperStrategy(
        final_momentum_rsi_min=Decimal("45"),
        final_momentum_rsi_max=Decimal("70"),
    ).assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=stronger_bullish_timeframes()
    )

    assert baseline.strategy == candidate.strategy == "MOMENTUM"
    assert baseline.rsi == candidate.rsi == Decimal("46")
    assert baseline.score == candidate.score
    assert "Momentum RSI is outside the final eligibility band." in baseline.reasons
    assert "Momentum RSI is outside the final eligibility band." not in candidate.reasons
    assert baseline.signal == "HOLD"
    assert candidate.signal == "BUY"


def test_rsi_quality_bonus_remains_on_original_48_to_68_band(monkeypatch):
    import mumin_trader.strategy.smart_sniper as strategy_module

    monkeypatch.setattr(strategy_module, "_rsi", lambda values, period=14: Decimal("46"))
    closes = stronger_rising_series()
    baseline = SmartSniperStrategy().assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=stronger_bullish_timeframes()
    )
    candidate = SmartSniperStrategy(
        final_momentum_rsi_min=Decimal("45"),
        final_momentum_rsi_max=Decimal("70"),
    ).assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=stronger_bullish_timeframes()
    )

    assert baseline.score == candidate.score
    assert "RSI confirms strength without an overbought reading." not in baseline.reasons
    assert "RSI confirms strength without an overbought reading." not in candidate.reasons


def test_recovery_rsi_scoring_is_unchanged_by_momentum_band(monkeypatch):
    import mumin_trader.strategy.smart_sniper as strategy_module

    monkeypatch.setattr(strategy_module, "_rsi", lambda values, period=14: Decimal("50"))
    closes = [Decimal("100")] * 42 + [
        Decimal(value) for value in ("102.8", "102", "100", "98", "97", "96", "97",
                                     "98", "99", "100", "101", "102", "103")
    ]
    higher = {"higher_1": [Decimal("100")] * 55, "higher_2": [Decimal("100")] * 55}
    baseline = SmartSniperStrategy().assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=higher
    )
    candidate = SmartSniperStrategy(
        final_momentum_rsi_min=Decimal("45"),
        final_momentum_rsi_max=Decimal("70"),
    ).assess(closes, spread_bps=Decimal("2"), higher_timeframe_closes=higher)

    assert baseline.strategy == candidate.strategy
    assert baseline.score == candidate.score
    assert baseline.signal == candidate.signal
    assert baseline.rsi == candidate.rsi == Decimal("50")
    assert "Recovery RSI is constructive." in baseline.reasons
    assert "Recovery RSI is constructive." in candidate.reasons


def test_invalid_final_momentum_rsi_band_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="RSI eligibility band"):
        SmartSniperStrategy(
            final_momentum_rsi_min=Decimal("70"),
            final_momentum_rsi_max=Decimal("45"),
        )

def test_research_mtf_switch_bypasses_only_final_veto_and_keeps_momentum_bonus():
    closes = stronger_rising_series()
    no_confluence = {
        "higher_1": [Decimal("100")] * 55,
        "higher_2": [Decimal("100")] * 55,
    }
    baseline = SmartSniperStrategy().assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=no_confluence,
    )
    candidate = SmartSniperStrategy(require_mtf_confluence=False).assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=no_confluence,
    )
    assert baseline.mtf_confluence is candidate.mtf_confluence is False
    assert baseline.strategy == candidate.strategy == "MOMENTUM"
    assert baseline.score == candidate.score
    assert baseline.signal == "HOLD"
    assert candidate.signal == "BUY"
    assert "Missing or non-confluent 5m/15m trend confirmation." in candidate.reasons


def test_mtf_momentum_quality_bonus_remains_25_points():
    closes = stronger_rising_series()
    missing = {
        "higher_1": [Decimal("100")] * 55,
        "higher_2": [Decimal("100")] * 55,
    }
    confirmed = stronger_bullish_timeframes()
    without_bonus = SmartSniperStrategy(require_mtf_confluence=False).assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=missing,
    )
    with_bonus = SmartSniperStrategy(require_mtf_confluence=False).assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=confirmed,
    )
    assert with_bonus.score - without_bonus.score == Decimal("25")


def test_research_mtf_switch_does_not_change_recovery_scoring(monkeypatch):
    import mumin_trader.strategy.smart_sniper as strategy_module

    monkeypatch.setattr(strategy_module, "_rsi", lambda values, period=14: Decimal("50"))
    closes = [Decimal("100")] * 42 + [
        Decimal(value) for value in ("102.8", "102", "100", "98", "97", "96", "97",
                                     "98", "99", "100", "101", "102", "103")
    ]
    no_confluence = {
        "higher_1": [Decimal("100")] * 55,
        "higher_2": [Decimal("100")] * 55,
    }
    baseline = SmartSniperStrategy().assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=no_confluence,
    )
    candidate = SmartSniperStrategy(require_mtf_confluence=False).assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=no_confluence,
    )
    assert baseline.strategy == candidate.strategy
    assert baseline.score == candidate.score
    assert baseline.rsi == candidate.rsi == Decimal("50")
    assert "Recovery RSI is constructive." in candidate.reasons


def test_mtf_veto_is_enabled_by_default():
    assert SmartSniperStrategy().require_mtf_confluence is True


def test_mtf_recovery_quality_bonus_remains_20_points(monkeypatch):
    import mumin_trader.strategy.smart_sniper as strategy_module

    closes = [Decimal("100")] * 42 + [
        Decimal(value) for value in ("102.8", "102", "100", "98", "97", "96", "97",
                                     "98", "99", "100", "101", "102", "103")
    ]
    monkeypatch.setattr(strategy_module, "_rsi", lambda values, period=14: Decimal("50"))
    monkeypatch.setattr(
        strategy_module,
        "_ema",
        lambda values, period: (
            Decimal("101") if period == 9 and values[-1] == closes[-1]
            else Decimal("100") if period == 9
            else Decimal("102")
        ),
    )
    monkeypatch.setattr(
        strategy_module,
        "_timeframe_bullish",
        lambda values: bool(values) and values[-1] > Decimal("150"),
    )
    missing = {
        "higher_1": [Decimal("100")] * 55,
        "higher_2": [Decimal("100")] * 55,
    }
    confirmed = {
        "higher_1": [Decimal("200")] * 55,
        "higher_2": [Decimal("200")] * 55,
    }
    strategy = SmartSniperStrategy(require_mtf_confluence=False)
    without_mtf_bonus = strategy.assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=missing,
    )
    with_mtf_bonus = strategy.assess(
        closes, spread_bps=Decimal("2"), higher_timeframe_closes=confirmed,
    )
    assert without_mtf_bonus.strategy == with_mtf_bonus.strategy == "RECOVERY"
    assert with_mtf_bonus.score - without_mtf_bonus.score == Decimal("20")
