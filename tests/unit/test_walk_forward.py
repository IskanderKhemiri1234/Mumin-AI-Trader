from decimal import Decimal

import pytest

from mumin_trader.research.walk_forward import (
    HistoricalCandle,
    run_backtest,
    walk_forward,
)


def candles(count=200):
    result = []
    price = Decimal("100")
    for index in range(count):
        drift = Decimal("1.0008") if index % 70 < 45 else Decimal("0.9995")
        price *= drift
        spread = Decimal("4") if index % 53 else Decimal("18")
        volume = Decimal("1000") if index % 47 else Decimal("300")
        trades = Decimal("100") if index % 47 else Decimal("35")
        result.append(HistoricalCandle(
            timestamp_ms=1_800_000_000_000 + index * 60_000,
            open=price * Decimal("0.999"),
            high=price * Decimal("1.002"),
            low=price * Decimal("0.998"),
            close=price,
            quote_volume=volume,
            trade_count=trades,
            spread_bps=spread,
        ))
    return result


def test_walk_forward_produces_multiple_unseen_test_windows():
    series = candles()
    report = walk_forward(
        series, warmup_bars=60, test_bars=40, step_bars=40
    )
    assert len(report.folds) == 3
    assert report.folds[0].start_index == 60
    assert report.folds[0].end_index == 100
    assert all(fold.ending_equity > 0 for fold in report.folds)
    assert all(fold.max_drawdown_percent >= 0 for fold in report.folds)


def test_walk_forward_first_fold_is_unchanged_by_appended_future_data():
    series = candles(200)
    baseline = walk_forward(
        series, warmup_bars=60, test_bars=40, step_bars=40
    ).folds[0]
    extended = walk_forward(
        candles(240), warmup_bars=60, test_bars=40, step_bars=40
    ).folds[0]
    assert baseline == extended


def test_single_backtest_requires_indicator_warmup():
    with pytest.raises(ValueError):
        run_backtest(candles(50), warmup_bars=60)


def test_walk_forward_rejects_non_chronological_data():
    series = candles(100)
    series[80] = HistoricalCandle(
        timestamp_ms=series[79].timestamp_ms,
        open=series[80].open,
        high=series[80].high,
        low=series[80].low,
        close=series[80].close,
        quote_volume=series[80].quote_volume,
        trade_count=series[80].trade_count,
        spread_bps=series[80].spread_bps,
    )
    with pytest.raises(ValueError, match="chronological"):
        walk_forward(series, warmup_bars=60, test_bars=20, step_bars=20)


def test_research_engine_executes_seed_ticket_and_reinvests_only_realized_profit(monkeypatch):
    from types import SimpleNamespace

    import mumin_trader.research.walk_forward as research

    class AlwaysBuy:
        def assess(self, closes, spread_bps=Decimal("0")):
            return SimpleNamespace(signal="BUY", score=Decimal("95"))

    monkeypatch.setattr(research, "SmartSniperStrategy", AlwaysBuy)
    series = []
    price = Decimal("100")
    for index in range(110):
        price *= Decimal("1.002")
        series.append(HistoricalCandle(
            timestamp_ms=1_800_000_000_000 + index * 60_000,
            open=price * Decimal("0.999"),
            high=price * Decimal("1.002"),
            low=price * Decimal("0.998"),
            close=price,
            quote_volume=Decimal("1000"),
            trade_count=Decimal("100"),
            spread_bps=Decimal("2"),
        ))

    result = research.run_backtest(series, warmup_bars=60)
    assert result.entry_orders > 0
    assert result.closed_trades > 0
    assert result.compounding_reinvested > Decimal("0")
