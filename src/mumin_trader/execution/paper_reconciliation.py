from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from decimal import Decimal
from typing import Any

ZERO = Decimal("0")
TOLERANCE = Decimal("0.00000002")


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _close(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= TOLERANCE


def reconcile_paper_ledger(connection: sqlite3.Connection) -> dict[str, Any]:
    """Reconcile Paper fills, FIFO inventory, fees, cash and marked equity.

    The function is read-only. It never changes the ledger or submits orders.
    """
    account = connection.execute(
        "SELECT initial_cash, cash, realized_pnl, fees_paid FROM bot_account WHERE id=1"
    ).fetchone()
    if account is None:
        return {
            "status": "INCOMPLETE",
            "checks": {},
            "issues": ["Paper account row is missing."],
        }

    initial_cash = _decimal(account["initial_cash"])
    recorded_cash = _decimal(account["cash"])
    recorded_realized = _decimal(account["realized_pnl"])
    recorded_fees = _decimal(account["fees_paid"])

    lots: dict[str, deque[dict[str, Decimal]]] = defaultdict(deque)
    expected_cash = initial_cash
    total_fees = ZERO
    fifo_realized = ZERO
    fill_matching = True
    issues: list[str] = []
    sell_results: list[dict[str, str]] = []

    fills = connection.execute(
        """SELECT fill_id, symbol, side, quantity, fill_price, gross_quote,
                  fee_quote, net_pnl
           FROM bot_fills ORDER BY fill_id ASC"""
    ).fetchall()

    for fill in fills:
        symbol = str(fill["symbol"]).upper()
        side = str(fill["side"]).upper()
        quantity = _decimal(fill["quantity"])
        fill_price = _decimal(fill["fill_price"])
        gross = _decimal(fill["gross_quote"])
        fee = _decimal(fill["fee_quote"])
        stored_net = _decimal(fill["net_pnl"])

        if quantity <= ZERO or fill_price <= ZERO or gross < ZERO or fee < ZERO:
            fill_matching = False
            issues.append(f"Fill {fill['fill_id']} has non-positive quantity/price or negative cash values.")
            continue
        if not _close(quantity * fill_price, gross):
            fill_matching = False
            issues.append(f"Fill {fill['fill_id']} gross quote does not equal quantity × fill price.")

        total_fees += fee
        if side == "BUY":
            expected_cash -= gross + fee
            lots[symbol].append({
                "quantity": quantity,
                "gross_per_unit": gross / quantity,
                "fee_per_unit": fee / quantity,
            })
            if not _close(stored_net, ZERO):
                fill_matching = False
                issues.append(f"BUY fill {fill['fill_id']} unexpectedly records realized PnL.")
            continue

        if side != "SELL":
            fill_matching = False
            issues.append(f"Fill {fill['fill_id']} has unsupported side {side}.")
            continue

        expected_cash += gross - fee
        remaining = quantity
        realized_for_sell = ZERO
        matched_quantity = ZERO
        while remaining > ZERO and lots[symbol]:
            lot = lots[symbol][0]
            matched = min(remaining, lot["quantity"])
            proceeds = gross * matched / quantity
            exit_fee = fee * matched / quantity
            entry_gross = lot["gross_per_unit"] * matched
            entry_fee = lot["fee_per_unit"] * matched
            realized_for_sell += proceeds - exit_fee - entry_gross - entry_fee
            lot["quantity"] -= matched
            remaining -= matched
            matched_quantity += matched
            if lot["quantity"] == ZERO:
                lots[symbol].popleft()

        if remaining > ZERO:
            fill_matching = False
            issues.append(
                f"SELL fill {fill['fill_id']} exceeds available FIFO inventory by {remaining} {symbol}."
            )
        fifo_realized += realized_for_sell
        if not _close(realized_for_sell, stored_net):
            fill_matching = False
            issues.append(
                f"SELL fill {fill['fill_id']} stored net PnL {stored_net} "
                f"does not match FIFO net PnL {realized_for_sell}."
            )
        sell_results.append({
            "fill_id": str(fill["fill_id"]),
            "symbol": symbol,
            "quantity": str(quantity),
            "matched_quantity": str(matched_quantity),
            "fifo_net_pnl": str(realized_for_sell),
            "stored_net_pnl": str(stored_net),
        })

    open_lots: dict[str, dict[str, Decimal]] = {}
    for symbol, symbol_lots in lots.items():
        quantity = sum((lot["quantity"] for lot in symbol_lots), ZERO)
        gross_cost = sum(
            (lot["gross_per_unit"] * lot["quantity"] for lot in symbol_lots), ZERO
        )
        entry_fees = sum(
            (lot["fee_per_unit"] * lot["quantity"] for lot in symbol_lots), ZERO
        )
        if quantity > ZERO:
            open_lots[symbol] = {
                "quantity": quantity,
                "gross_cost": gross_cost,
                "entry_fees": entry_fees,
            }

    positions = connection.execute(
        """SELECT symbol, quantity, entry_gross, entry_fee
           FROM bot_positions ORDER BY symbol"""
    ).fetchall()
    recorded_positions = {str(row["symbol"]).upper(): row for row in positions}
    positions_matching = set(open_lots) == set(recorded_positions)
    if not positions_matching:
        issues.append("Open FIFO inventory symbols do not match bot_positions.")
    for symbol in set(open_lots) | set(recorded_positions):
        expected = open_lots.get(symbol)
        recorded = recorded_positions.get(symbol)
        if expected is None or recorded is None:
            positions_matching = False
            continue
        if not (
            _close(expected["quantity"], _decimal(recorded["quantity"]))
            and _close(expected["gross_cost"], _decimal(recorded["entry_gross"]))
            and _close(expected["entry_fees"], _decimal(recorded["entry_fee"]))
        ):
            positions_matching = False
            issues.append(f"Open position {symbol} does not match its remaining FIFO lots.")

    cash_reconciled = _close(expected_cash, recorded_cash)
    fees_reconciled = _close(total_fees, recorded_fees)
    realized_reconciled = _close(fifo_realized, recorded_realized)
    if not cash_reconciled:
        issues.append(f"Cash mismatch: fills imply {expected_cash}, account records {recorded_cash}.")
    if not fees_reconciled:
        issues.append(f"Fee mismatch: fills sum to {total_fees}, account records {recorded_fees}.")
    if not realized_reconciled:
        issues.append(
            f"Realized PnL mismatch: FIFO implies {fifo_realized}, account records {recorded_realized}."
        )

    latest_prices = {
        str(row["symbol"]).upper(): row
        for row in connection.execute(
            """SELECT m.symbol, m.price, m.data_source, m.received_at_ms
               FROM bot_market_ticks m
               JOIN (SELECT symbol, MAX(id) AS max_id FROM bot_market_ticks GROUP BY symbol) x
                 ON x.max_id=m.id"""
        ).fetchall()
    }
    market_value = ZERO
    open_cost_basis = ZERO
    market_data: dict[str, dict[str, Any]] = {}
    marks_complete = True
    for symbol, lot in open_lots.items():
        mark = latest_prices.get(symbol)
        open_cost_basis += lot["gross_cost"] + lot["entry_fees"]
        if mark is None:
            marks_complete = False
            issues.append(f"No market mark is available for open position {symbol}.")
            continue
        price = _decimal(mark["price"])
        if price <= ZERO:
            marks_complete = False
            issues.append(f"Market mark for {symbol} is not positive.")
            continue
        value = lot["quantity"] * price
        market_value += value
        market_data[symbol] = {
            "price": str(price),
            "source": str(mark["data_source"]),
            "received_at_ms": int(mark["received_at_ms"]),
        }

    expected_equity = recorded_cash + market_value
    unrealized = market_value - open_cost_basis if marks_complete else None
    equity_identity = (
        initial_cash + fifo_realized + unrealized
        if unrealized is not None else None
    )
    equity_reconciled = (
        _close(expected_equity, equity_identity)
        if equity_identity is not None else False
    )
    if not equity_reconciled and marks_complete:
        issues.append(
            f"Equity identity mismatch: cash + marked positions is {expected_equity}; "
            f"initial + FIFO realized + unrealized is {equity_identity}."
        )

    checks = {
        "fills_match_fifo": fill_matching,
        "cash_matches_fills": cash_reconciled,
        "fees_match_fills": fees_reconciled,
        "realized_pnl_matches_fifo": realized_reconciled,
        "open_positions_match_fifo": positions_matching,
        "equity_identity_matches": equity_reconciled,
        "all_open_positions_have_marks": marks_complete,
    }
    status = "PASS" if all(checks.values()) else (
        "INCOMPLETE" if not marks_complete and all(
            value for key, value in checks.items() if key != "all_open_positions_have_marks"
        ) else "FAIL"
    )
    return {
        "status": status,
        "checks": checks,
        "issues": issues,
        "initial_cash": str(initial_cash),
        "expected_cash_from_fills": str(expected_cash),
        "recorded_cash": str(recorded_cash),
        "fifo_realized_pnl": str(fifo_realized),
        "recorded_realized_pnl": str(recorded_realized),
        "fees_from_fills": str(total_fees),
        "recorded_fees_paid": str(recorded_fees),
        "open_cost_basis_including_entry_fees": str(open_cost_basis),
        "open_market_value": str(market_value),
        "unrealized_pnl_including_entry_fees": str(unrealized) if unrealized is not None else None,
        "reconciled_equity": str(expected_equity),
        "equity_from_pnl_identity": str(equity_identity) if equity_identity is not None else None,
        "sell_fifo_matches": sell_results,
        "market_data": market_data,
    }
