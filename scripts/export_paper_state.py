from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from export_paper_report import load_snapshot
from mumin_trader.strategy_swarm import compound_allocation, compoundable_equity
import sqlite3


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def build_ui_contract(database: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    symbols = (
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
        "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    )
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        decision_rows = {
            row["symbol"]: dict(row)
            for row in connection.execute(
                """SELECT d.* FROM bot_agent_decisions d
                   JOIN (SELECT symbol, MAX(tick) AS latest_tick
                         FROM bot_agent_decisions GROUP BY symbol) latest
                   ON latest.symbol=d.symbol AND latest.latest_tick=d.tick"""
            )
        }
    finally:
        connection.close()

    equity = Decimal(snapshot["equity"])
    reserve_cash = Decimal("70.00") * Decimal("0.20")
    compoundable = compoundable_equity(
        equity, Decimal(snapshot["realized_pnl"]), initial_capital=Decimal("70.00")
    )
    next_entry_budget = compound_allocation(
        compoundable, Decimal(snapshot["cash"]), reserve_cash,
        initial_capital=Decimal("70.00"), max_trade=Decimal("5.00")
    )
    positions = {item["symbol"]: item for item in snapshot["positions"]}
    assets = []
    for symbol in symbols:
        position = positions.get(symbol)
        decision = decision_rows.get(symbol)
        market_price = snapshot["market_prices"].get(symbol)
        market_value = position["market_value"] if position else Decimal("0")
        allocation_pct = (
            Decimal(market_value) / equity * Decimal("100")
            if equity > 0 else Decimal("0")
        )
        assets.append({
            "symbol": symbol,
            "market_price": market_price,
            "position_market_value_usdt": market_value,
            "compounder_actual_allocation_pct": allocation_pct,
            "agent_decision": ({
                "liquidity_sweep": {
                    "vote": decision["liquidity_sweep_vote"],
                    "reason": decision["liquidity_sweep_reason"],
                },
                "depth_imbalance": {
                    "vote": decision["depth_imbalance_vote"],
                    "reason": decision["depth_imbalance_reason"],
                },
                "micro_alpha_filter": {
                    "vote": decision["micro_alpha_filter_vote"],
                    "reason": decision["micro_alpha_filter_reason"],
                },
                "final_signal": decision["final_signal"],
                "action": decision["action"],
                "spread_bps": Decimal(decision["spread_bps"]),
                "spread_under_8_bps": bool(decision["spread_pass"]),
                "agent_consensus": decision["agent_consensus"],
                "agent_consensus_pass": bool(decision["agent_consensus_pass"]),
                "atr_pct": Decimal(decision["atr_pct"]),
                "macro_sma20": Decimal(decision["macro_sma20"]),
                "macro_sma20_slope": Decimal(decision["macro_sma20_slope"]),
                "macro_trend_reason": decision["macro_trend_reason"],
            } if decision else None),
        })
    return {
        "assets": assets,
        "compounder": {
            "compoundable_equity_usdt": compoundable,
            "next_entry_budget_usdt": next_entry_budget,
            "reserve_cash_usdt": reserve_cash,
            "allocation_semantics": "actual_position_market_value_divided_by_current_equity",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a read-only Paper state JSON snapshot.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_snapshot(args.db)
    ui_contract = build_ui_contract(args.db, snapshot)
    snapshot.update(ui_contract)
    payload = {
        "schema_version": 2,
        "mode": "PAPER_ONLY",
        "initial_capital_usdt": "70.00",
        "risk": {
            "stop_loss_percent": "0.50",
            "take_profit_percent": "1.25",
            "max_trade_allocation_usdt": "5.00",
            "max_spread_bps": "8.00",
            "max_daily_loss_percent": "2.00",
            "max_drawdown_percent": "5.00",
        },
        "snapshot": json_value(snapshot),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Exported read-only Paper state: tick={snapshot['tick']}, positions={len(snapshot['positions'])}")


if __name__ == "__main__":
    main()
