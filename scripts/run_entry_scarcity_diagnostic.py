from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy, _ema, _timeframe_bullish
from run_binance_walk_forward import ALLOWED, HIGHER_TIMEFRAMES, fetch_history

ZERO = Decimal(0)
ONE_HUNDRED = Decimal(100)
BPS = Decimal(10000)
ATR_MIN = Decimal("0.08")
ATR_MAX = Decimal("1.20")
SPREAD_MAX = Decimal(12)
MINIMUM_SCORE = Decimal(75)
MINIMUM_NET_EDGE_BPS = Decimal(10)


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


def _aligned_closes(
    rows: list[dict],
    higher_rows: dict[str, list[dict]],
    index: int,
) -> dict[str, list[Decimal]]:
    timestamp = rows[index]["timestamp_ms"]
    aligned: dict[str, list[Decimal]] = {}
    for slot, history in higher_rows.items():
        timestamps = [item["timestamp_ms"] for item in history]
        count = bisect_right(timestamps, timestamp)
        aligned[slot] = [
            item["close"] for item in history[max(0, count - 45):count]
        ]
    return aligned


def _local_setup_failures(
    assessment,
    closes: list[Decimal],
) -> list[str]:
    failures: list[str] = []
    fast = _ema(closes[-40:], 9)
    prior_fast = _ema(closes[-41:-1], 9) if len(closes) >= 41 else _ema(closes[-40:-1], 9)
    if assessment.strategy == "MOMENTUM":
        if assessment.regime != "BULL":
            failures.append("momentum_local_trend_not_bullish")
        if assessment.momentum_3_bps <= Decimal(5):
            failures.append("momentum_3_bps_not_above_5")
        if not Decimal(48) <= assessment.rsi <= Decimal(68):
            failures.append("momentum_rsi_outside_48_68")
    elif assessment.strategy == "RECOVERY":
        if not (
            assessment.drawdown_12_bps <= Decimal(-150)
            and assessment.rebound_bps >= Decimal(60)
        ):
            failures.append("recovery_pullback_rebound_setup_missing")
        if not (fast > prior_fast and closes[-1] > fast):
            failures.append("recovery_fast_ema_reclaim_missing")
    else:
        failures.append("strategy_not_selected")
    return failures


def diagnose_series(
    rows: list[dict],
    higher_rows: dict[str, list[dict]],
    spread_bps: Decimal,
    symbol: str,
) -> tuple[dict, list[dict]]:
    strategy = SmartSniperStrategy()
    shield = AntiLossQuantumShield()
    indexed = {
        key: ([item["timestamp_ms"] for item in values], values)
        for key, values in higher_rows.items()
    }
    signal_counts: Counter[str] = Counter()
    gate_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    shield_market_counts: Counter[str] = Counter()
    shield_candidate_counts: Counter[str] = Counter()
    mtf_component_counts: Counter[str] = Counter()
    records: list[dict] = []
    buy_candidates = final_entries = 0

    for index in range(60, len(rows)):
        bar = rows[index]
        closes = [item["close"] for item in rows[max(0, index - 60):index + 1]]
        aligned: dict[str, list[Decimal]] = {}
        for slot, (timestamps, history) in indexed.items():
            count = bisect_right(timestamps, bar["timestamp_ms"])
            aligned[slot] = [
                item["close"] for item in history[max(0, count - 45):count]
            ]
        atr = _atr_percent(rows, index)
        higher_4h_bullish = _timeframe_bullish(aligned.get("higher_1", []))
        higher_1d_bullish = _timeframe_bullish(aligned.get("higher_2", []))
        mtf_component_counts["4h_bullish"] += int(higher_4h_bullish)
        mtf_component_counts["1d_bullish"] += int(higher_1d_bullish)
        mtf_component_counts["both_bullish"] += int(higher_4h_bullish and higher_1d_bullish)
        mtf_component_counts["neither_bullish"] += int(not higher_4h_bullish and not higher_1d_bullish)
        assessment = strategy.assess(
            closes,
            spread_bps=spread_bps,
            higher_timeframe_closes=aligned,
            atr_percent=atr,
        )
        signal_counts[assessment.signal] += 1
        expected_move = (
            assessment.momentum_12_bps
            if assessment.strategy == "MOMENTUM"
            else min(assessment.rebound_bps, assessment.momentum_12_bps)
        )
        required_move = (
            assessment.target_percent * ONE_HUNDRED
            + assessment.estimated_cost_bps
            + MINIMUM_NET_EDGE_BPS
        )
        failures = _local_setup_failures(assessment, closes)
        if not assessment.mtf_confluence:
            failures.append("higher_timeframe_confluence_missing")
        if not ATR_MIN <= assessment.atr_percent <= ATR_MAX:
            failures.append("atr_outside_0.08_1.20_percent")
        if spread_bps > SPREAD_MAX:
            failures.append("spread_above_12_bps")
        if expected_move < required_move:
            failures.append("expected_move_below_target_costs_and_buffer")
        if assessment.score < MINIMUM_SCORE:
            failures.append("quality_score_below_75")

        # Count every failed condition (overlapping), then assign one exclusive
        # primary cause using a fixed diagnostic priority. This is attribution,
        # not a counterfactual bypass or a proposed production-rule change.
        for failure in failures:
            gate_counts[failure] += 1
        priority = (
            "higher_timeframe_confluence_missing",
            "momentum_3_bps_not_above_5",
            "momentum_rsi_outside_48_68",
            "momentum_local_trend_not_bullish",
            "expected_move_below_target_costs_and_buffer",
            "recovery_pullback_rebound_setup_missing",
            "recovery_fast_ema_reclaim_missing",
            "atr_outside_0.08_1.20_percent",
            "spread_above_12_bps",
            "quality_score_below_75",
            "strategy_not_selected",
        )
        primary = next((item for item in priority if item in failures), None)
        if assessment.signal == "HOLD":
            primary_counts[primary or "unclassified_hold"] += 1

        prior = rows[max(0, index - 20):index]
        volume_values = sorted(item["quote_volume"] for item in prior)
        trade_values = sorted(item["trade_count"] for item in prior)

        def median(values: list[Decimal]) -> Decimal | None:
            if len(values) != 20:
                return None
            middle = len(values) // 2
            return (
                values[middle] if len(values) % 2
                else (values[middle - 1] + values[middle]) / Decimal(2)
            )

        volume_median = median(volume_values)
        trades_median = median(trade_values)
        volume_ratio = (
            bar["quote_volume"] / volume_median
            if volume_median is not None and volume_median > ZERO else None
        )
        trade_ratio = (
            bar["trade_count"] / trades_median
            if trades_median is not None and trades_median > ZERO else None
        )
        previous_close = rows[index - 1]["close"]
        move_bps = (bar["close"] / previous_close - Decimal(1)) * BPS
        shield_result = shield.assess(
            spread_bps=spread_bps,
            quote_volume_ratio=volume_ratio,
            trade_count_ratio=trade_ratio,
            price_change_bps=move_bps,
            candle_age_ms=0,
        )
        if shield_result.block_entries:
            for reason in shield_result.reasons:
                shield_market_counts[reason] += 1

        is_buy = assessment.signal == "BUY"
        if is_buy:
            buy_candidates += 1
            if shield_result.block_entries:
                for reason in shield_result.reasons:
                    shield_candidate_counts[reason] += 1
            else:
                final_entries += 1

        records.append({
            "symbol": symbol,
            "interval": "1h",
            "timestamp_ms": bar["timestamp_ms"],
            "spread_bps": str(spread_bps),
            "signal": assessment.signal,
            "strategy_branch": assessment.strategy,
            "regime": assessment.regime,
            "score": str(assessment.score),
            "momentum_3_bps": str(assessment.momentum_3_bps),
            "momentum_12_bps": str(assessment.momentum_12_bps),
            "drawdown_12_bps": str(assessment.drawdown_12_bps),
            "rebound_bps": str(assessment.rebound_bps),
            "rsi": str(assessment.rsi),
            "atr_percent": str(assessment.atr_percent),
            "mtf_confluence": assessment.mtf_confluence,
            "higher_4h_bullish": higher_4h_bullish,
            "higher_1d_bullish": higher_1d_bullish,
            "expected_move_bps": str(expected_move),
            "required_move_bps": str(required_move),
            "failed_gates_overlapping": failures,
            "primary_rejection_exclusive": primary if assessment.signal == "HOLD" else None,
            "shield_mode": shield_result.mode,
            "shield_blocks_entry": shield_result.block_entries,
            "shield_reasons": list(shield_result.reasons),
            "final_entry_after_shield": is_buy and not shield_result.block_entries,
        })

    holds = signal_counts["HOLD"]
    summary = {
        "symbol": symbol,
        "interval": "1h",
        "spread_bps": str(spread_bps),
        "evaluated_closed_candles": len(records),
        "signal_counts": dict(signal_counts),
        "hold_candles": holds,
        "mtf_component_candle_counts": dict(mtf_component_counts),
        "strategy_gate_failures_overlapping": dict(gate_counts.most_common()),
        "strategy_primary_rejection_exclusive": dict(primary_counts.most_common()),
        "strategy_primary_rejection_percent_of_holds": {
            key: str((Decimal(count) / Decimal(holds) * ONE_HUNDRED).quantize(Decimal("0.01")))
            for key, count in primary_counts.items()
        } if holds else {},
        "buy_candidates_before_shield": buy_candidates,
        "shield_market_condition_counts_all_candles": dict(shield_market_counts.most_common()),
        "shield_candidate_rejection_counts_buy_candidates_only": dict(shield_candidate_counts.most_common()),
        "final_entries_after_shield": final_entries,
    }
    return summary, records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only attribution of Smart Sniper MTF and momentum entry scarcity."
    )
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,LINKUSDT",
    )
    parser.add_argument("--interval", choices=("1h",), default="1h")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--spread-scenarios", default="5,10,20,30")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols or any(symbol not in ALLOWED for symbol in symbols):
        raise SystemExit("Research symbols must come from the fixed research allowlist.")
    if not 400 <= args.limit <= 10000:
        raise SystemExit("--limit must be between 400 and 10000.")
    spreads = [Decimal(item.strip()) for item in args.spread_scenarios.split(",")]
    if not spreads or any(not item.is_finite() or item < ZERO for item in spreads):
        raise SystemExit("Spread scenarios must be finite non-negative basis-point values.")

    summaries: list[dict] = []
    rejection_records: list[dict] = []
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json"}) as client:
        for symbol in symbols:
            base = fetch_history(client, symbol, "1h", args.limit)
            higher_names = HIGHER_TIMEFRAMES["1h"]
            higher = {
                slot: fetch_history(client, symbol, timeframe, args.limit)
                for slot, timeframe in zip(("higher_1", "higher_2"), higher_names)
            }
            for spread in spreads:
                summary, records = diagnose_series(base, higher, spread, symbol)
                summaries.append(summary)
                rejection_records.extend(records)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "BINANCE_PUBLIC_HISTORICAL_CLOSED_CANDLES",
        "research_only": True,
        "production_strategy_modified": False,
        "paper_only": True,
        "capital_reference_usdt": "70",
        "live_orders_enabled": False,
        "execution_allowlist_modified": False,
        "risk_limits_modified": False,
        "production_atr_band": {"lower_percent": "0.08", "upper_percent": "1.20"},
        "fixed_stop_loss_percent": "0.50",
        "take_profit_rule": "unchanged strategy-selected 1.25%-1.50%",
        "shield_enabled_for_attribution": True,
        "compounding_enabled": False,
        "method": (
            "Per-closed-candle diagnostic attribution on identical 1h bars. "
            "MTF, local momentum/setup, expected-move, ATR, spread and score failures "
            "are counted independently and may overlap; one exclusive primary reason "
            "is also assigned using a fixed priority. Shield market states and BUY-only "
            "Shield rejections are reported separately. No gate is bypassed or tuned."
        ),
        "higher_timeframes": ["4h", "1d"],
        "spread_scenarios_bps": [str(item) for item in spreads],
        "summary_by_symbol_and_spread": summaries,
        "per_candle_rejection_ledger": rejection_records,
        "limitations": [
            "This is observational gate attribution, not a causal counterfactual; removing a gate can change score and strategy-branch selection.",
            "The MTF condition is the strategy's conjunction of bullish 4h and 1d confirmations.",
            "Momentum is separated into local setup conditions and the expected-move-versus-target/cost/buffer gate.",
            "Overlapping counts must not be summed as unique rejected candles; use the exclusive primary-rejection field for a partition.",
            "Spread scenarios are assumptions, not archived historical order-book depth.",
            "No strategy threshold, Paper execution setting, production allowlist, capital or risk limit is changed.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "summary_rows": len(summaries),
        "per_candle_records": len(rejection_records),
        "generated_at_utc": payload["generated_at_utc"],
    }, indent=2))


if __name__ == "__main__":
    main()
