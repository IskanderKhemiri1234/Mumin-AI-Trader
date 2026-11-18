from decimal import Decimal

import pytest

from mumin_trader.ai_engine.strategy_engine import CORE_SYMBOLS, StrategyEngine


def valid_inputs():
    return {
        "symbol": "BTCUSDT",
        "entry_price": Decimal("100"),
        "spread_bps": Decimal("4"),
        "quote_volume": Decimal("6000000"),
        "rsi": Decimal("56"),
        "momentum_bps": Decimal("35"),
        "timeframe_trends": {"fast": True, "15m": True, "1h": True},
    }


def test_fixed_eight_asset_universe():
    assert CORE_SYMBOLS == (
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
        "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    )


def test_strategy_approves_only_full_confluence_and_calculates_exits():
    decision = StrategyEngine().evaluate(**valid_inputs())
    assert decision.approved
    assert decision.score >= 75
    assert decision.stop_loss_price == Decimal("99.5000")
    assert decision.take_profit_price == Decimal("101.2500")


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("spread_bps", Decimal("13"), "Liquidity Shield"),
        ("quote_volume", Decimal("999999"), "Liquidity Shield"),
        ("momentum_bps", Decimal("5"), "Momentum"),
        ("rsi", Decimal("70"), "RSI"),
    ],
)
def test_strategy_rejects_failed_hard_gates(field, value, reason):
    inputs = valid_inputs()
    inputs[field] = value
    decision = StrategyEngine().evaluate(**inputs)
    assert not decision.approved
    assert reason in decision.reason


def test_strategy_rejects_missing_or_bearish_timeframe():
    inputs = valid_inputs()
    inputs["timeframe_trends"] = {"fast": True, "15m": True}
    assert "missing" in StrategyEngine().evaluate(**inputs).reason

    inputs["timeframe_trends"]["1h"] = False
    assert "not bullish" in StrategyEngine().evaluate(**inputs).reason


def test_take_profit_is_bounded_to_user_range():
    with pytest.raises(ValueError, match="between 1.25% and 1.50%"):
        StrategyEngine(take_profit_percent=Decimal("1.60"))


def test_strategy_engine_fails_closed_when_advanced_filter_rejects():
    inputs = valid_inputs()
    inputs["advanced_filter_reasons"] = ("REGIME_DIRECTIONAL_EFFICIENCY_TOO_LOW",)
    decision = StrategyEngine().evaluate(**inputs)
    assert not decision.approved
    assert "REGIME_DIRECTIONAL_EFFICIENCY_TOO_LOW" in decision.reason
    assert decision.stop_loss_price is None
    assert decision.take_profit_price is None
