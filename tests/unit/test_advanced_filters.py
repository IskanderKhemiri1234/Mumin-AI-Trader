from decimal import Decimal

import pytest

from mumin_trader.advanced_filters import (
    CORE_SYMBOLS,
    FilterDecision,
    assess_symbol,
    evaluate_anti_chase_and_breakout,
    evaluate_liquidity,
    evaluate_market_regime,
    evaluate_momentum,
    evaluate_mtf_trend,
    evaluate_net_edge,
    rank_symbol_assessments,
)


D = Decimal


def rising_prices(count=60):
    price = D("100")
    values = []
    for index in range(count):
        price *= D("1.002") if index % 2 == 0 else D("0.9988")
        values.append(price)
    return values


def test_mtf_requires_all_closed_timeframes_to_align():
    closes = rising_prices()
    result = evaluate_mtf_trend({
        "5m": closes, "15m": closes, "1h": closes, "4h": closes,
    })
    assert result.passed
    assert result.reason == "MTF_ALL_FRAMES_BULLISH"
    assert dict(result.metrics)["aligned_frames"] == D("4")


def test_mtf_fails_closed_when_a_timeframe_is_missing():
    closes = rising_prices()
    result = evaluate_mtf_trend({"5m": closes, "15m": closes, "1h": closes})
    assert not result.passed
    assert result.reason == "MTF_DATA_MISSING:4h"


def test_momentum_requires_price_rsi_and_volume_confirmation():
    closes = rising_prices()
    volumes = [D("100")] * 20 + [D("160")]
    result = evaluate_momentum(closes, volumes)
    assert result.passed
    assert result.reason == "MOMENTUM_CONFIRMED"
    assert dict(result.metrics)["volume_ratio"] == D("1.6")


def test_momentum_rejects_weak_volume():
    closes = rising_prices()
    volumes = [D("100")] * 21
    result = evaluate_momentum(closes, volumes)
    assert not result.passed
    assert result.reason == "MOMENTUM_VOLUME_CONFIRMATION_FAILED"


def test_liquidity_rejects_wide_spread():
    result = evaluate_liquidity(
        bid=D("100"), ask=D("100.10"), quote_volume_24h=D("5000000")
    )
    assert not result.passed
    assert result.reason == "LIQUIDITY_SPREAD_LIMIT_REJECTED"


def test_liquidity_rejects_missing_depth_side():
    result = evaluate_liquidity(
        bid=D("100"), ask=D("100.01"), quote_volume_24h=D("5000000"),
        bid_depth_quote=D("1000"),
    )
    assert not result.passed
    assert result.reason == "LIQUIDITY_DEPTH_DATA_INCOMPLETE"


def test_anti_chase_rejects_extended_price():
    closes = [D("100")] * 21 + [D("110")]
    highs = [value * D("1.001") for value in closes]
    lows = [value * D("0.999") for value in closes]
    volumes = [D("100")] * len(closes)
    result = evaluate_anti_chase_and_breakout(
        closes=closes, highs=highs, lows=lows, volumes=volumes
    )
    assert not result.passed
    assert result.reason == "ANTI_CHASE_PRICE_TOO_EXTENDED"


def test_market_regime_rejects_flat_market():
    closes = [D("100")] * 30
    highs = [D("100.01")] * 30
    lows = [D("99.99")] * 30
    result = evaluate_market_regime(highs=highs, lows=lows, closes=closes)
    assert not result.passed
    assert result.reason == "REGIME_DIRECTIONAL_EFFICIENCY_TOO_LOW"


def test_net_edge_accounts_for_round_trip_costs():
    result = evaluate_net_edge(
        target_pct=D("0.50"), spread_bps=D("3"),
        fee_rate=D("0.001"), slippage_bps_per_side=D("2"),
    )
    assert result.passed
    metrics = dict(result.metrics)
    assert metrics["gross_edge_bps"] == D("50")
    assert metrics["estimated_cost_bps"] == D("27")
    assert metrics["net_edge_bps"] == D("23")


def test_net_edge_rejects_target_that_does_not_cover_costs():
    result = evaluate_net_edge(target_pct=D("0.20"), spread_bps=D("8"))
    assert not result.passed
    assert result.reason == "NET_EDGE_INSUFFICIENT_AFTER_COSTS"


def test_assessment_is_independent_and_ranking_prioritizes_approved():
    passed = FilterDecision(True, "PASS", 90, (("net_edge_bps", D("20")),))
    failed = FilterDecision(False, "FAIL", 0)
    good = assess_symbol(
        "ETHUSDT", trend=passed, momentum=passed, liquidity=passed,
        price_action=passed, regime=passed, net_edge=passed,
    )
    bad = assess_symbol(
        "BTCUSDT", trend=failed, momentum=passed, liquidity=passed,
        price_action=passed, regime=passed, net_edge=passed,
    )
    ranked = rank_symbol_assessments([bad, good])
    assert ranked[0].symbol == "ETHUSDT"
    assert ranked[0].approved
    assert not ranked[1].approved


def test_only_the_fixed_eight_asset_universe_can_be_assessed():
    passed = FilterDecision(True, "PASS", 90)
    with pytest.raises(ValueError, match="SYMBOL_OUTSIDE_CORE_EIGHT_ASSET_UNIVERSE"):
        assess_symbol(
            "DOGEUSDT", trend=passed, momentum=passed, liquidity=passed,
            price_action=passed, regime=passed, net_edge=passed,
        )
    assert len(CORE_SYMBOLS) == 8
