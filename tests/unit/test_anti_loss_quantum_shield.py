from decimal import Decimal

from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield


def assess(**overrides):
    values = {
        "spread_bps": Decimal("5"),
        "quote_volume_ratio": Decimal("1"),
        "trade_count_ratio": Decimal("1"),
        "price_change_bps": Decimal("10"),
        "candle_age_ms": 30_000,
    }
    values.update(overrides)
    return AntiLossQuantumShield().assess(**values)


def test_normal_market_passes_guard():
    result = assess()
    assert result.mode == "NORMAL"
    assert result.block_entries is False
    assert result.exit_fraction == Decimal("0")


def test_missing_volume_baseline_fails_closed():
    result = assess(quote_volume_ratio=None)
    assert result.mode == "DATA_GUARD"
    assert result.block_entries is True
    assert result.exit_fraction == Decimal("0")


def test_liquidity_and_price_anomalies_trigger_defensive_de_risk():
    result = assess(
        quote_volume_ratio=Decimal("0.3"),
        trade_count_ratio=Decimal("0.4"),
        price_change_bps=Decimal("95"),
    )
    assert result.mode == "DEFENSIVE"
    assert result.block_entries is True
    assert result.exit_fraction == Decimal("0.25")


def test_critical_spread_blocks_entries_without_forced_bad_fill():
    result = assess(
        spread_bps=Decimal("45"),
        quote_volume_ratio=Decimal("0.1"),
        trade_count_ratio=Decimal("0.1"),
        price_change_bps=Decimal("200"),
    )
    assert result.mode == "CRITICAL"
    assert result.block_entries is True
    assert result.exit_fraction == Decimal("0")


def test_stale_market_is_critical():
    result = assess(candle_age_ms=200_000)
    assert result.mode == "CRITICAL"
    assert result.block_entries is True
