from __future__ import annotations

import argparse
import hashlib
import json
from bisect import bisect_right
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from run_binance_walk_forward import ALLOWED, fetch_history
from mumin_trader.research.score_floor_diagnostics import (
    is_score_only_near_miss,
    rejection_categories,
)
from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield
from mumin_trader.research.walk_forward import HistoricalCandle, _median
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT",
)
EXPECTED_BASE_CANDLES = 9940
WARMUP_BARS = 240
DEV_END = 6958
VALIDATION_END = 8449
SPREADS_BPS = (Decimal("5"), Decimal("10"))
SCORE_BASELINE = Decimal("75")
SCORE_CANDIDATE = Decimal("70")
ZERO = Decimal("0")
BPS = Decimal("10000")


def _to_candles(rows: list[dict], spread: Decimal) -> list[HistoricalCandle]:
    return [HistoricalCandle(**row, spread_bps=spread) for row in rows]


def _evaluate_symbol_segment(
    *,
    symbol: str,
    base: list[HistoricalCandle],
    higher: dict[str, list[HistoricalCandle]],
    segment: str,
    start: int,
    end: int,
    spread: Decimal,
) -> dict:
    baseline_strategy = SmartSniperStrategy(minimum_score=SCORE_BASELINE)
    candidate_strategy = SmartSniperStrategy(minimum_score=SCORE_CANDIDATE)
    shield = AntiLossQuantumShield()
    higher_indexes = {
        key: ([c.timestamp_ms for c in rows], rows)
        for key, rows in higher.items()
    }
    counts = {
        "raw_assessments": 0,
        "baseline_buy_before_shield": 0,
        "shield_blocked_baseline_buys": 0,
        "candidate_buy_before_shield": 0,
        "score_only_near_misses_before_shield": 0,
        "score_only_near_misses_after_shield": 0,
        "entries_available_to_candidate": 0,
    }
    category_counts: dict[str, int] = {}
    waterfall: dict[str, int] = {}
    score_bands = {"below_70": 0, "70_to_lt_75": 0, "75_plus": 0}
    by_regime: dict[str, dict[str, int]] = {}
    by_strategy: dict[str, dict[str, int]] = {}
    near_miss_by_regime: dict[str, int] = {}
    near_miss_by_strategy: dict[str, int] = {}
    near_miss_timestamps: set[int] = set()
    first_failure_order = (
        "mtf", "momentum", "rsi", "atr", "spread",
        "expected_move_cost", "score_floor",
    )

    for index in range(start, end):
        bar = base[index]
        closes = [item.close for item in base[max(0, index - 60):index + 1]]
        aligned: dict[str, list[Decimal]] = {}
        for timeframe, (timestamps, rows) in higher_indexes.items():
            eligible_count = bisect_right(timestamps, bar.timestamp_ms)
            aligned[timeframe] = [
                item.close for item in rows[max(0, eligible_count - 45):eligible_count]
            ]

        atr_window = base[max(0, index - 14):index + 1]
        true_ranges = []
        for offset in range(1, len(atr_window)):
            current = atr_window[offset]
            previous_close = atr_window[offset - 1].close
            true_ranges.append(max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            ))
        atr_values = true_ranges[-14:]
        atr_percent = (
            sum(atr_values, ZERO) / Decimal(len(atr_values)) / bar.close * Decimal("100")
            if atr_values else ZERO
        )
        baseline = baseline_strategy.assess(
            closes, spread_bps=spread,
            higher_timeframe_closes=aligned, atr_percent=atr_percent,
        )
        candidate = candidate_strategy.assess(
            closes, spread_bps=spread,
            higher_timeframe_closes=aligned, atr_percent=atr_percent,
        )
        counts["raw_assessments"] += 1
        score = baseline.score
        if score < SCORE_CANDIDATE:
            score_bands["below_70"] += 1
        elif score < SCORE_BASELINE:
            score_bands["70_to_lt_75"] += 1
        else:
            score_bands["75_plus"] += 1

        categories = rejection_categories(baseline)
        for category in categories:
            category_counts[category] = category_counts.get(category, 0) + 1
        first_failure = next(
            (category for category in first_failure_order if category in categories),
            "other_or_not_buy_setup" if baseline.signal != "BUY" else "passed_strategy",
        )
        waterfall[first_failure] = waterfall.get(first_failure, 0) + 1

        regime_bucket = by_regime.setdefault(
            baseline.regime, {"assessments": 0, "baseline_buys": 0, "score_only_near_misses": 0}
        )
        strategy_bucket = by_strategy.setdefault(
            baseline.strategy, {"assessments": 0, "baseline_buys": 0, "score_only_near_misses": 0}
        )
        for bucket in (regime_bucket, strategy_bucket):
            bucket["assessments"] += 1
            bucket["baseline_buys"] += int(baseline.signal == "BUY")

        previous = base[index - 1].close if index > 0 else bar.close
        price_change_bps = (bar.close / previous - Decimal("1")) * BPS
        prior = base[max(0, index - 20):index]
        median_volume = _median([item.quote_volume for item in prior])
        median_trades = _median([item.trade_count for item in prior])
        volume_ratio = (
            bar.quote_volume / median_volume
            if len(prior) == 20 and median_volume is not None and median_volume > ZERO
            else None
        )
        trade_ratio = (
            bar.trade_count / median_trades
            if len(prior) == 20 and median_trades is not None and median_trades > ZERO
            else None
        )
        shield_decision = shield.assess(
            spread_bps=spread,
            quote_volume_ratio=volume_ratio,
            trade_count_ratio=trade_ratio,
            price_change_bps=price_change_bps,
            candle_age_ms=0,
        )
        if baseline.signal == "BUY":
            counts["baseline_buy_before_shield"] += 1
            if shield_decision.block_entries:
                counts["shield_blocked_baseline_buys"] += 1
        if candidate.signal == "BUY":
            counts["candidate_buy_before_shield"] += 1
        if is_score_only_near_miss(baseline, candidate):
            counts["score_only_near_misses_before_shield"] += 1
            near_miss_timestamps.add(bar.timestamp_ms)
            near_miss_by_regime[baseline.regime] = near_miss_by_regime.get(baseline.regime, 0) + 1
            near_miss_by_strategy[baseline.strategy] = near_miss_by_strategy.get(baseline.strategy, 0) + 1
            regime_bucket["score_only_near_misses"] += 1
            strategy_bucket["score_only_near_misses"] += 1
            if not shield_decision.block_entries:
                counts["score_only_near_misses_after_shield"] += 1
                counts["entries_available_to_candidate"] += 1

    return {
        "symbol": symbol,
        "segment": segment,
        "spread_bps": str(spread),
        "evaluated_bars": end - start,
        "counts": counts,
        "score_distribution": score_bands,
        "independent_rejection_category_counts": category_counts,
        "mutually_exclusive_first_failure_waterfall": waterfall,
        "score_only_near_misses_by_regime": near_miss_by_regime,
        "score_only_near_misses_by_strategy": near_miss_by_strategy,
        "assessment_counts_by_regime": by_regime,
        "assessment_counts_by_strategy": by_strategy,
        "unique_near_miss_timestamps": len(near_miss_timestamps),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Development/validation-only score-floor diagnostic. Never evaluates sealed holdout."
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("artifacts/score-floor-throughput-diagnostic.json"),
    )
    args = parser.parse_args()
    rows_out: list[dict] = []
    data_hash = hashlib.sha256()

    with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        for symbol in SYMBOLS:
            if symbol not in ALLOWED:
                raise RuntimeError(f"{symbol} is outside the research-only universe.")
            raw_base = fetch_history(client, symbol, "1h", EXPECTED_BASE_CANDLES)
            if len(raw_base) != EXPECTED_BASE_CANDLES:
                raise RuntimeError(
                    f"{symbol}: expected {EXPECTED_BASE_CANDLES} closed 1h bars; got {len(raw_base)}."
                )
            raw_higher = {
                "higher_1": fetch_history(client, symbol, "4h", 3000),
                "higher_2": fetch_history(client, symbol, "1d", 1000),
            }
            # Hash and evaluate only the development + validation range.
            for row in raw_base[:VALIDATION_END]:
                data_hash.update(
                    f'{row["timestamp_ms"]},{row["open"]},{row["high"]},{row["low"]},'
                    f'{row["close"]},{row["quote_volume"]},{row["trade_count"]}\n'.encode()
                )
            for spread in SPREADS_BPS:
                base = _to_candles(raw_base, spread)
                higher = {
                    key: _to_candles(values, spread)
                    for key, values in raw_higher.items()
                }
                rows_out.append(_evaluate_symbol_segment(
                    symbol=symbol, base=base, higher=higher,
                    segment="development", start=WARMUP_BARS, end=DEV_END, spread=spread,
                ))
                rows_out.append(_evaluate_symbol_segment(
                    symbol=symbol, base=base, higher=higher,
                    segment="validation", start=DEV_END, end=VALIDATION_END, spread=spread,
                ))

    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "research_only": True,
        "paper_only": True,
        "live_orders_enabled": False,
        "sealed_holdout_evaluated": False,
        "capital_reference_usdt_per_symbol_variant": "70",
        "compounding_enabled": False,
        "baseline_minimum_score": str(SCORE_BASELINE),
        "candidate_minimum_score": str(SCORE_CANDIDATE),
        "candidate_eligible_score_band": "[70,75)",
        "symbols": list(SYMBOLS),
        "closed_1h_candles_per_symbol": EXPECTED_BASE_CANDLES,
        "evaluated_index_ranges": {
            "development": {"start": WARMUP_BARS, "end_exclusive": DEV_END},
            "validation": {"start": DEV_END, "end_exclusive": VALIDATION_END},
            "sealed_holdout": {"start": VALIDATION_END, "end_exclusive": EXPECTED_BASE_CANDLES, "evaluated": False},
        },
        "warmup_bars": WARMUP_BARS,
        "spread_scenarios_bps": [str(item) for item in SPREADS_BPS],
        "input_data_sha256_dev_validation_only": data_hash.hexdigest(),
        "results": rows_out,
        "pre_registered_stop_rule": {
            "proceed_only_if": "At least 30 distinct score-only near-miss timestamps in aggregate after Shield across development plus validation, and at least 4 of 8 symbols contribute at least one such opportunity at either primary spread.",
            "holdout_authorized_by_this_run": False,
        },
        "limitations": [
            "Scores are heuristic rankings, not calibrated probabilities.",
            "A near miss is counted only when candidate 70 changes HOLD to BUY and baseline 75 rejects solely for the score floor.",
            "All non-score strategy conditions are evaluated identically for both variants.",
            "Shield is applied after the strategy assessment and remains unchanged.",
            "Only fully closed 1h bars and higher-timeframe bars with close timestamps no later than the decision bar are used.",
            "No sealed-holdout observations are evaluated or summarized.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode()
    args.output.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    near_misses = sum(
        row["counts"]["score_only_near_misses_after_shield"] for row in rows_out
    )
    contributors = {
        row["symbol"]
        for row in rows_out
        if row["counts"]["score_only_near_misses_after_shield"] > 0
    }
    decision = near_misses >= 30 and len(contributors) >= 4
    print(json.dumps({
        "report_path": str(args.output),
        "report_sha256": digest,
        "development_validation_score_only_near_misses_after_shield": near_misses,
        "contributing_symbols": sorted(contributors),
        "contributing_symbol_count": len(contributors),
        "diagnostic_stop_rule_pass": decision,
        "sealed_holdout_evaluated": False,
    }, indent=2))
    if not decision:
        raise SystemExit("Diagnostic stop rule failed; do not run or unseal the Holdout.")


if __name__ == "__main__":
    main()
