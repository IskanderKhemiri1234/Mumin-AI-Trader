from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from mumin_trader.research.walk_forward import (
    INITIAL_CASH,
    HistoricalCandle,
    run_backtest,
)
from run_binance_walk_forward import ALLOWED, fetch_history

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT",
)
EXPECTED_BASE_CANDLES = 9940
WARMUP_BARS = 240
SPLIT_DEV_END = 6958
SPLIT_VALIDATION_END = 8449
SPLIT_HOLDOUT_END = EXPECTED_BASE_CANDLES
SPREADS_BPS = (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"))
VARIANTS = ("baseline", "relative_volume_candidate")


def _segment(
    base: list[HistoricalCandle],
    higher: dict[str, list[HistoricalCandle]],
    start: int,
    end: int,
    *,
    candidate: bool,
):
    context_start = max(0, start - WARMUP_BARS)
    selected_base = base[context_start:end]
    selected_higher = higher
    return run_backtest(
        selected_base,
        warmup_bars=start - context_start,
        initial_cash=INITIAL_CASH,
        enable_shield=True,
        enable_compounding=False,
        enable_liquidity_confirmation=candidate,
        higher_timeframes=selected_higher,
    )


def _metrics(metrics) -> dict:
    return {
        "start_index": metrics.start_index,
        "end_index": metrics.end_index,
        "starting_capital_usdt": str(INITIAL_CASH),
        "ending_equity_usdt": str(metrics.ending_equity),
        "net_pnl_usdt": str(metrics.ending_equity - INITIAL_CASH),
        "return_percent": str(metrics.total_return_percent),
        "max_drawdown_percent": str(metrics.max_drawdown_percent),
        "closed_trades": metrics.closed_trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate_percent": str(metrics.win_rate_percent),
        "profit_factor": str(metrics.profit_factor) if metrics.profit_factor is not None else None,
        "fees_paid_usdt": str(metrics.fees_paid),
        "shield_blocks": metrics.shield_blocks,
        "buy_candidates": metrics.buy_candidates,
        "shield_blocked_buy_candidates": metrics.blocked_buy_candidates,
        "liquidity_confirmation_blocks": metrics.liquidity_confirmation_blocks,
        "entry_orders": metrics.entry_orders,
    }


def _acceptance(holdout: list[dict]) -> dict:
    primary = [row for row in holdout if Decimal(row["spread_bps"]) in (Decimal("5"), Decimal("10"))]
    negative = [row for row in holdout if Decimal(row["spread_bps"]) in (Decimal("20"), Decimal("30"))]
    criteria: dict[str, dict] = {}

    for spread in (Decimal("5"), Decimal("10")):
        rows = [row for row in primary if Decimal(row["spread_bps"]) == spread]
        baseline = [row for row in rows if row["variant"] == "baseline"]
        candidate = [row for row in rows if row["variant"] == "relative_volume_candidate"]
        baseline_pnl = sum((Decimal(row["metrics"]["net_pnl_usdt"]) for row in baseline), Decimal("0"))
        candidate_pnl = sum((Decimal(row["metrics"]["net_pnl_usdt"]) for row in candidate), Decimal("0"))
        baseline_dd = max((Decimal(row["metrics"]["max_drawdown_percent"]) for row in baseline), default=Decimal("0"))
        candidate_dd = max((Decimal(row["metrics"]["max_drawdown_percent"]) for row in candidate), default=Decimal("0"))
        candidate_trades = sum(row["metrics"]["closed_trades"] for row in candidate)
        profitable_symbols = sum(
            Decimal(row["metrics"]["net_pnl_usdt"]) > Decimal("0")
            for row in candidate
        )
        complete = len(baseline) == len(SYMBOLS) and len(candidate) == len(SYMBOLS)
        criteria[f"spread_{spread}_bps"] = {
            "candidate_positive_net_pnl": {
                "pass": complete and candidate_pnl > Decimal("0"),
                "value_usdt": str(candidate_pnl),
            },
            "candidate_beats_paired_baseline": {
                "pass": complete and candidate_pnl > baseline_pnl,
                "candidate_net_pnl_usdt": str(candidate_pnl),
                "baseline_net_pnl_usdt": str(baseline_pnl),
            },
            "drawdown_no_worse_than_baseline": {
                "pass": complete and candidate_dd <= baseline_dd,
                "candidate_max_symbol_drawdown_percent": str(candidate_dd),
                "baseline_max_symbol_drawdown_percent": str(baseline_dd),
            },
            "at_least_30_closed_trades": {
                "pass": complete and candidate_trades >= 30,
                "candidate_closed_trades": candidate_trades,
            },
            "at_least_5_of_8_profitable_symbols": {
                "pass": complete and profitable_symbols >= 5,
                "profitable_candidate_symbols": profitable_symbols,
            },
        }

    negative_checks = []
    for spread in (Decimal("20"), Decimal("30")):
        rows = [row for row in negative if Decimal(row["spread_bps"]) == spread]
        zero = len(rows) == len(SYMBOLS) * len(VARIANTS) and all(
            row["metrics"]["entry_orders"] == 0 for row in rows
        )
        negative_checks.append({
            "spread_bps": str(spread),
            "pass": zero,
            "series_count": len(rows),
            "entries": sum(row["metrics"]["entry_orders"] for row in rows),
        })
    criteria["negative_control_zero_entries"] = {
        "pass": all(row["pass"] for row in negative_checks),
        "scenarios": negative_checks,
    }
    passed = sum(
        int(item["pass"])
        for key, item in criteria.items()
        if key == "negative_control_zero_entries"
    )
    passed += sum(
        int(check["pass"])
        for key, value in criteria.items()
        if key.startswith("spread_")
        for check in value.values()
    )
    return {
        "passed": passed,
        "required": 11,
        "all_pass": passed == 11,
        "criteria": criteria,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-pass paired 70/15/15 Paper holdout for relative-volume liquidity confirmation."
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/liquidity-confirmation-holdout.json"))
    args = parser.parse_args()

    results: list[dict] = []
    data_hash = hashlib.sha256()
    with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        for symbol in SYMBOLS:
            if symbol not in ALLOWED:
                raise RuntimeError(f"{symbol} is outside the research-only data universe.")
            base_rows = fetch_history(client, symbol, "1h", EXPECTED_BASE_CANDLES)
            if len(base_rows) != EXPECTED_BASE_CANDLES:
                raise RuntimeError(
                    f"{symbol}: expected exactly {EXPECTED_BASE_CANDLES} closed 1h candles; got {len(base_rows)}."
                )
            higher_rows = {
                "higher_1": fetch_history(client, symbol, "4h", 3000),
                "higher_2": fetch_history(client, symbol, "1d", 1000),
            }
            for row in base_rows:
                data_hash.update(
                    f'{row["timestamp_ms"]},{row["open"]},{row["high"]},{row["low"]},'
                    f'{row["close"]},{row["quote_volume"]},{row["trade_count"]}\n'.encode()
                )
            base_by_spread = {
                spread: [HistoricalCandle(**row, spread_bps=spread) for row in base_rows]
                for spread in SPREADS_BPS
            }
            higher_by_spread = {
                spread: {
                    slot: [HistoricalCandle(**row, spread_bps=spread) for row in values]
                    for slot, values in higher_rows.items()
                }
                for spread in SPREADS_BPS
            }

            for spread in SPREADS_BPS:
                for variant in VARIANTS:
                    candidate = variant == "relative_volume_candidate"
                    for segment_name, start, end in (
                        ("development", WARMUP_BARS, SPLIT_DEV_END),
                        ("validation", SPLIT_DEV_END, SPLIT_VALIDATION_END),
                        ("sealed_holdout", SPLIT_VALIDATION_END, SPLIT_HOLDOUT_END),
                    ):
                        metrics = _segment(
                            base_by_spread[spread],
                            higher_by_spread[spread],
                            start,
                            end,
                            candidate=candidate,
                        )
                        results.append({
                            "symbol": symbol,
                            "spread_bps": str(spread),
                            "variant": variant,
                            "segment": segment_name,
                            "metrics": _metrics(metrics),
                        })

    holdout = [row for row in results if row["segment"] == "sealed_holdout"]
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_commit_sha": __import__("os").environ.get("GITHUB_SHA", "local-unpinned"),
        "research_only": True,
        "paper_only": True,
        "live_orders_enabled": False,
        "starting_capital_usdt_per_symbol_variant": "70",
        "compounding_enabled": False,
        "base_timeframe": "1h",
        "higher_timeframes": ["4h", "1d"],
        "symbols": list(SYMBOLS),
        "closed_base_candles_per_symbol": EXPECTED_BASE_CANDLES,
        "chronological_split": {
            "development": {"start": 0, "end_exclusive": SPLIT_DEV_END, "bars": SPLIT_DEV_END},
            "validation": {"start": SPLIT_DEV_END, "end_exclusive": SPLIT_VALIDATION_END, "bars": 1491},
            "sealed_holdout": {"start": SPLIT_VALIDATION_END, "end_exclusive": SPLIT_HOLDOUT_END, "bars": 1491},
            "warmup_bars": WARMUP_BARS,
        },
        "candidate_rule": {
            "lookback_closed_bars": 20,
            "quote_volume_ratio_min_inclusive": "0.80",
            "quote_volume_ratio_max_inclusive": "2.50",
            "trade_count_ratio_min_inclusive": "0.80",
            "trade_count_ratio_max_inclusive": "2.50",
            "both_required": True,
            "missing_or_zero_baseline": "fail_closed",
        },
        "invariants": {
            "mtf_mandatory": True,
            "rsi_48_to_68_unchanged": True,
            "atr_0_08_to_1_20_unchanged": True,
            "three_candle_momentum_gt_5_bps_unchanged": True,
            "shield_enabled_and_unchanged": True,
            "fees_per_side": "0.001",
            "slippage_bps_per_side": "2",
            "spread_scenarios_bps": [str(value) for value in SPREADS_BPS],
        },
        "input_data_sha256": data_hash.hexdigest(),
        "results": results,
        "acceptance": _acceptance(holdout),
        "limitations": [
            "Quote volume and trade count are candle-level activity proxies, not order-book depth or executable liquidity.",
            "Spread is a fixed scenario, not archived historical order-book data.",
            "Only fully closed candles are used; higher-timeframe observations are timestamp aligned.",
            "Stops and targets are evaluated at candle closes; intrabar paths are not reconstructed.",
            "Development and validation metrics are descriptive; no parameter fitting is performed.",
            "The sealed holdout is evaluated once by this workflow after quality gates pass.",
            "Historical Paper simulation is not a forecast or guarantee.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode()
    args.output.write_bytes(encoded)
    artifact_hash = hashlib.sha256(encoded).hexdigest()
    print(json.dumps({
        "report_path": str(args.output),
        "report_sha256": artifact_hash,
        "holdout_acceptance": payload["acceptance"],
    }, indent=2))
    if not payload["acceptance"]["all_pass"]:
        raise SystemExit("Relative-volume candidate rejected: one or more frozen holdout criteria failed.")


if __name__ == "__main__":
    main()
