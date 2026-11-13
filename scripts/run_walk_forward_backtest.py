from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from mumin_trader.research.walk_forward import (
    HistoricalCandle,
    run_backtest,
    walk_forward,
)


def load_candles(path: Path) -> list[HistoricalCandle]:
    required = {
        "timestamp_ms", "open", "high", "low", "close",
        "quote_volume", "trade_count", "spread_bps",
    }
    candles: list[HistoricalCandle] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")
        for row in reader:
            candles.append(HistoricalCandle(
                timestamp_ms=int(row["timestamp_ms"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                quote_volume=Decimal(row["quote_volume"]),
                trade_count=Decimal(row["trade_count"]),
                spread_bps=Decimal(row["spread_bps"]),
            ))
    return candles


def serializable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return [serializable(item) for item in value]
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fee-aware Smart Sniper historical backtest and walk-forward evaluation."
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--warmup-bars", type=int, default=240)
    parser.add_argument("--test-bars", type=int, default=120)
    parser.add_argument("--step-bars", type=int, default=120)
    args = parser.parse_args()

    candles = load_candles(args.csv)
    backtest = run_backtest(candles, warmup_bars=args.warmup_bars)
    report = walk_forward(
        candles,
        warmup_bars=args.warmup_bars,
        test_bars=args.test_bars,
        step_bars=args.step_bars,
    )
    payload = {
        "data_rows": len(candles),
        "backtest": asdict(backtest),
        "walk_forward": asdict(report),
        "limitations": [
            "Results are historical simulations, not forecasts or guarantees.",
            "Signals are evaluated at candle close and filled at the next candle open.",
            "Stops and targets are checked on candle closes; intrabar path is unavailable.",
            "Walk-forward windows are out-of-sample for a fixed heuristic, not ML training.",
            "The research seed ticket is 1 USDT; incremental sizing uses realized net profit only.",
        ],
    }
    print(json.dumps(serializable(payload), indent=2))


if __name__ == "__main__":
    main()
