from decimal import Decimal

import pytest

from mumin_trader.research.liquidity_confirmation import liquidity_confirmation_passes


@pytest.mark.parametrize(
    ("volume", "trades"),
    [
        ("0.80", "0.80"),
        ("2.50", "2.50"),
        ("0.80", "2.50"),
        ("2.50", "0.80"),
        ("1.00", "1.00"),
    ],
)
def test_liquidity_confirmation_accepts_inclusive_boundaries(volume, trades):
    assert liquidity_confirmation_passes(Decimal(volume), Decimal(trades))


@pytest.mark.parametrize(
    ("volume", "trades"),
    [
        ("0.799999", "1.00"),
        ("2.500001", "1.00"),
        ("1.00", "0.799999"),
        ("1.00", "2.500001"),
        ("0", "1.00"),
        ("1.00", "0"),
        ("-0.01", "1.00"),
    ],
)
def test_liquidity_confirmation_rejects_out_of_range_or_zero_ratios(volume, trades):
    assert not liquidity_confirmation_passes(Decimal(volume), Decimal(trades))


@pytest.mark.parametrize(
    ("volume", "trades"),
    [
        (None, Decimal("1.00")),
        (Decimal("1.00"), None),
        (Decimal("NaN"), Decimal("1.00")),
        (Decimal("1.00"), Decimal("Infinity")),
    ],
)
def test_liquidity_confirmation_fails_closed_for_missing_or_non_finite_ratios(volume, trades):
    assert not liquidity_confirmation_passes(volume, trades)


def test_liquidity_confirmation_is_disabled_by_default_and_only_blocks_candidate_entries(monkeypatch):
    from types import SimpleNamespace

    import mumin_trader.research.walk_forward as research

    class AlwaysBuy:
        def assess(self, closes, spread_bps=Decimal("0"), **kwargs):
            return SimpleNamespace(
                signal="BUY",
                score=Decimal("95"),
                regime="BULL",
                mtf_confluence=True,
                atr_percent=Decimal("0.30"),
                target_percent=Decimal("1.25"),
            )

    monkeypatch.setattr(research, "SmartSniperStrategy", AlwaysBuy)
    series = []
    price = Decimal("100")
    for index in range(110):
        price *= Decimal("1.002")
        volume = Decimal("700") if index == 60 else Decimal("1000")
        trades = Decimal("700") if index == 60 else Decimal("1000")
        series.append(research.HistoricalCandle(
            timestamp_ms=1_800_000_000_000 + index * 60_000,
            open=price * Decimal("0.999"),
            high=price * Decimal("1.002"),
            low=price * Decimal("0.998"),
            close=price,
            quote_volume=volume,
            trade_count=trades,
            spread_bps=Decimal("2"),
        ))

    baseline = research.run_backtest(series, warmup_bars=60)
    candidate = research.run_backtest(
        series, warmup_bars=60, enable_liquidity_confirmation=True
    )

    assert baseline.liquidity_confirmation_blocks == 0
    assert candidate.liquidity_confirmation_blocks >= 1
    assert candidate.entry_orders <= baseline.entry_orders
    assert candidate.shield_blocks == baseline.shield_blocks
