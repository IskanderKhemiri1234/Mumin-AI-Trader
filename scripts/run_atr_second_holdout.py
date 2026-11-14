from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from run_atr_calibration import (
    ACTIVE_SPREADS,
    STRESS_SPREADS,
    AtrBand,
    _aggregate,
    _atr_series,
    _evaluate_with_band,
    _metrics_payload,
)
from run_binance_walk_forward import ALLOWED

from mumin_trader.research.walk_forward import HistoricalCandle

BASE_URL = "https://data-api.binance.vision"
SYMBOLS = tuple(sorted(ALLOWED))
INTERVALS = ("1h", "4h", "1d")
INITIAL_CASH = Decimal(70)
# Frozen in advance: this period ends more than eight months before the
# first calibration holdout began (2026-09-12 UTC).
WARMUP_START = "2025-08-01T00:00:00+00:00"
TEST_START = "2025-10-01T00:00:00+00:00"
TEST_END = "2026-01-01T00:00:00+00:00"
BASELINE = AtrBand("baseline_0.08_1.20", Decimal("0.08"), Decimal("1.20"))
FROZEN_CANDIDATE = AtrBand("frozen_candidate_0.02_3.00", Decimal("0.02"), Decimal("3.00"))


def _epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _fetch_period(
    client: httpx.Client,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    response = client.get(f"{BASE_URL}/api/v3/klines", params={
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms - 1,
        "limit": 1000,
    })
    response.raise_for_status()
    rows = response.json()
    collected: dict[int, list] = {}
    while rows:
        for row in rows:
            open_ms = int(row[0])
            close_ms = int(row[6])
            if start_ms <= close_ms < end_ms:
                collected[open_ms] = row
        last_open_ms = int(rows[-1][0])
        next_start = last_open_ms + 1
        if next_start >= end_ms or len(rows) < 1000:
            break
        response = client.get(f"{BASE_URL}/api/v3/klines", params={
            "symbol": symbol,
            "interval": interval,
            "startTime": next_start,
            "endTime": end_ms - 1,
            "limit": 1000,
        })
        response.raise_for_status()
        rows = response.json()

    candles = [{
        "timestamp_ms": int(row[6]),
        "open": Decimal(str(row[1])),
        "high": Decimal(str(row[2])),
        "low": Decimal(str(row[3])),
        "close": Decimal(str(row[4])),
        "quote_volume": Decimal(str(row[7])),
        "trade_count": Decimal(str(row[8])),
    } for _, row in sorted(collected.items())]
    if len(candles) < 60:
        raise RuntimeError(
            f"Insufficient historical candles for {symbol} {interval}: {len(candles)}"
        )
    return candles


def _series_summary(items: list[dict]) -> dict[str, object]:
    result = _aggregate(items)
    result["positive_series"] = sum(
        Decimal(str(item["return_percent"])) > 0 for item in items
    )
    result["negative_series"] = sum(
        Decimal(str(item["return_percent"])) < 0 for item in items
    )
    return result


def main() -> None:
    warmup_start_ms = _epoch_ms(WARMUP_START)
    test_start_ms = _epoch_ms(TEST_START)
    test_end_ms = _epoch_ms(TEST_END)
    histories: dict[tuple[str, str], list[dict]] = {}
    with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        server = client.get(f"{BASE_URL}/api/v3/time")
        server.raise_for_status()
        server_time = int(server.json()["serverTime"])
        if test_end_ms > server_time:
            raise RuntimeError("The frozen second holdout period must be fully historical.")
        for symbol in SYMBOLS:
            for interval in INTERVALS:
                histories[(symbol, interval)] = _fetch_period(
                    client, symbol, interval, warmup_start_ms, test_end_ms
                )

    results: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        raw = histories[(symbol, "1h")]
        start_index = next(
            (index for index, row in enumerate(raw) if row["timestamp_ms"] >= test_start_ms),
            None,
        )
        if start_index is None or start_index < 60:
            raise RuntimeError(f"Missing causal warm-up/test boundary for {symbol}.")
        if raw[-1]["timestamp_ms"] < test_end_ms - 60 * 60 * 1000:
            raise RuntimeError(f"Historical 1h data ends early for {symbol}.")

        atr_values = _atr_series([
            HistoricalCandle(**row, spread_bps=Decimal(5)) for row in raw
        ])
        for spread in (*ACTIVE_SPREADS, *STRESS_SPREADS):
            candles = [
                HistoricalCandle(**row, spread_bps=spread) for row in raw
            ]
            higher = {
                slot: [
                    HistoricalCandle(**row, spread_bps=spread)
                    for row in histories[(symbol, interval)]
                ]
                for slot, interval in zip(("higher_1", "higher_2"), ("4h", "1d"))
            }
            comparison = {}
            for band in (BASELINE, FROZEN_CANDIDATE):
                metrics = _evaluate_with_band(
                    candles, higher, atr_values, start_index, len(candles), band
                )
                comparison[band.name] = _metrics_payload(metrics)
            results.append({
                "symbol": symbol,
                "interval": "1h",
                "spread_assumption_bps": str(spread),
                "warmup_bars_before_test": start_index,
                "test_start_timestamp_ms": raw[start_index]["timestamp_ms"],
                "test_end_timestamp_ms": raw[-1]["timestamp_ms"],
                "baseline": comparison[BASELINE.name],
                "frozen_candidate": comparison[FROZEN_CANDIDATE.name],
            })

    summary: dict[str, dict[str, object]] = {}
    for spread in (*ACTIVE_SPREADS, *STRESS_SPREADS):
        rows = [item for item in results if item["spread_assumption_bps"] == str(spread)]
        baseline_rows = [item["baseline"] for item in rows]
        candidate_rows = [item["frozen_candidate"] for item in rows]
        summary[str(spread)] = {
            "baseline": _series_summary(baseline_rows),
            "frozen_candidate": _series_summary(candidate_rows),
        }

    active_summaries = [summary[str(spread)] for spread in ACTIVE_SPREADS]
    acceptance = {
        "requires_positive_candidate_mean_return_at_5_and_10_bps": all(
            Decimal(str(item["frozen_candidate"]["mean_return_percent"])) > 0
            for item in active_summaries
        ),
        "requires_candidate_to_beat_baseline_at_5_and_10_bps": all(
            Decimal(str(item["frozen_candidate"]["mean_return_percent"]))
            > Decimal(str(item["baseline"]["mean_return_percent"]))
            for item in active_summaries
        ),
        "requires_candidate_drawdown_not_to_exceed_baseline_at_5_and_10_bps": all(
            Decimal(str(item["frozen_candidate"]["mean_max_drawdown_percent"]))
            <= Decimal(str(item["baseline"]["mean_max_drawdown_percent"]))
            for item in active_summaries
        ),
        "requires_at_least_30_candidate_closed_trades_at_5_and_10_bps": all(
            int(item["frozen_candidate"]["total_closed_trades"]) >= 30
            for item in active_summaries
        ),
        "requires_at_least_5_of_8_positive_symbol_series_at_5_and_10_bps": all(
            int(item["frozen_candidate"]["positive_series"]) >= 5
            for item in active_summaries
        ),
    }
    acceptance["overall_pass"] = all(acceptance.values())

    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": "BINANCE_PUBLIC_HISTORICAL_CLOSED_CANDLES",
        "research_only": True,
        "production_strategy_modified": False,
        "paper_only": True,
        "capital_reference_usdt_per_independent_series": str(INITIAL_CASH),
        "stop_loss_percent_fixed": "0.50",
        "take_profit_rule": "unchanged strategy-selected 1.25%-1.50%",
        "shield_enabled": True,
        "compounding_enabled": False,
        "fees_and_execution_costs": {
            "taker_fee_per_side_percent": "0.10",
            "slippage_bps_per_side": "2",
            "spread_scenarios_bps": ["5", "10", "20", "30"],
            "spread_fill_model": "half spread on entry and half spread on exit",
        },
        "frozen_hypothesis": FROZEN_CANDIDATE.as_dict(),
        "comparison_baseline": BASELINE.as_dict(),
        "historical_period": {
            "warmup_start_utc": WARMUP_START,
            "test_start_utc": TEST_START,
            "test_end_exclusive_utc": TEST_END,
            "first_holdout_start_utc": "2026-09-12T18:00:00+00:00",
            "periods_overlap": False,
            "parameter_recalibration_performed": False,
            "selection_or_tuning_on_second_period": False,
        },
        "method": (
            "One fixed, pre-registered 1h out-of-sample period. Prior closed bars "
            "are used only for indicator warm-up and higher-timeframe alignment. "
            "The baseline and frozen candidate are evaluated on identical candles."
        ),
        "acceptance_criteria": acceptance,
        "summary_by_spread_bps": summary,
        "per_symbol_results": results,
        "limitations": [
            "Each symbol/spread is an independent 70 USDT Paper simulation, not a combined portfolio.",
            "Historical candles do not reproduce historical order-book depth; spread values are explicit assumptions.",
            "Stops and targets use the existing candle-close simulator, not intrabar order-book paths.",
            "A pass is necessary but not sufficient for production approval; a positive historical result is not a guarantee of future profitability.",
            "No live order execution, production strategy edits, risk-limit changes, or allowlist changes occur in this workflow.",
        ],
    }
    output = Path("artifacts/smart-sniper-atr-second-holdout-1h.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
