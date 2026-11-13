from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from mumin_trader.research.walk_forward import HistoricalCandle, walk_forward

BASE_URL = "https://data-api.binance.vision"
ALLOWED = {"BTCUSDT", "ETHUSDT", "BNBUSDT"}


def fetch_history(client: httpx.Client, symbol: str, interval: str, limit: int):
    server = client.get(f"{BASE_URL}/api/v3/time")
    server.raise_for_status()
    server_time = int(server.json()["serverTime"])
    response = client.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
    )
    response.raise_for_status()
    rows = response.json()
    candles = []
    for row in rows:
        if len(row) < 9 or int(row[6]) >= server_time:
            continue
        candles.append({
            "timestamp_ms": int(row[6]),
            "open": Decimal(str(row[1])),
            "high": Decimal(str(row[2])),
            "low": Decimal(str(row[3])),
            "close": Decimal(str(row[4])),
            "quote_volume": Decimal(str(row[7])),
            "trade_count": Decimal(str(row[8])),
        })
    if len(candles) < 400:
        raise RuntimeError(f"Only {len(candles)} closed candles returned for {symbol}.")
    return candles


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch public Binance candles and run Smart Sniper walk-forward stress tests."
    )
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--warmup-bars", type=int, default=240)
    parser.add_argument("--test-bars", type=int, default=120)
    parser.add_argument("--step-bars", type=int, default=120)
    parser.add_argument("--spread-scenarios", default="5,10,20")
    parser.add_argument("--output", type=Path, default=Path("smart-sniper-walk-forward.json"))
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols or any(symbol not in ALLOWED for symbol in symbols):
        raise SystemExit(f"Symbols must be selected from: {', '.join(sorted(ALLOWED))}")
    if not 400 <= args.limit <= 1000:
        raise SystemExit("--limit must be between 400 and 1000.")
    spreads = [Decimal(item.strip()) for item in args.spread_scenarios.split(",")]
    if not spreads or any(value < 0 for value in spreads):
        raise SystemExit("Spread scenarios must be non-negative basis-point values.")

    results = []
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json"}) as client:
        for symbol in symbols:
            raw = fetch_history(client, symbol, args.interval, args.limit)
            for spread in spreads:
                candles = [
                    HistoricalCandle(**row, spread_bps=spread)
                    for row in raw
                ]
                for shield_enabled in (True, False):
                    for compounding_enabled in (True, False):
                        report = walk_forward(
                            candles,
                            warmup_bars=args.warmup_bars,
                            test_bars=args.test_bars,
                            step_bars=args.step_bars,
                            enable_shield=shield_enabled,
                            enable_compounding=compounding_enabled,
                        )
                        results.append({
                            "symbol": symbol,
                            "interval": args.interval,
                            "spread_assumption_bps": str(spread),
                            "shield_enabled": shield_enabled,
                            "compounding_enabled": compounding_enabled,
                            "closed_candles": len(candles),
                            "folds": [
                        {
                            "start_index": fold.start_index,
                            "end_index": fold.end_index,
                            "ending_equity": str(fold.ending_equity),
                            "return_percent": str(fold.total_return_percent),
                            "max_drawdown_percent": str(fold.max_drawdown_percent),
                            "closed_trades": fold.closed_trades,
                            "wins": fold.wins,
                            "losses": fold.losses,
                            "win_rate_percent": str(fold.win_rate_percent),
                            "profit_factor": (
                                str(fold.profit_factor) if fold.profit_factor is not None else None
                            ),
                            "fees_paid": str(fold.fees_paid),
                            "shield_blocks": fold.shield_blocks,
                            "buy_candidates": fold.buy_candidates,
                            "blocked_buy_candidates": fold.blocked_buy_candidates,
                            "entry_orders": fold.entry_orders,
                            "compounding_reinvested": str(fold.compounding_reinvested),
                        }
                            for fold in report.folds
                            ],
                        })

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "BINANCE_PUBLIC_MARKET_DATA",
        "method": "rolling out-of-sample evaluation of a fixed rule-based strategy",
        "spread_scenarios_bps": [str(value) for value in spreads],
        "results": results,
        "limitations": [
            "Historical candles do not contain historical order-book depth; spread scenarios are explicit assumptions.",
            "The strategy is heuristic and is not a calibrated probability model.",
            "Walk-forward folds are out-of-sample windows; no ML model is trained.",
            "Stops and targets are evaluated at candle closes, not inferred intrabar paths.",
            "Historical performance does not guarantee future results.",
            "Research uses a 1.02 USDT seed ticket to clear minimum notional after fees; only incremental ticket size comes from realized net profits.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
