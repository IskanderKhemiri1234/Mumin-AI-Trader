from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict, deque
from decimal import Decimal
from pathlib import Path
from typing import Any

from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger

ZERO = Decimal("0")


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _decision_map(connection: sqlite3.Connection) -> dict[tuple[int, str], dict[str, Any]]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_agent_decisions'"
    ).fetchone()
    if not exists:
        return {}
    rows = connection.execute(
        """SELECT tick,symbol,liquidity_sweep_vote,liquidity_sweep_reason,
                  depth_imbalance_vote,depth_imbalance_reason,
                  micro_alpha_filter_vote,micro_alpha_filter_reason,
                  final_signal,action,macro_trend_reason,spread_bps,atr_pct
           FROM bot_agent_decisions ORDER BY tick"""
    ).fetchall()
    return {(int(row["tick"]), str(row["symbol"]).upper()): dict(row) for row in rows}


def build_audit(database: str | Path) -> dict[str, Any]:
    path = Path(database).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"bot_account", "bot_positions", "bot_fills", "bot_market_ticks"}
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"Paper ledger schema is incomplete: {', '.join(missing)}")

        account = connection.execute(
            "SELECT initial_cash,cash,realized_pnl,fees_paid,tick_count FROM bot_account WHERE id=1"
        ).fetchone()
        if account is None:
            raise RuntimeError("Paper account row is missing.")
        decisions = _decision_map(connection)
        fills = connection.execute(
            """SELECT fill_id,tick,symbol,side,quantity,reference_price,fill_price,
                      gross_quote,fee_quote,net_pnl,data_source,exit_reason,created_at
               FROM bot_fills ORDER BY fill_id"""
        ).fetchall()

        lots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        per_symbol: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "buy_fills": 0, "sell_fills": 0, "closed_trades": 0,
            "wins": 0, "losses": 0, "gross_pnl_usdt": ZERO,
            "entry_fees_usdt": ZERO, "exit_fees_usdt": ZERO,
            "net_pnl_usdt": ZERO, "buy_notional_usdt": ZERO,
            "sell_notional_usdt": ZERO, "matched_quantity": ZERO,
            "entry_reasons": defaultdict(int), "exit_signals": defaultdict(int),
        })
        round_trips: list[dict[str, Any]] = []
        fees_by_symbol: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: {"BUY": ZERO, "SELL": ZERO}
        )
        issues: list[str] = []
        total_fees = ZERO
        total_gross = ZERO
        total_net = ZERO

        for fill in fills:
            symbol = str(fill["symbol"]).upper()
            side = str(fill["side"]).upper()
            quantity = dec(fill["quantity"])
            price = dec(fill["fill_price"])
            gross = dec(fill["gross_quote"])
            fee = dec(fill["fee_quote"])
            stats = per_symbol[symbol]
            total_fees += fee
            fees_by_symbol[symbol][side] += fee
            if side == "BUY":
                stats["buy_fills"] += 1
                stats["buy_notional_usdt"] += gross
                decision = decisions.get((int(fill["tick"]), symbol), {})
                entry_reason = str(decision.get("micro_alpha_filter_reason") or "DECISION_NOT_RECORDED")
                stats["entry_reasons"][entry_reason] += 1
                lots[symbol].append({
                    "quantity": quantity, "gross": gross, "fee": fee,
                    "fill_id": int(fill["fill_id"]), "tick": int(fill["tick"]),
                    "price": price, "time": str(fill["created_at"]),
                    "reason": entry_reason, "decision": decision,
                })
                continue
            if side != "SELL":
                issues.append(f"Unsupported side on fill {fill['fill_id']}: {side}")
                continue
            stats["sell_fills"] += 1
            stats["sell_notional_usdt"] += gross
            remaining = quantity
            matched = ZERO
            sell_gross_pnl = ZERO
            allocated_entry_fee = ZERO
            allocated_exit_fee = ZERO
            entry_records = []
            while remaining > ZERO and lots[symbol]:
                lot = lots[symbol][0]
                take = min(remaining, lot["quantity"])
                fraction = take / quantity
                exit_proceeds = gross * fraction
                exit_fee_part = fee * fraction
                entry_cost = lot["gross"] * take / lot["quantity"]
                entry_fee_part = lot["fee"] * take / lot["quantity"]
                sell_gross_pnl += exit_proceeds - entry_cost
                allocated_entry_fee += entry_fee_part
                allocated_exit_fee += exit_fee_part
                matched += take
                entry_records.append({
                    "buy_fill_id": lot["fill_id"], "buy_tick": lot["tick"],
                    "buy_price": str(lot["price"]), "buy_time": lot["time"],
                    "entry_reason": lot["reason"], "matched_quantity": str(take),
                })
                lot["quantity"] -= take
                lot["gross"] -= entry_cost
                lot["fee"] -= entry_fee_part
                remaining -= take
                if lot["quantity"] == ZERO:
                    lots[symbol].popleft()
            if remaining > ZERO:
                issues.append(
                    f"SELL fill {fill['fill_id']} has {remaining} {symbol} without matched BUY inventory."
                )
            net = sell_gross_pnl - allocated_entry_fee - allocated_exit_fee
            decision = decisions.get((int(fill["tick"]), symbol), {})
            exit_signal = str(decision.get("final_signal") or "DECISION_NOT_RECORDED")
            stats["exit_signals"][exit_signal] += 1
            stats["closed_trades"] += 1
            stats["gross_pnl_usdt"] += sell_gross_pnl
            stats["entry_fees_usdt"] += allocated_entry_fee
            stats["exit_fees_usdt"] += allocated_exit_fee
            stats["net_pnl_usdt"] += net
            stats["matched_quantity"] += matched
            if net > ZERO:
                stats["wins"] += 1
            elif net < ZERO:
                stats["losses"] += 1
            total_gross += sell_gross_pnl
            total_net += net
            round_trips.append({
                "sell_fill_id": int(fill["fill_id"]), "sell_tick": int(fill["tick"]),
                "symbol": symbol, "sell_time": str(fill["created_at"]),
                "exit_fill_price": str(price), "exit_notional_usdt": str(gross),
                "matched_quantity": str(matched), "unmatched_quantity": str(remaining),
                "entry_lots": entry_records, "gross_pnl_usdt": str(sell_gross_pnl),
                "entry_fees_usdt": str(allocated_entry_fee),
                "exit_fees_usdt": str(allocated_exit_fee), "net_pnl_usdt": str(net),
                "recorded_net_pnl_usdt": str(dec(fill["net_pnl"])),
                "exit_signal_context": exit_signal,
                "exit_trigger_reason": str(fill["exit_reason"]) if fill["exit_reason"] else "UNKNOWN_LEGACY_RECORD",
            })

        summary = {}
        for symbol, values in sorted(per_symbol.items()):
            net_wins = sum(
                (dec(item["net_pnl_usdt"]) for item in round_trips
                 if item["symbol"] == symbol and dec(item["net_pnl_usdt"]) > ZERO), ZERO
            )
            net_losses = -sum(
                (dec(item["net_pnl_usdt"]) for item in round_trips
                 if item["symbol"] == symbol and dec(item["net_pnl_usdt"]) < ZERO), ZERO
            )
            closed = int(values["closed_trades"])
            summary[symbol] = {
                "buy_fills": values["buy_fills"], "sell_fills": values["sell_fills"],
                "closed_trades": closed, "wins": values["wins"], "losses": values["losses"],
                "win_rate_percent": str(
                    Decimal(values["wins"]) / Decimal(closed) * Decimal("100")
                    if closed else ZERO
                ),
                "gross_pnl_usdt": str(values["gross_pnl_usdt"]),
                "closed_round_trip_entry_fees_usdt": str(values["entry_fees_usdt"]),
                "closed_round_trip_exit_fees_usdt": str(values["exit_fees_usdt"]),
                "fill_buy_fees_usdt": str(fees_by_symbol[symbol]["BUY"]),
                "fill_sell_fees_usdt": str(fees_by_symbol[symbol]["SELL"]),
                "fill_total_fees_usdt": str(
                    fees_by_symbol[symbol]["BUY"] + fees_by_symbol[symbol]["SELL"]
                ),
                "net_pnl_usdt": str(values["net_pnl_usdt"]),
                "profit_factor_net": str(net_wins / net_losses) if net_losses > ZERO else None,
                "buy_notional_usdt": str(values["buy_notional_usdt"]),
                "sell_notional_usdt": str(values["sell_notional_usdt"]),
                "entry_reason_counts": dict(values["entry_reasons"]),
                "exit_signal_context_counts": dict(values["exit_signals"]),
                "exit_trigger_reason_note": "Exact exit trigger is read from exit_reason; NULL on legacy fills is reported as unknown.",
            }

        reconciliation = reconcile_paper_ledger(connection)
        recorded_fees = dec(account["fees_paid"])
        recorded_realized = dec(account["realized_pnl"])
        if abs(total_fees - recorded_fees) > Decimal("0.00000002"):
            issues.append("Aggregate fill fees do not match account fees_paid.")
        if abs(total_net - recorded_realized) > Decimal("0.00000002"):
            issues.append("Aggregate FIFO round-trip PnL does not match account realized_pnl.")

        return {
            "schema_version": 1, "mode": "PAPER_ONLY",
            "source": "persisted Binance public-market-data Paper ledger",
            "account": {
                "initial_cash_usdt": str(dec(account["initial_cash"])),
                "cash_usdt": str(dec(account["cash"])),
                "recorded_realized_pnl_usdt": str(recorded_realized),
                "reconstructed_realized_pnl_usdt": str(total_net),
                "recorded_fees_usdt": str(recorded_fees),
                "reconstructed_fees_usdt": str(total_fees),
                "closed_round_trips": len(round_trips),
                "unmatched_open_lots": {
                    symbol: [{"remaining_quantity": str(lot["quantity"]),
                              "entry_tick": lot["tick"], "entry_reason": lot["reason"]}
                             for lot in symbol_lots]
                    for symbol, symbol_lots in lots.items() if symbol_lots
                },
            },
            "per_symbol": summary, "round_trips": round_trips,
            "reconciliation": reconciliation, "issues": issues,
            "accounting_status": (
                "PASS" if not issues and reconciliation.get("status") == "PASS" else "NO_GO"
            ),
            "limitations": [
                "Entry reason is joined from the decision record at the BUY fill tick.",
                "Legacy schema does not persist the exact exit trigger (stop, target, or SELL signal); exit signal context is not asserted as the trigger.",
                "Fee values are quote-denominated simulated fees; no real exchange commission asset or maker/taker classification exists in this Paper ledger.",
                "This is a retrospective audit of simulated fills, not a backtest or live execution result.",
            ],
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only per-symbol Paper FIFO accounting audit.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = build_audit(args.db)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "accounting_status": report["accounting_status"],
        "closed_round_trips": report["account"]["closed_round_trips"],
        "issues": report["issues"],
        "reconciliation_status": report["reconciliation"]["status"],
    }, indent=2))
    if report["accounting_status"] != "PASS":
        raise SystemExit("Paper accounting audit did not pass; see the full JSON report.")


if __name__ == "__main__":
    main()
