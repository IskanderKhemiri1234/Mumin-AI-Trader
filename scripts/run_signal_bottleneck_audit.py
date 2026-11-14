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
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy
from run_binance_walk_forward import ALLOWED, HIGHER_TIMEFRAMES, fetch_history

ZERO = Decimal("0")
BPS = Decimal("10000")


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _atr_percent(rows: list[dict], index: int) -> Decimal:
    window = rows[max(0, index - 14):index + 1]
    ranges = []
    for offset in range(1, len(window)):
        current, previous = window[offset], window[offset - 1]
        ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"]),
        ))
    if not ranges:
        return ZERO
    return sum(ranges[-14:], ZERO) / Decimal(len(ranges[-14:])) / rows[index]["close"] * Decimal("100")


def audit_series(
    rows: list[dict],
    higher_rows: dict[str, list[dict]],
    spread_bps: Decimal,
) -> dict:
    strategy = SmartSniperStrategy()
    shield = AntiLossQuantumShield()
    indexed = {
        key: ([item["timestamp_ms"] for item in values], values)
        for key, values in higher_rows.items()
    }
    strategy_rejections: Counter[str] = Counter()
    shield_rejections: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    buy_candidates = 0
    final_entries = 0
    evaluated = 0

    for index in range(60, len(rows)):
        bar = rows[index]
        closes = [item["close"] for item in rows[max(0, index - 60):index + 1]]
        aligned = {}
        for slot, (timestamps, history) in indexed.items():
            count = bisect_right(timestamps, bar["timestamp_ms"])
            aligned[slot] = [
                item["close"] for item in history[max(0, count - 45):count]
            ]
        assessment = strategy.assess(
            closes,
            spread_bps=spread_bps,
            higher_timeframe_closes=aligned,
            atr_percent=_atr_percent(rows, index),
        )
        evaluated += 1
        if assessment.signal == "BUY":
            buy_candidates += 1
        else:
            for reason in assessment.reasons:
                strategy_rejections[reason] += 1

        prior = rows[max(0, index - 20):index]
        volume_median = _median([item["quote_volume"] for item in prior])
        trades_median = _median([item["trade_count"] for item in prior])
        volume_ratio = (
            bar["quote_volume"] / volume_median
            if len(prior) == 20 and volume_median and volume_median > ZERO else None
        )
        trades_ratio = (
            bar["trade_count"] / trades_median
            if len(prior) == 20 and trades_median and trades_median > ZERO else None
        )
        previous = rows[index - 1]["close"]
        move_bps = (bar["close"] / previous - Decimal("1")) * BPS
        shield_result = shield.assess(
            spread_bps=spread_bps,
            quote_volume_ratio=volume_ratio,
            trade_count_ratio=trades_ratio,
            price_change_bps=move_bps,
            candle_age_ms=0,
        )
        modes[shield_result.mode] += 1
        if shield_result.block_entries:
            for reason in shield_result.reasons:
                shield_rejections[reason] += 1
        if assessment.signal == "BUY" and not shield_result.block_entries:
            final_entries += 1

    return {
        "spread_bps": str(spread_bps),
        "evaluated_closed_candles": evaluated,
        "strategy_buy_candidates_before_shield": buy_candidates,
        "final_entries_after_shield": final_entries,
        "strategy_rejection_reasons": dict(strategy_rejections.most_common()),
        "shield_modes": dict(modes),
        "shield_rejection_reasons": dict(shield_rejections.most_common()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Attribute Smart Sniper signal and liquidity-filter rejections.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,LINKUSDT")
    parser.add_argument("--interval", required=True, choices=tuple(HIGHER_TIMEFRAMES))
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--spread-scenarios", default="5,10,20,30")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    if not symbols or any(symbol not in ALLOWED for symbol in symbols):
        raise SystemExit("Research symbols must come from the research allowlist.")
    if not 400 <= args.limit <= 10000:
        raise SystemExit("--limit must be between 400 and 10000.")
    spreads = [Decimal(value.strip()) for value in args.spread_scenarios.split(",")]
    if any(not value.is_finite() or value < ZERO for value in spreads):
        raise SystemExit("Spread assumptions must be finite non-negative basis points.")

    output = []
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json"}) as client:
        for symbol in symbols:
            base = fetch_history(client, symbol, args.interval, args.limit)
            higher_names = HIGHER_TIMEFRAMES[args.interval]
            higher = {
                slot: fetch_history(client, symbol, timeframe, args.limit)
                for slot, timeframe in zip(("higher_1", "higher_2"), higher_names)
            }
            for spread in spreads:
                output.append({
                    "symbol": symbol,
                    "base_interval": args.interval,
                    "higher_timeframes": list(higher_names),
                    **audit_series(base, higher, spread),
                })

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "BINANCE_PUBLIC_MARKET_DATA",
        "method": "closed-candle, timestamp-aligned, per-condition rejection attribution; no parameter fitting",
        "results": output,
        "limitations": [
            "Spread is a fixed scenario, not archived historical order-book data.",
            "This audit attributes current-rule rejections; it does not prove a relaxed rule is profitable.",
            "No production thresholds or Paper execution risk limits are changed.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
