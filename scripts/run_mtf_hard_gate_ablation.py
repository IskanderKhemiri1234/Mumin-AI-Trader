from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from run_binance_walk_forward import ALLOWED, HIGHER_TIMEFRAMES, fetch_history
from mumin_trader.research.walk_forward import HistoricalCandle, run_backtest
from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
BPS = Decimal("10000")
INITIAL_CASH = Decimal("70")
HISTORY_BARS = 9940
DEVELOPMENT_BARS = 6958
VALIDATION_BARS = 1491
HOLDOUT_BARS = 1491
SPREADS = (Decimal("5"), Decimal("10"), Decimal("20"), Decimal("30"))
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LINKUSDT")


def _candles(rows: list[dict], spread: Decimal) -> list[HistoricalCandle]:
    return [HistoricalCandle(**row, spread_bps=spread) for row in rows]


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


def _median(values: list[Decimal]) -> Decimal | None:
    if len(values) != 20:
        return None
    ordered = sorted(values)
    return (ordered[9] + ordered[10]) / Decimal("2")


def _incremental_signals(
    rows: list[dict],
    higher: dict[str, list[dict]],
    spread: Decimal,
    holdout_start: int,
) -> list[dict]:
    baseline = SmartSniperStrategy(enforce_mtf_hard_gate=True)
    candidate = SmartSniperStrategy(enforce_mtf_hard_gate=False)
    shield = AntiLossQuantumShield()
    indexed = {
        key: ([item["timestamp_ms"] for item in values], values)
        for key, values in higher.items()
    }
    found: list[dict] = []
    for index in range(max(60, holdout_start), len(rows)):
        bar = rows[index]
        closes = [item["close"] for item in rows[max(0, index - 60):index + 1]]
        aligned: dict[str, list[Decimal]] = {}
        for slot, (timestamps, history) in indexed.items():
            count = bisect_right(timestamps, bar["timestamp_ms"])
            aligned[slot] = [
                item["close"] for item in history[max(0, count - 45):count]
            ]
        atr = _atr_percent(rows, index)
        base_assessment = baseline.assess(
            closes, spread_bps=spread, higher_timeframe_closes=aligned, atr_percent=atr,
        )
        ablated_assessment = candidate.assess(
            closes, spread_bps=spread, higher_timeframe_closes=aligned, atr_percent=atr,
        )
        if base_assessment.signal == "BUY" or ablated_assessment.signal != "BUY":
            continue

        prior = rows[max(0, index - 20):index]
        median_volume = _median([item["quote_volume"] for item in prior])
        median_trades = _median([item["trade_count"] for item in prior])
        volume_ratio = (
            bar["quote_volume"] / median_volume
            if median_volume is not None and median_volume > ZERO else None
        )
        trade_ratio = (
            bar["trade_count"] / median_trades
            if median_trades is not None and median_trades > ZERO else None
        )
        previous_close = rows[index - 1]["close"]
        move_bps = (bar["close"] / previous_close - Decimal("1")) * BPS
        shield_result = shield.assess(
            spread_bps=spread,
            quote_volume_ratio=volume_ratio,
            trade_count_ratio=trade_ratio,
            price_change_bps=move_bps,
            candle_age_ms=0,
        )
        found.append({
            "timestamp_ms": bar["timestamp_ms"],
            "baseline_signal": base_assessment.signal,
            "candidate_signal": ablated_assessment.signal,
            "candidate_branch": ablated_assessment.strategy,
            "candidate_score": str(ablated_assessment.score),
            "mtf_state": (
                "both" if ablated_assessment.mtf_confluence else
                "only_4h" if (
                    _bullish(aligned.get("higher_1", []))
                    and not _bullish(aligned.get("higher_2", []))
                ) else
                "only_1d" if (
                    _bullish(aligned.get("higher_2", []))
                    and not _bullish(aligned.get("higher_1", []))
                ) else "neither"
            ),
            "shield_blocked": shield_result.block_entries,
            "shield_reasons": list(shield_result.reasons),
            "paper_entry_signal_after_shield": not shield_result.block_entries,
            "other_strategy_gates_passed": True,
        })
    return found


def _bullish(values: list[Decimal]) -> bool:
    from mumin_trader.strategy.smart_sniper import _timeframe_bullish
    return _timeframe_bullish(values)


def _metric_dict(metrics) -> dict:
    return {
        "closed_trades": metrics.closed_trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate_percent": str(metrics.win_rate_percent),
        "net_pnl_usdt": str(metrics.ending_equity - INITIAL_CASH),
        "ending_equity_usdt": str(metrics.ending_equity),
        "net_return_percent": str(metrics.total_return_percent),
        "fees_paid_usdt": str(metrics.fees_paid),
        "max_drawdown_percent": str(metrics.max_drawdown_percent),
        "profit_factor": str(metrics.profit_factor) if metrics.profit_factor is not None else None,
        "shield_blocks": metrics.shield_blocks,
        "buy_candidates_before_shield": metrics.buy_candidates,
        "shield_blocked_buy_candidates": metrics.blocked_buy_candidates,
        "paper_entry_orders": metrics.entry_orders,
    }


def _aggregate(rows: list[dict], variant: str) -> dict:
    selected = [item[variant] for item in rows]
    pnl = sum((Decimal(item["net_pnl_usdt"]) for item in selected), ZERO)
    starting = INITIAL_CASH * Decimal(len(selected))
    return {
        "net_pnl_usdt": str(pnl),
        "aggregate_net_return_percent": str(pnl / starting * ONE_HUNDRED),
        "closed_trades": sum(item["closed_trades"] for item in selected),
        "positive_symbol_series": sum(Decimal(item["net_pnl_usdt"]) > ZERO for item in selected),
        "max_of_series_drawdowns_percent": str(max(
            (Decimal(item["max_drawdown_percent"]) for item in selected), default=ZERO
        )),
        "fees_paid_usdt": str(sum((Decimal(item["fees_paid_usdt"]) for item in selected), ZERO)),
        "paper_entry_orders": sum(item["paper_entry_orders"] for item in selected),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preregistered research-only paired MTF hard-veto ablation."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=HISTORY_BARS)
    args = parser.parse_args()
    if args.limit != HISTORY_BARS:
        raise SystemExit(f"The frozen experiment requires exactly {HISTORY_BARS} candles per symbol.")

    per_symbol: dict[str, dict] = {}
    with httpx.Client(timeout=30.0, headers={"Accept": "application/json"}) as client:
        for symbol in SYMBOLS:
            base = fetch_history(client, symbol, "1h", HISTORY_BARS)
            if len(base) != HISTORY_BARS:
                raise RuntimeError(f"{symbol}: expected {HISTORY_BARS} closed 1h candles, got {len(base)}.")
            higher_names = HIGHER_TIMEFRAMES["1h"]
            higher_raw = {
                timeframe: fetch_history(client, symbol, timeframe, HISTORY_BARS)
                for timeframe in higher_names
            }
            per_symbol[symbol] = {"base": base, "higher": {
                slot: higher_raw[timeframe]
                for slot, timeframe in zip(("higher_1", "higher_2"), higher_names)
            }}

    results: dict[str, list[dict]] = {str(spread): [] for spread in SPREADS}
    negative_controls: dict[str, dict] = {}
    boundaries: dict[str, dict] = {}
    incremental_ledger: dict[str, dict[str, list[dict]]] = {}

    for symbol in SYMBOLS:
        base = per_symbol[symbol]["base"]
        higher = per_symbol[symbol]["higher"]
        development_end = DEVELOPMENT_BARS
        validation_end = DEVELOPMENT_BARS + VALIDATION_BARS
        holdout_start = validation_end
        if development_end + VALIDATION_BARS + HOLDOUT_BARS != len(base):
            raise RuntimeError("Frozen 70/15/15 split does not exactly cover the source history.")
        boundaries[symbol] = {
            "bars_total": len(base),
            "development_indices": [0, development_end],
            "validation_indices": [development_end, validation_end],
            "holdout_indices": [holdout_start, len(base)],
            "development_last_close_utc": datetime.fromtimestamp(
                base[development_end - 1]["timestamp_ms"] / 1000, UTC
            ).isoformat(),
            "validation_first_close_utc": datetime.fromtimestamp(
                base[development_end]["timestamp_ms"] / 1000, UTC
            ).isoformat(),
            "validation_last_close_utc": datetime.fromtimestamp(
                base[validation_end - 1]["timestamp_ms"] / 1000, UTC
            ).isoformat(),
            "holdout_first_close_utc": datetime.fromtimestamp(
                base[holdout_start]["timestamp_ms"] / 1000, UTC
            ).isoformat(),
            "holdout_last_close_utc": datetime.fromtimestamp(
                base[-1]["timestamp_ms"] / 1000, UTC
            ).isoformat(),
        }

        for spread in SPREADS:
            candles = _candles(base, spread)
            higher_candles = {
                slot: _candles(items, spread) for slot, items in higher.items()
            }
            baseline = run_backtest(
                candles, warmup_bars=holdout_start, initial_cash=INITIAL_CASH,
                enable_shield=True, enable_compounding=False,
                enforce_mtf_hard_gate=True, higher_timeframes=higher_candles,
            )
            candidate = run_backtest(
                candles, warmup_bars=holdout_start, initial_cash=INITIAL_CASH,
                enable_shield=True, enable_compounding=False,
                enforce_mtf_hard_gate=False, higher_timeframes=higher_candles,
            )
            record = {
                "symbol": symbol,
                "spread_bps": str(spread),
                "baseline": _metric_dict(baseline),
                "candidate": _metric_dict(candidate),
            }
            results[str(spread)].append(record)
            if spread in (Decimal("5"), Decimal("10")):
                incremental_ledger.setdefault(str(spread), {})[symbol] = _incremental_signals(
                    base, higher, spread, holdout_start,
                )

    acceptance: dict[str, dict] = {}
    for spread in (Decimal("5"), Decimal("10")):
        key = str(spread)
        rows = results[key]
        base_agg = _aggregate(rows, "baseline")
        cand_agg = _aggregate(rows, "candidate")
        checks = {
            "candidate_aggregate_net_return_positive": Decimal(cand_agg["aggregate_net_return_percent"]) > ZERO,
            "candidate_beats_baseline_net_return": (
                Decimal(cand_agg["aggregate_net_return_percent"])
                > Decimal(base_agg["aggregate_net_return_percent"])
            ),
            "candidate_max_drawdown_not_above_baseline": (
                Decimal(cand_agg["max_of_series_drawdowns_percent"])
                <= Decimal(base_agg["max_of_series_drawdowns_percent"])
            ),
            "at_least_30_closed_trades": cand_agg["closed_trades"] >= 30,
            "at_least_5_of_8_symbol_series_positive": cand_agg["positive_symbol_series"] >= 5,
            "20_and_30_bps_negative_controls_zero_entries": True,
        }
        acceptance[key] = {
            "baseline_aggregate": base_agg,
            "candidate_aggregate": cand_agg,
            "checks": checks,
            "pass": all(checks.values()),
        }

    for spread in (Decimal("20"), Decimal("30")):
        key = str(spread)
        candidate_orders = sum(item["candidate"]["paper_entry_orders"] for item in results[key])
        baseline_orders = sum(item["baseline"]["paper_entry_orders"] for item in results[key])
        negative_controls[key] = {
            "baseline_entry_orders": baseline_orders,
            "candidate_entry_orders": candidate_orders,
            "both_zero": baseline_orders == 0 and candidate_orders == 0,
        }
    negative_controls_pass = all(item["both_zero"] for item in negative_controls.values())
    for item in acceptance.values():
        item["checks"]["20_and_30_bps_negative_controls_zero_entries"] = negative_controls_pass
        item["pass"] = all(item["checks"].values())

    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": "BINANCE_PUBLIC_HISTORICAL_CLOSED_CANDLES",
        "experiment": "MTF_HARD_GATE_ABLATION",
        "research_only": True,
        "paper_only": True,
        "live_orders_enabled": False,
        "production_strategy_modified": False,
        "production_risk_limits_modified": False,
        "production_execution_allowlist_modified": False,
        "capital_per_symbol_variant_usdt": str(INITIAL_CASH),
        "compounding_enabled": False,
        "fixed_stop_loss_percent": "0.50",
        "take_profit_rule": "unchanged strategy-selected 1.25%-1.50%",
        "production_atr_band_percent": ["0.08", "1.20"],
        "fees_each_side_percent": "0.10",
        "slippage_each_side_bps": "2",
        "shield_enabled": True,
        "universe": list(SYMBOLS),
        "base_interval": "1h",
        "higher_timeframes": ["4h", "1d"],
        "spread_scenarios_bps": [str(item) for item in SPREADS],
        "split": {
            "development_bars": DEVELOPMENT_BARS,
            "validation_bars": VALIDATION_BARS,
            "holdout_bars": HOLDOUT_BARS,
            "ratio": "70/15/15",
            "holdout_is_acceptance_segment": True,
            "per_symbol_boundaries": boundaries,
        },
        "method": (
            "Paired baseline/candidate replay on identical closed candles. Candidate disables "
            "only SmartSniperStrategy's final MTF hard veto; MTF indicators and score bonuses "
            "remain active. Paper walker, Shield, costs, sizing, stop, target and all other "
            "eligibility gates are identical. No compounding. Each symbol/variant starts with "
            "an independent 70 USDT account. Holdout is the final 1,491 bars; no tuning."
        ),
        "results_by_spread": results,
        "incremental_candidate_signal_ledger": incremental_ledger,
        "negative_controls": negative_controls,
        "acceptance": acceptance,
        "overall_pass": all(item["pass"] for item in acceptance.values()),
        "limitations": [
            "Historical spread values are fixed scenarios, not archived order-book observations.",
            "The Paper engine evaluates exits at candle closes; intrabar stop/target path is unavailable.",
            "The final open position is liquidated at the final holdout close.",
            "An incremental eligible signal is not necessarily a filled Paper entry if a position is already open.",
            "A positive historical result is not evidence of future profitability and does not authorize live trading.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
