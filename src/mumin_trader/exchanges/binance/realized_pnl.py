from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal
from typing import Any

ZERO = Decimal("0")


class RealizedPnLError(ValueError):
    pass


def build_realized_pnl_report(
    fills: list[dict[str, Any]],
    *,
    quote_asset: str = "USDT",
    bnb_asset: str = "BNB",
) -> dict[str, Any]:
    """Build a deterministic FIFO report from exchange trade fills.

    BNB commissions require commission_quote_value captured from a historical
    BNB/quote candle. That valuation is explicitly a mark, not an exchange-
    reported conversion. Unknown fee assets and sells without FIFO inventory
    are reported as unresolved instead of manufacturing PnL.
    """
    quote = quote_asset.upper()
    bnb = bnb_asset.upper()
    ordered = sorted(fills, key=lambda row: (int(row["trade_time_ms"]), int(row["trade_id"])))
    lots: dict[str, deque[dict[str, Decimal]]] = defaultdict(deque)
    summaries: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    total_realized = ZERO
    total_fee_quote_mark = ZERO
    maker_count = taker_count = 0
    maker_fees_quote = taker_fees_quote = ZERO

    for fill in ordered:
        symbol = str(fill["symbol"]).upper()
        if not symbol.endswith(quote):
            unresolved.append(f"{symbol}:unsupported_quote_asset")
            continue
        base = symbol.removesuffix(quote)
        price = Decimal(str(fill["price"]))
        qty = Decimal(str(fill["quantity"]))
        quote_qty = Decimal(str(fill["quote_quantity"]))
        commission = Decimal(str(fill["commission"]))
        fee_asset = str(fill["commission_asset"]).upper()
        is_buyer = bool(fill["is_buyer"])
        is_maker = bool(fill.get("is_maker", False))
        fee_quote_value: Decimal | None

        values = (price, qty, quote_qty, commission)
        if any(not value.is_finite() or value < ZERO for value in values) or price <= ZERO or qty <= ZERO:
            raise RealizedPnLError(f"{symbol}:{fill['trade_id']}: invalid fill amount")
        if commission == ZERO:
            fee_quote_value = ZERO
        elif fee_asset == quote:
            fee_quote_value = commission
        elif fee_asset == base:
            fee_quote_value = commission * price
        elif fee_asset == bnb:
            raw_value = fill.get("commission_quote_value")
            fee_quote_value = None if raw_value is None else Decimal(str(raw_value))
            if fee_quote_value is None or not fee_quote_value.is_finite() or fee_quote_value < ZERO:
                unresolved.append(f"{symbol}:{fill['trade_id']}:missing_historical_BNB_fee_valuation")
                continue
        else:
            unresolved.append(f"{symbol}:{fill['trade_id']}:unsupported_fee_asset:{fee_asset}")
            continue

        if is_maker:
            maker_count += 1
            maker_fees_quote += fee_quote_value
        else:
            taker_count += 1
            taker_fees_quote += fee_quote_value
        total_fee_quote_mark += fee_quote_value
        report = summaries.setdefault(symbol, {
            "realized_pnl_quote": ZERO,
            "closed_quantity": ZERO,
            "open_quantity": ZERO,
            "open_cost_basis_quote": ZERO,
            "buy_fills": 0,
            "sell_fills": 0,
            "maker_fills": 0,
            "taker_fills": 0,
            "fees_quote_equivalent_mark": ZERO,
            "entry_fee_quote_equivalent_mark": ZERO,
            "exit_fee_quote_equivalent_mark": ZERO,
            "gross_buy_quote": ZERO,
            "gross_sell_quote": ZERO,
            "gross_buy_quantity": ZERO,
            "gross_sell_quantity": ZERO,
        })
        report["maker_fills" if is_maker else "taker_fills"] += 1
        report["fees_quote_equivalent_mark"] += fee_quote_value

        if is_buyer:
            report["gross_buy_quote"] += quote_qty
            report["gross_buy_quantity"] += qty
            report["entry_fee_quote_equivalent_mark"] += fee_quote_value
            net_qty = qty - (commission if fee_asset == base else ZERO)
            if net_qty <= ZERO:
                unresolved.append(f"{symbol}:{fill['trade_id']}:base_fee_consumes_buy_fill")
                continue
            # Base-asset fee reduces delivered units; quote/BNB fees add to acquisition cost.
            entry_cost = quote_qty + (fee_quote_value if fee_asset in {quote, bnb} else ZERO)
            lots[base].append({"quantity": net_qty, "unit_cost": entry_cost / net_qty})
            report["buy_fills"] += 1
            continue

        report["gross_sell_quote"] += quote_qty
        report["gross_sell_quantity"] += qty
        report["exit_fee_quote_equivalent_mark"] += fee_quote_value
        disposed_qty = qty + (commission if fee_asset == base else ZERO)
        net_proceeds = quote_qty - (fee_quote_value if fee_asset in {quote, bnb} else ZERO)
        if net_proceeds < ZERO:
            unresolved.append(f"{symbol}:{fill['trade_id']}:fee_exceeds_sell_proceeds")
            continue
        remaining = disposed_qty
        cost = ZERO
        while remaining > ZERO and lots[base]:
            lot = lots[base][0]
            consumed = min(remaining, lot["quantity"])
            cost += consumed * lot["unit_cost"]
            lot["quantity"] -= consumed
            remaining -= consumed
            if lot["quantity"] == ZERO:
                lots[base].popleft()
        if remaining > Decimal("0.000000000000000001"):
            unresolved.append(
                f"{symbol}:{fill['trade_id']}:sell_exceeds_FIFO_inventory:{remaining}"
            )
            continue
        realized = net_proceeds - cost
        report["realized_pnl_quote"] += realized
        report["closed_quantity"] += qty
        report["sell_fills"] += 1
        total_realized += realized

    for symbol, report in summaries.items():
        base = symbol.removesuffix(quote)
        open_qty = sum((lot["quantity"] for lot in lots[base]), ZERO)
        open_cost = sum((lot["quantity"] * lot["unit_cost"] for lot in lots[base]), ZERO)
        report["open_quantity"] = open_qty
        report["open_cost_basis_quote"] = open_cost
        for key, value in list(report.items()):
            if isinstance(value, Decimal):
                report[key] = str(value)

    return {
        "method": "FIFO",
        "quote_asset": quote,
        "fee_valuation": "quote/base exact arithmetic; BNB uses stored historical 1m candle close mark",
        "realized_pnl_quote": str(total_realized),
        "fees_quote_equivalent_mark": str(total_fee_quote_mark),
        "maker_fees_quote_equivalent_mark": str(maker_fees_quote),
        "taker_fees_quote_equivalent_mark": str(taker_fees_quote),
        "maker_fills": maker_count,
        "taker_fills": taker_count,
        "symbols": summaries,
        "unresolved": unresolved,
        "complete": not unresolved,
    }
