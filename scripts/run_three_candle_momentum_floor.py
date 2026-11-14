from __future__ import annotations

import argparse
import hashlib
import json
from bisect import bisect_right
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from run_binance_walk_forward import fetch_history

from mumin_trader.research.walk_forward import HistoricalCandle, run_backtest
from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
BPS = Decimal("10000")
BASE_INTERVAL = "1h"
HIGHER_INTERVALS = ("4h", "1d")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT")
CANDLE_COUNT = 9940
DEVELOPMENT_BARS = 6958
VALIDATION_BARS = 1491
HOLDOUT_BARS = 1491
HOLDOUT_START = DEVELOPMENT_BARS + VALIDATION_BARS
SPREADS = (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"))
BASELINE_FLOOR = Decimal("5")
CANDIDATE_FLOOR = Decimal("0")
INITIAL_CASH = Decimal("70")


def _atr_percent(rows: list[dict], index: int) -> Decimal:
    window = rows[max(0, index - 14):index + 1]
    ranges: list[Decimal] = []
    for offset in range(1, len(window)):
        current, previous = window[offset], window[offset - 1]
        ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"]),
        ))
    if not ranges:
        return ZERO
    return sum(ranges[-14:], ZERO) / Decimal(len(ranges[-14:])) / rows[index]["close"] * ONE_HUNDRED


def _candles(rows: list[dict], spread: Decimal) -> list[HistoricalCandle]:
    return [HistoricalCandle(**row, spread_bps=spread) for row in rows]


def _aligned_higher(
    higher_rows: dict[str, list[dict]],
    timestamps: dict[str, list[int]],
    timestamp_ms: int,
) -> dict[str, list[Decimal]]:
    aligned: dict[str, list[Decimal]] = {}
    for slot, history in higher_rows.items():
        count = bisect_right(timestamps[slot], timestamp_ms)
        aligned[slot] = [item["close"] for item in history[max(0, count - 45):count]]
    return aligned


def _incremental_ledger(
    base: list[dict],
    higher_rows: dict[str, list[dict]],
    spread: Decimal,
    symbol: str,
) -> dict:
    higher_timestamps = {
        slot: [item["timestamp_ms"] for item in history]
        for slot, history in higher_rows.items()
    }
    baseline = SmartSniperStrategy(momentum_3_floor_bps=BASELINE_FLOOR)
    candidate = SmartSniperStrategy(momentum_3_floor_bps=CANDIDATE_FLOOR)
    shield = AntiLossQuantumShield()
    incremental: list[dict] = []
    shield_blocked = 0
    shield_passed = 0

    for index in range(HOLDOUT_START, CANDLE_COUNT):
        bar = base[index]
        closes = [item["close"] for item in base[max(0, index - 60):index + 1]]
        aligned = _aligned_higher(higher_rows, higher_timestamps, bar["timestamp_ms"])
        atr = _atr_percent(base, index)
        baseline_result = baseline.assess(
            closes, spread_bps=spread, higher_timeframe_closes=aligned, atr_percent=atr
        )
        candidate_result = candidate.assess(
            closes, spread_bps=spread, higher_timeframe_closes=aligned, atr_percent=atr
        )
        if baseline_result.signal == "HOLD" and candidate_result.signal == "BUY":
            previous_close = base[index - 1]["close"]
            move_bps = (bar["close"] / previous_close - Decimal("1")) * BPS
            prior = base[max(0, index - 20):index]
            volume_values = sorted(item["quote_volume"] for item in prior)
            trade_values = sorted(item["trade_count"] for item in prior)

            def median(values: list[Decimal]) -> Decimal | None:
                if len(values) != 20:
                    return None
                middle = len(values) // 2
                return values[middle] if len(values) % 2 else (
                    values[middle - 1] + values[middle]
                ) / Decimal("2")

            volume_median, trade_median = median(volume_values), median(trade_values)
            shield_result = shield.assess(
                spread_bps=spread,
                quote_volume_ratio=(
                    bar["quote_volume"] / volume_median
                    if volume_median is not None and volume_median > ZERO else None
                ),
                trade_count_ratio=(
                    bar["trade_count"] / trade_median
                    if trade_median is not None and trade_median > ZERO else None
                ),
                price_change_bps=move_bps,
                candle_age_ms=0,
            )
            if shield_result.block_entries:
                shield_blocked += 1
            else:
                shield_passed += 1
            incremental.append({
                "symbol": symbol,
                "timestamp_ms": bar["timestamp_ms"],
                "spread_bps": str(spread),
                "momentum_3_bps": str(candidate_result.momentum_3_bps),
                "momentum_12_bps": str(candidate_result.momentum_12_bps),
                "score": str(candidate_result.score),
                "rsi": str(candidate_result.rsi),
                "mtf_confluence": candidate_result.mtf_confluence,
                "atr_percent": str(candidate_result.atr_percent),
                "expected_move_bps": str(candidate_result.momentum_12_bps),
                "estimated_cost_bps": str(candidate_result.estimated_cost_bps),
                "baseline_signal": baseline_result.signal,
                "candidate_signal": candidate_result.signal,
                "candidate_reasons": list(candidate_result.reasons),
                "shield_blocked": shield_result.block_entries,
                "shield_reasons": list(shield_result.reasons),
            })

    return {
        "incremental_buy_signals_before_shield": len(incremental),
        "incremental_signals_blocked_by_shield": shield_blocked,
        "incremental_signals_passed_by_shield": shield_passed,
        "incremental_signal_ledger": incremental,
    }


def _metrics_dict(metrics) -> dict:
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
        "shield_blocks_market_candles": metrics.shield_blocks,
        "buy_candidates_before_shield": metrics.buy_candidates,
        "buy_candidates_blocked_by_shield": metrics.blocked_buy_candidates,
        "paper_entry_orders": metrics.entry_orders,
        "compounding_reinvested_usdt": str(metrics.compounding_reinvested),
    }


def _aggregate(rows: list[dict], variant: str) -> dict:
    selected = [item[variant] for item in rows]
    pnl = sum((Decimal(item["net_pnl_usdt"]) for item in selected), ZERO)
    initial = INITIAL_CASH * Decimal(len(selected))
    return {
        "net_pnl_usdt": str(pnl),
        "net_return_percent_on_aggregate_initial_cash": str(
            pnl / initial * ONE_HUNDRED if initial else ZERO
        ),
        "max_symbol_drawdown_percent": str(max(
            (Decimal(item["max_drawdown_percent"]) for item in selected), default=ZERO
        )),
        "closed_trades": sum(item["closed_trades"] for item in selected),
        "profitable_symbol_series": sum(
            Decimal(item["net_pnl_usdt"]) > ZERO for item in selected
        ),
        "paper_entry_orders": sum(item["paper_entry_orders"] for item in selected),
    }


def _acceptance(baseline: dict, candidate: dict, negative_controls: dict) -> dict:
    return {
        "candidate_aggregate_holdout_net_return_positive": Decimal(candidate["net_pnl_usdt"]) > ZERO,
        "candidate_beats_baseline_net_return": Decimal(candidate["net_pnl_usdt"]) > Decimal(baseline["net_pnl_usdt"]),
        "candidate_max_drawdown_not_above_baseline": Decimal(candidate["max_symbol_drawdown_percent"]) <= Decimal(baseline["max_symbol_drawdown_percent"]),
        "candidate_has_at_least_30_closed_trades": candidate["closed_trades"] >= 30,
        "candidate_has_at_least_5_of_8_profitable_symbol_series": candidate["profitable_symbol_series"] >= 5,
        "20_and_30_bps_negative_controls_zero_entries_both_variants": negative_controls["passed"],
    }


def run_research(client: httpx.Client) -> dict:
    symbol_results: list[dict] = []
    negative_control_rows: list[dict] = []
    all_incremental: list[dict] = []
    split_metadata: dict[str, dict] = {}

    for symbol in SYMBOLS:
        base = fetch_history(client, symbol, BASE_INTERVAL, 10000)[-CANDLE_COUNT:]
        if len(base) != CANDLE_COUNT:
            raise RuntimeError(f"{symbol}: expected exactly {CANDLE_COUNT} closed 1h candles, got {len(base)}.")
        higher_rows = {
            slot: fetch_history(client, symbol, interval, 10000)
            for slot, interval in zip(("higher_1", "higher_2"), HIGHER_INTERVALS)
        }
        split_metadata[symbol] = {
            "closed_1h_candles": len(base),
            "development_count": DEVELOPMENT_BARS,
            "validation_count": VALIDATION_BARS,
            "untouched_holdout_count": HOLDOUT_BARS,
            "development_start_utc": datetime.fromtimestamp(base[0]["timestamp_ms"] / 1000, UTC).isoformat(),
            "development_end_utc": datetime.fromtimestamp(base[DEVELOPMENT_BARS - 1]["timestamp_ms"] / 1000, UTC).isoformat(),
            "validation_start_utc": datetime.fromtimestamp(base[DEVELOPMENT_BARS]["timestamp_ms"] / 1000, UTC).isoformat(),
            "validation_end_utc": datetime.fromtimestamp(base[HOLDOUT_START - 1]["timestamp_ms"] / 1000, UTC).isoformat(),
            "holdout_start_utc": datetime.fromtimestamp(base[HOLDOUT_START]["timestamp_ms"] / 1000, UTC).isoformat(),
            "holdout_end_utc": datetime.fromtimestamp(base[-1]["timestamp_ms"] / 1000, UTC).isoformat(),
        }

        for spread in SPREADS:
            base_candles = _candles(base, spread)
            higher_candles = {
                slot: _candles(rows, spread) for slot, rows in higher_rows.items()
            }
            baseline_metrics = run_backtest(
                base_candles,
                warmup_bars=HOLDOUT_START,
                initial_cash=INITIAL_CASH,
                enable_shield=True,
                enable_compounding=False,
                higher_timeframes=higher_candles,
                momentum_3_floor_bps=BASELINE_FLOOR,
            )
            candidate_metrics = run_backtest(
                base_candles,
                warmup_bars=HOLDOUT_START,
                initial_cash=INITIAL_CASH,
                enable_shield=True,
                enable_compounding=False,
                higher_timeframes=higher_candles,
                momentum_3_floor_bps=CANDIDATE_FLOOR,
            )
            row = {
                "symbol": symbol,
                "spread_bps": str(spread),
                "baseline_floor": "> 5 bps",
                "candidate_floor": "> 0 bps",
                "baseline": _metrics_dict(baseline_metrics),
                "candidate": _metrics_dict(candidate_metrics),
            }
            symbol_results.append(row)
            if spread in (Decimal("5"), Decimal("10")):
                ledger = _incremental_ledger(base, higher_rows, spread, symbol)
                all_incremental.append({
                    "symbol": symbol,
                    "spread_bps": str(spread),
                    **{key: value for key, value in ledger.items() if key != "incremental_signal_ledger"},
                })
                row["incremental_signal_ledger"] = ledger["incremental_signal_ledger"]
            if spread in (Decimal("20"), Decimal("30")):
                negative_control_rows.append({
                    "symbol": symbol,
                    "spread_bps": str(spread),
                    "baseline_entries": baseline_metrics.entry_orders,
                    "candidate_entries": candidate_metrics.entry_orders,
                })

    aggregate_by_spread: dict[str, dict] = {}
    for spread in SPREADS:
        rows = [item for item in symbol_results if Decimal(item["spread_bps"]) == spread]
        baseline = _aggregate(rows, "baseline")
        candidate = _aggregate(rows, "candidate")
        negative = [item for item in negative_control_rows if Decimal(item["spread_bps"]) == spread]
        if spread in (Decimal("5"), Decimal("10")):
            controls_pass = all(
                item["baseline_entries"] == 0 and item["candidate_entries"] == 0
                for item in negative_control_rows
            )
            flags = _acceptance(baseline, candidate, {"passed": controls_pass})
            aggregate_by_spread[str(spread)] = {
                "baseline": baseline,
                "candidate": candidate,
                "acceptance_flags": flags,
                "overall_pass": all(flags.values()),
            }
        else:
            aggregate_by_spread[str(spread)] = {
                "baseline": baseline,
                "candidate": candidate,
                "negative_control_rows": negative,
                "zero_entries_both_variants_all_symbols": all(
                    item["baseline_entries"] == 0 and item["candidate_entries"] == 0
                    for item in negative
                ),
            }

    negative_controls_pass = all(
        item["baseline_entries"] == 0 and item["candidate_entries"] == 0
        for item in negative_control_rows
    )
    for spread in ("5", "10"):
        aggregate_by_spread[spread]["acceptance_flags"][
            "20_and_30_bps_negative_controls_zero_entries_both_variants"
        ] = negative_controls_pass
        aggregate_by_spread[spread]["overall_pass"] = all(
            aggregate_by_spread[spread]["acceptance_flags"].values()
        )

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
        "production_atr_band_percent": {"min": "0.08", "max": "1.20"},
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
            "untouched_holdout": HOLDOUT_BARS,
            "holdout_start_index": HOLDOUT_START,
            "policy": "Only the final 1491 candles are evaluated. Earlier development and validation bars are warm-up/history only; no thresholds are tuned.",
        },
        "candidate_definition": {
            "baseline_momentum_3_eligibility": "momentum_3_bps > 5",
            "candidate_momentum_3_eligibility": "momentum_3_bps > 0",
            "momentum_quality_bonus": "The +10 score bonus remains awarded only when momentum_3_bps > 5.",
            "recovery_branch": "unchanged",
            "all_other_gates": "unchanged",
        },
        "split_metadata_by_symbol": split_metadata,
        "aggregate_by_spread": aggregate_by_spread,
        "per_symbol_results": symbol_results,
        "incremental_signal_summary": all_incremental,
        "negative_control_results": negative_control_rows,
        "negative_controls_pass": negative_controls_pass,
        "limitations": [
            "Spread scenarios are fixed assumptions, not archived historical order-book spreads.",
            "The 1h bars are closed Binance public market data; 4h and 1d confirmation uses only higher-timeframe close timestamps not later than each 1h close.",
            "The final holdout is evaluated once and is not used for threshold selection.",
            "Paper fills, fees and slippage are simulated; this is not live execution or a profitability guarantee.",
            "The backtester evaluates stop/target exits on candle-close returns and does not reconstruct intrabar order paths.",
            "Incremental signal ledger is a paired per-candle strategy assessment; Paper entry-order deltas can differ because positions and execution state evolve.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paired Paper-only OOS ablation of the Smart Sniper three-candle momentum floor."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        payload = run_research(client)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    args.output.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    summary = {
        "output": str(args.output),
        "sha256": digest,
        "negative_controls_pass": payload["negative_controls_pass"],
        "spread_results": {
            spread: {
                "overall_pass": values.get("overall_pass"),
                "baseline_net_pnl_usdt": values["baseline"]["net_pnl_usdt"],
                "candidate_net_pnl_usdt": values["candidate"]["net_pnl_usdt"],
                "candidate_closed_trades": values["candidate"]["closed_trades"],
                "candidate_profitable_symbol_series": values["candidate"]["profitable_symbol_series"],
            }
            for spread, values in payload["aggregate_by_spread"].items()
        },
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
