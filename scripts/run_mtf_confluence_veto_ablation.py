from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from run_binance_walk_forward import fetch_history

from mumin_trader.research.walk_forward import HistoricalCandle, run_backtest

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
BASE_INTERVAL = "1h"
HIGHER_INTERVALS = ("4h", "1d")
SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT",
)
CANDLE_COUNT = 9940
DEVELOPMENT_BARS = 6958
VALIDATION_BARS = 1491
HOLDOUT_BARS = 1491
HOLDOUT_START = DEVELOPMENT_BARS + VALIDATION_BARS
SPREADS = (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"))
INITIAL_CASH = Decimal("70")
RSI_MIN = Decimal("48")
RSI_MAX = Decimal("68")


def _candles(rows: list[dict], spread: Decimal) -> list[HistoricalCandle]:
    return [HistoricalCandle(**row, spread_bps=spread) for row in rows]


def _metrics(metrics) -> dict:
    return {
        "initial_cash_usdt": str(INITIAL_CASH),
        "ending_equity_usdt": str(metrics.ending_equity),
        "net_pnl_usdt": str(metrics.ending_equity - INITIAL_CASH),
        "net_return_percent": str(metrics.total_return_percent),
        "max_drawdown_percent": str(metrics.max_drawdown_percent),
        "closed_trades": metrics.closed_trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate_percent": str(metrics.win_rate_percent),
        "profit_factor": str(metrics.profit_factor) if metrics.profit_factor is not None else None,
        "fees_paid_usdt": str(metrics.fees_paid),
        "shield_blocks": metrics.shield_blocks,
        "buy_candidates": metrics.buy_candidates,
        "blocked_buy_candidates": metrics.blocked_buy_candidates,
        "paper_entry_orders": metrics.entry_orders,
    }


def _aggregate(rows: list[dict], variant: str) -> dict:
    selected = [row[variant] for row in rows]
    pnl = sum((Decimal(row["net_pnl_usdt"]) for row in selected), ZERO)
    initial = INITIAL_CASH * Decimal(len(selected))
    return {
        "net_pnl_usdt": str(pnl),
        "net_return_percent_on_aggregate_initial_cash": str(
            pnl / initial * ONE_HUNDRED if initial else ZERO
        ),
        "max_symbol_drawdown_percent": str(max(
            (Decimal(row["max_drawdown_percent"]) for row in selected), default=ZERO
        )),
        "closed_trades": sum(row["closed_trades"] for row in selected),
        "profitable_symbol_series": sum(
            Decimal(row["net_pnl_usdt"]) > ZERO for row in selected
        ),
        "paper_entry_orders": sum(row["paper_entry_orders"] for row in selected),
    }


def _acceptance(baseline: dict, candidate: dict, negative_controls_pass: bool) -> dict:
    return {
        "candidate_aggregate_holdout_net_return_positive": (
            Decimal(candidate["net_pnl_usdt"]) > ZERO
        ),
        "candidate_beats_baseline_net_return": (
            Decimal(candidate["net_pnl_usdt"]) > Decimal(baseline["net_pnl_usdt"])
        ),
        "candidate_max_drawdown_not_above_baseline": (
            Decimal(candidate["max_symbol_drawdown_percent"])
            <= Decimal(baseline["max_symbol_drawdown_percent"])
        ),
        "candidate_has_at_least_30_closed_trades": candidate["closed_trades"] >= 30,
        "candidate_has_at_least_5_of_8_profitable_symbol_series": (
            candidate["profitable_symbol_series"] >= 5
        ),
        "20_and_30_bps_negative_controls_zero_entries_both_variants": (
            negative_controls_pass
        ),
    }


def run_research(client: httpx.Client) -> dict:
    rows: list[dict] = []
    split_metadata: dict[str, dict] = {}
    negative_control_rows: list[dict] = []

    for symbol in SYMBOLS:
        base = fetch_history(client, symbol, BASE_INTERVAL, 10000)[-CANDLE_COUNT:]
        if len(base) != CANDLE_COUNT:
            raise RuntimeError(
                f"{symbol}: expected {CANDLE_COUNT} closed 1h candles, got {len(base)}."
            )
        higher = {
            slot: fetch_history(client, symbol, interval, 10000)
            for slot, interval in zip(("higher_1", "higher_2"), HIGHER_INTERVALS)
        }

        def candle_time(index: int) -> str:
            return datetime.fromtimestamp(
                base[index]["timestamp_ms"] / 1000, UTC
            ).isoformat()

        split_metadata[symbol] = {
            "closed_1h_candles": len(base),
            "development_count": DEVELOPMENT_BARS,
            "validation_count": VALIDATION_BARS,
            "sealed_holdout_count": HOLDOUT_BARS,
            "development_start_utc": candle_time(0),
            "development_end_utc": candle_time(DEVELOPMENT_BARS - 1),
            "validation_start_utc": candle_time(DEVELOPMENT_BARS),
            "validation_end_utc": candle_time(HOLDOUT_START - 1),
            "holdout_start_utc": candle_time(HOLDOUT_START),
            "holdout_end_utc": candle_time(-1),
        }

        base_by_spread = {
            spread: _candles(base, spread) for spread in SPREADS
        }
        higher_by_spread = {
            spread: {
                slot: _candles(history, spread)
                for slot, history in higher.items()
            }
            for spread in SPREADS
        }

        for spread in SPREADS:
            common = {
                "warmup_bars": HOLDOUT_START,
                "initial_cash": INITIAL_CASH,
                "enable_shield": True,
                "enable_compounding": False,
                "higher_timeframes": higher_by_spread[spread],
                "final_momentum_rsi_min": RSI_MIN,
                "final_momentum_rsi_max": RSI_MAX,
            }
            baseline = run_backtest(
                base_by_spread[spread],
                require_mtf_confluence=True,
                **common,
            )
            candidate = run_backtest(
                base_by_spread[spread],
                require_mtf_confluence=False,
                **common,
            )
            row = {
                "symbol": symbol,
                "spread_bps": str(spread),
                "baseline_mtf_final_veto": "required",
                "candidate_mtf_final_veto": "disabled",
                "rsi_final_eligibility_both_variants": "48–68",
                "baseline": _metrics(baseline),
                "candidate": _metrics(candidate),
            }
            rows.append(row)
            if spread in (Decimal("20"), Decimal("30")):
                negative_control_rows.append({
                    "symbol": symbol,
                    "spread_bps": str(spread),
                    "baseline_entries": baseline.entry_orders,
                    "candidate_entries": candidate.entry_orders,
                })

    aggregates: dict[str, dict] = {}
    negative_controls_pass = all(
        row["baseline_entries"] == 0 and row["candidate_entries"] == 0
        for row in negative_control_rows
    )
    for spread in SPREADS:
        selected = [row for row in rows if Decimal(row["spread_bps"]) == spread]
        baseline_aggregate = _aggregate(selected, "baseline")
        candidate_aggregate = _aggregate(selected, "candidate")
        if spread in (Decimal("5"), Decimal("10")):
            flags = _acceptance(
                baseline_aggregate, candidate_aggregate, negative_controls_pass
            )
            aggregates[str(spread)] = {
                "baseline": baseline_aggregate,
                "candidate": candidate_aggregate,
                "acceptance_flags": flags,
                "passed_all_six": all(flags.values()),
            }
        else:
            aggregates[str(spread)] = {
                "baseline": baseline_aggregate,
                "candidate": candidate_aggregate,
                "negative_controls_zero_entries": all(
                    row["baseline_entries"] == 0 and row["candidate_entries"] == 0
                    for row in selected
                ),
            }

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "research_only": True,
        "paper_only": True,
        "live_orders_enabled": False,
        "production_strategy_modified": False,
        "production_risk_limits_modified": False,
        "execution_allowlist_modified": False,
        "capital_reference_usdt_per_symbol_per_variant": "70",
        "stop_loss_percent": "0.50",
        "take_profit_rule": "unchanged strategy-selected 1.25%-1.50%",
        "rsi_final_eligibility_both_variants": {"min": "48", "max": "68"},
        "mtf_quality_rewards": {"momentum": "25", "recovery": "20"},
        "recovery_setup_and_scoring": "unchanged",
        "production_mtf_veto_default": True,
        "fees_each_side_percent": "0.10",
        "slippage_each_side_bps": "2",
        "shield_enabled": True,
        "compounding_enabled": False,
        "universe": list(SYMBOLS),
        "base_interval": BASE_INTERVAL,
        "higher_timeframes": list(HIGHER_INTERVALS),
        "closed_candle_count_per_symbol": CANDLE_COUNT,
        "chronological_split": {
            "development": DEVELOPMENT_BARS,
            "validation": VALIDATION_BARS,
            "sealed_holdout": HOLDOUT_BARS,
            "holdout_start_index": HOLDOUT_START,
            "policy": (
                "Only the final 1491 candles are simulated; earlier periods provide "
                "strictly prior indicator and higher-timeframe history. No tuning."
            ),
        },
        "candidate_definition": {
            "baseline": "require_mtf_confluence=True",
            "candidate": "require_mtf_confluence=False",
            "only_changed_predicate": "final hard MTF confluence veto",
            "momentum_mtf_quality_bonus": "+25 unchanged",
            "recovery_mtf_quality_bonus": "+20 unchanged",
            "recovery_setup_scoring_and_eligibility": "unchanged",
            "all_other_gates": "unchanged",
        },
        "split_metadata_by_symbol": split_metadata,
        "aggregate_by_spread": aggregates,
        "per_symbol_results": rows,
        "negative_control_results": negative_control_rows,
        "negative_controls_pass": negative_controls_pass,
        "limitations": [
            "Spread scenarios are fixed assumptions, not archived historical order-book spreads.",
            "Only fully closed higher-timeframe candles at or before each 1h close are aligned.",
            "Paper fills, fees and slippage are simulated, not live execution.",
            (
                "The backtester evaluates exits on candle-close returns and does not "
                "reconstruct intrabar order paths."
            ),
            "The final holdout is evaluated once and is not used for threshold selection.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paired Paper-only OOS ablation of the final MTF confluence veto."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        payload = run_research(client)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    args.output.write_bytes(encoded)
    summary = {
        "output": str(args.output),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "negative_controls_pass": payload["negative_controls_pass"],
        "spread_results": {
            spread: {
                "passed_all_six": result.get("passed_all_six"),
                "baseline_net_pnl_usdt": result["baseline"]["net_pnl_usdt"],
                "candidate_net_pnl_usdt": result["candidate"]["net_pnl_usdt"],
                "candidate_closed_trades": result["candidate"]["closed_trades"],
                "candidate_profitable_symbol_series": (
                    result["candidate"]["profitable_symbol_series"]
                ),
            }
            for spread, result in payload["aggregate_by_spread"].items()
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
