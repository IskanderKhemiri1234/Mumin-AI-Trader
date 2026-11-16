from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_testnet_execution import (
    BinanceSpotTestnetExecution,
    SpotExecutionError,
    SpotOrderOutcomeUnknown,
)
from mumin_trader.exchanges.binance.testnet_order_preflight import TESTNET_REST_BASE_URL


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
            if str(key).lower() not in {
                "apikey", "api_key", "secret", "signature", "x-mbx-apikey",
                "binance_api_key", "binance_api_secret",
            }
        }
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


class Evidence:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.events_path = self.directory / "events.jsonl"
        self.summary_path = self.directory / "summary.json"

    def record(self, event: str, payload: Any) -> None:
        item = {"at": utc_now(), "event": event, "payload": json_safe(payload)}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, sort_keys=True, default=str) + "\n")
        os.chmod(self.events_path, 0o600)

    def summary(self, payload: dict[str, Any]) -> None:
        self.summary_path.write_text(
            json.dumps(json_safe(payload), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        os.chmod(self.summary_path, 0o600)


def require_testnet(settings: Settings) -> None:
    if settings.TRADING_ENV is not TradingEnvironment.TESTNET:
        raise SpotExecutionError("Refusing: TRADING_ENV must be TESTNET.")
    if settings.TRADING_MODE is not TradingMode.TESTNET:
        raise SpotExecutionError("Refusing: TRADING_MODE must be TESTNET.")
    if settings.ALLOW_MAINNET or settings.LIVE_TRADING_ENABLED:
        raise SpotExecutionError("Refusing: Mainnet/live flags must both be false.")
    if not settings.TESTNET_ORDER_PREFLIGHT_ENABLED or not settings.TESTNET_SPOT_EXECUTION_ENABLED:
        raise SpotExecutionError(
            "Refusing: enable both TESTNET_ORDER_PREFLIGHT_ENABLED and "
            "TESTNET_SPOT_EXECUTION_ENABLED explicitly in the private Termux environment."
        )
    if not settings.BINANCE_API_KEY.get_secret_value() or not settings.BINANCE_API_SECRET.get_secret_value():
        raise SpotExecutionError("Spot Testnet API credentials are missing from the private environment.")


def make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"spot-testnet-{stamp}-{uuid.uuid4().hex[:8]}"


def round_down_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise SpotExecutionError("Exchange LOT_SIZE stepSize is invalid.")
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def child_result(list_response: dict[str, Any]) -> dict[str, Any]:
    reports = list_response.get("orderReports", [])
    normalized = []
    for report in reports:
        normalized.append({
            "orderId": str(report.get("orderId", "")),
            "clientOrderId": str(report.get("clientOrderId", "")),
            "status": str(report.get("status", "")).upper(),
            "executedQty": str(report.get("executedQty", "0")),
            "origQty": str(report.get("origQty", "0")),
        })
    filled = [item for item in normalized if item["status"] == "FILLED"]
    siblings = [item for item in normalized if item["status"] in {"CANCELED", "EXPIRED"}]
    mutual_cancel = len(normalized) == 2 and len(filled) == 1 and len(siblings) == 1
    return {
        "orderListId": str(list_response.get("orderListId", "")),
        "listClientOrderId": str(list_response.get("listClientOrderId", "")),
        "listOrderStatus": str(list_response.get("listOrderStatus", "")).upper(),
        "children": normalized,
        "mutual_cancel_proven": mutual_cancel,
    }


async def wait_for_order(
    client: BinanceSpotTestnetExecution,
    *,
    symbol: str,
    client_order_id: str,
    timeout_seconds: int,
    poll_seconds: float,
    evidence: Evidence,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, Any] | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await client.get_order(symbol=symbol, client_order_id=client_order_id)
        if last is None:
            raise SpotOrderOutcomeUnknown("Submitted BUY is not queryable; do not submit another order.")
        evidence.record("buy_order_observed", last)
        status = str(last.get("status", "")).upper()
        if status in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            return last
        await asyncio.sleep(poll_seconds)
    if last is None:
        raise SpotOrderOutcomeUnknown("No exchange order response was received.")
    return last


async def watch_oco(
    client: BinanceSpotTestnetExecution,
    *,
    symbol: str,
    list_client_order_id: str,
    timeout_seconds: int,
    poll_seconds: float,
    evidence: Evidence,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict[str, Any] | None = None
    while asyncio.get_running_loop().time() < deadline:
        last = await client.oco.get_order_list(
            symbol=symbol, list_client_order_id=list_client_order_id,
        )
        if last is None:
            raise SpotOrderOutcomeUnknown("OCO list is not visible; its exchange outcome is unresolved.")
        evidence.record("oco_order_list_observed", last)
        state = client.oco._state(last)
        result = child_result(last)
        if result["mutual_cancel_proven"]:
            return last
        if state in {"PARTIAL_FILL_REVIEW", "CANCELED", "REJECT"}:
            return last
        if str(last.get("listOrderStatus", "")).upper() == "ALL_DONE":
            return last
        await asyncio.sleep(poll_seconds)
    return last or {}


async def run_cycle(args: argparse.Namespace) -> int:
    settings = Settings()
    require_testnet(settings)
    if args.confirm_testnet_cycle != "TESTNET_ONLY":
        raise SpotExecutionError("Explicit TESTNET_ONLY confirmation is required.")
    symbol = args.symbol.upper()
    if symbol not in settings.watchlist_symbols:
        raise SpotExecutionError("The selected symbol is not in the configured watchlist.")
    quantity = Decimal(args.quantity)
    buy_price = Decimal(args.buy_price)
    take_profit = Decimal(args.take_profit_price)
    stop_price = Decimal(args.stop_price)
    if not all(value.is_finite() and value > 0 for value in (quantity, buy_price, take_profit, stop_price)):
        raise SpotExecutionError("Quantity and prices must be finite positive decimal values.")
    if args.buy_timeout_seconds < 10 or args.buy_timeout_seconds > 900:
        raise SpotExecutionError("BUY timeout must be between 10 and 900 seconds.")
    if args.oco_timeout_seconds < 10 or args.oco_timeout_seconds > 3600:
        raise SpotExecutionError("OCO timeout must be between 10 and 3600 seconds.")
    if args.poll_seconds < 1 or args.poll_seconds > 10:
        raise SpotExecutionError("Polling interval must be between 1 and 10 seconds.")

    run_id = make_run_id()
    directory = Path(args.evidence_root).expanduser() / run_id
    evidence = Evidence(directory)
    database = directory / "testnet-ledger.sqlite3"
    os.chmod(directory, 0o700)
    store = SQLiteOrderStore(str(database))
    client = BinanceSpotTestnetExecution(settings, store)
    buy_client_id = f"mumin-buy-{uuid.uuid4().hex[:12]}"
    list_client_id = f"mumin-oco-{uuid.uuid4().hex[:12]}"
    summary: dict[str, Any] = {
        "run_id": run_id,
        "environment": "BINANCE_SPOT_TESTNET",
        "rest_base_url": TESTNET_REST_BASE_URL,
        "symbol": symbol,
        "buy_client_order_id": buy_client_id,
        "oco_list_client_order_id": list_client_id,
        "database": str(database),
        "evidence_directory": str(directory),
        "started_at": utc_now(),
        "status": "RUNNING",
        "mainnet_used": False,
        "secrets_written": False,
    }
    evidence.record("run_started", {
        "run_id": run_id, "symbol": symbol, "quantity": quantity,
        "buy_price": buy_price, "take_profit_price": take_profit,
        "stop_price": stop_price, "rest_base_url": TESTNET_REST_BASE_URL,
    })
    try:
        initial = await client.reconcile()
        evidence.record("initial_reconciliation", initial)
        if initial["discrepancies"] or initial["halted"]:
            raise SpotExecutionError(
                "Initial Testnet reconciliation is not clean. No BUY was submitted; inspect evidence."
            )
        if not initial["realized_pnl"]["complete"]:
            raise SpotExecutionError("Initial FIFO/fee ledger is incomplete; no BUY was submitted.")

        ticker = await client._ticker_price(symbol)
        symbol_info = await client._exchange_symbol(symbol)
        base_asset = str(symbol_info["baseAsset"]).upper()
        quote_asset = str(symbol_info["quoteAsset"]).upper()
        notional = quantity * buy_price
        if quote_asset != settings.QUOTE_ASSET.upper():
            raise SpotExecutionError("Lifecycle runner only permits the configured USDT quote asset.")
        if notional < settings.MIN_TRADE_ALLOCATION_USD or notional > settings.MAX_TRADE_ALLOCATION_USD:
            raise SpotExecutionError(
                f"BUY notional {notional} is outside configured bounds "
                f"{settings.MIN_TRADE_ALLOCATION_USD}..{settings.MAX_TRADE_ALLOCATION_USD} USDT."
            )
        if buy_price <= 0:
            raise SpotExecutionError("BUY limit price must be positive.")
        evidence.record("market_and_symbol_snapshot", {
            "last_price": ticker, "base_asset": base_asset, "quote_asset": quote_asset,
            "symbol_info": symbol_info, "buy_notional": notional,
        })

        # Validate both legs before exposing any Testnet capital.
        buy_params = await client._validator.validate_limit_order(
            symbol=symbol, side="BUY", quantity=quantity,
            price=buy_price, client_order_id=buy_client_id,
        )
        oco_params = await client.oco._validate_request(
            symbol=symbol, quantity=quantity,
            take_profit_price=take_profit, stop_price=stop_price,
            stop_limit_price=None, stop_type="STOP_LOSS",
        )
        evidence.record("pre_submit_validation", {
            "buy_order": buy_params, "oco_sell_template": oco_params,
        })

        buy_response = await client.submit_limit_order(
            symbol=symbol, side="BUY", quantity=quantity,
            price=buy_price, client_order_id=buy_client_id,
        )
        evidence.record("buy_submission_response", buy_response)
        buy_order = await wait_for_order(
            client, symbol=symbol, client_order_id=buy_client_id,
            timeout_seconds=args.buy_timeout_seconds,
            poll_seconds=args.poll_seconds, evidence=evidence,
        )
        if str(buy_order.get("status", "")).upper() not in {"FILLED", "CANCELED"}:
            # The order is known and still open; cancel its remainder once, then query.
            buy_order = await client.cancel_order(symbol=symbol, client_order_id=buy_client_id)
            evidence.record("buy_cancel_after_timeout", buy_order)
            buy_order = await client.get_order(symbol=symbol, client_order_id=buy_client_id) or buy_order
        evidence.record("buy_final_order", buy_order)
        buy_order_id = buy_order.get("orderId")
        if buy_order_id is None:
            raise SpotOrderOutcomeUnknown("BUY response has no exchange orderId; do not retry.")
        await client.sync_trade_history()
        buy_summary = store.order_fill_summary(
            symbol, int(buy_order_id), base_asset=base_asset,
        )
        buy_fills = store.trade_fills_for_order(symbol, int(buy_order_id))
        evidence.record("buy_fills_from_myTrades", {"summary": buy_summary, "fills": buy_fills})
        net_quantity = Decimal(buy_summary["net_base_quantity"])
        if net_quantity <= 0:
            raise SpotExecutionError("BUY produced no net base-asset fill; no OCO was submitted.")

        filters = {item.get("filterType"): item for item in symbol_info.get("filters", [])}
        lot = filters.get("LOT_SIZE")
        if not lot:
            raise SpotExecutionError("LOT_SIZE filter is missing; cannot size protective OCO.")
        step = Decimal(str(lot["stepSize"]))
        oco_quantity = round_down_to_step(net_quantity, step)
        if oco_quantity <= 0:
            raise SpotExecutionError("Net filled quantity is below the exchange OCO quantity step.")
        if oco_quantity < net_quantity:
            evidence.record("base_fee_or_step_dust", {
                "net_bought_quantity": net_quantity,
                "oco_protected_quantity": oco_quantity,
                "unprotected_dust": net_quantity - oco_quantity,
            })

        # Revalidate against the current Testnet ticker immediately before OCO.
        await client.oco._validate_request(
            symbol=symbol, quantity=oco_quantity,
            take_profit_price=take_profit, stop_price=stop_price,
            stop_limit_price=None, stop_type="STOP_LOSS",
        )
        oco_response = await client.oco.place_sell_oco(
            symbol=symbol, quantity=oco_quantity,
            take_profit_price=take_profit, stop_price=stop_price,
            list_client_order_id=list_client_id,
        )
        evidence.record("oco_submission_response", oco_response)
        if not oco_response.get("orderListId") or len(oco_response.get("orders", [])) != 2:
            raise SpotOrderOutcomeUnknown("OCO response lacks its parent or two child order IDs.")
        oco_final = await watch_oco(
            client, symbol=symbol, list_client_order_id=list_client_id,
            timeout_seconds=args.oco_timeout_seconds,
            poll_seconds=args.poll_seconds, evidence=evidence,
        )
        evidence.record("oco_final_exchange_state", oco_final)
        ids = [str(item.get("orderId", "")) for item in oco_final.get("orders", [])]
        child_fills = {
            order_id: store.trade_fills_for_order(symbol, int(order_id))
            for order_id in ids if order_id.isdigit()
        }
        evidence.record("oco_child_myTrades_fills", child_fills)
        await client.sync_trade_history()
        pnl = store.realized_pnl_report(settings.QUOTE_ASSET)
        evidence.record("fifo_realized_pnl_report", pnl)
        reconciliation = await client.reconcile()
        evidence.record("final_reconciliation", reconciliation)
        fill_discrepancies = store.order_fill_quantity_discrepancies()
        unmatched = store.unmatched_trade_order_ids()
        proof = child_result(oco_final)
        fifo_complete = bool(pnl.get("complete"))
        reconciliation_clean = not reconciliation["discrepancies"] and not reconciliation["halted"]
        summary.update({
            "buy_order_id": str(buy_order_id),
            "buy_status": str(buy_order.get("status", "")).upper(),
            "buy_fill_summary": buy_summary,
            "order_list_id": str(oco_final.get("orderListId", oco_response.get("orderListId", ""))),
            "oco_children": proof["children"],
            "mutual_cancel_proven": proof["mutual_cancel_proven"],
            "fifo_complete": fifo_complete,
            "fifo_realized_pnl": pnl,
            "fill_discrepancies": fill_discrepancies,
            "unmatched_trade_orders": unmatched,
            "reconciliation_discrepancies": reconciliation["discrepancies"],
            "reconciliation_clean": reconciliation_clean,
            "status": (
                "TESTNET_CYCLE_VERIFIED"
                if proof["mutual_cancel_proven"] and fifo_complete
                and not fill_discrepancies and not unmatched and reconciliation_clean
                else "TESTNET_CYCLE_INCOMPLETE"
            ),
            "finished_at": utc_now(),
        })
        return 0 if summary["status"] == "TESTNET_CYCLE_VERIFIED" else 2
    except Exception as exc:
        store.halt("TESTNET_LIFECYCLE_INCOMPLETE")
        summary.update({
            "status": "TESTNET_LIFECYCLE_INCOMPLETE",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "finished_at": utc_now(),
        })
        evidence.record("run_error", {"type": type(exc).__name__, "message": str(exc)})
        return 2
    finally:
        evidence.summary(summary)
        await client.close()
        print(json.dumps({
            "status": summary["status"],
            "run_id": run_id,
            "symbol": symbol,
            "buy_client_order_id": buy_client_id,
            "oco_list_client_order_id": list_client_id,
            "evidence_directory": str(directory),
            "summary_file": str(evidence.summary_path),
            "mainnet_used": False,
            "secrets_written": False,
        }, indent=2))


async def watch_existing(args: argparse.Namespace) -> int:
    settings = Settings()
    require_testnet(settings)
    directory = Path(args.evidence_dir).expanduser()
    evidence = Evidence(directory)
    store = SQLiteOrderStore(args.database)
    client = BinanceSpotTestnetExecution(settings, store)
    try:
        result = await watch_oco(
            client, symbol=args.symbol.upper(),
            list_client_order_id=args.list_client_order_id,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds, evidence=evidence,
        )
        await client.sync_trade_history()
        pnl = store.realized_pnl_report(settings.QUOTE_ASSET)
        reconciliation = await client.reconcile()
        proof = child_result(result)
        summary = {
            "environment": "BINANCE_SPOT_TESTNET",
            "symbol": args.symbol.upper(),
            "order_list_id": str(result.get("orderListId", "")),
            "oco_list_client_order_id": args.list_client_order_id,
            "oco_children": proof["children"],
            "mutual_cancel_proven": proof["mutual_cancel_proven"],
            "fifo_complete": bool(pnl.get("complete")),
            "fifo_realized_pnl": pnl,
            "fill_discrepancies": store.order_fill_quantity_discrepancies(),
            "unmatched_trade_orders": store.unmatched_trade_order_ids(),
            "reconciliation_discrepancies": reconciliation["discrepancies"],
            "reconciliation_clean": not reconciliation["discrepancies"] and not reconciliation["halted"],
            "status": "TESTNET_CYCLE_VERIFIED" if (
                proof["mutual_cancel_proven"] and pnl.get("complete")
                and not store.order_fill_quantity_discrepancies()
                and not store.unmatched_trade_order_ids()
                and not reconciliation["discrepancies"] and not reconciliation["halted"]
            ) else "TESTNET_CYCLE_INCOMPLETE",
            "finished_at": utc_now(),
        }
        evidence.summary(summary)
        print(json.dumps({
            "status": summary["status"],
            "order_list_id": summary["order_list_id"],
            "oco_children": summary["oco_children"],
            "evidence_directory": str(directory),
            "summary_file": str(evidence.summary_path),
            "mainnet_used": False,
            "secrets_written": False,
        }, indent=2))
        return 0 if summary["status"] == "TESTNET_CYCLE_VERIFIED" else 2
    finally:
        await client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Supervised BUY -> protective OCO -> FIFO evidence runner. Binance Spot Testnet only."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run one explicitly confirmed Testnet lifecycle.")
    run.add_argument("--symbol", default="BTCUSDT")
    run.add_argument("--quantity", required=True)
    run.add_argument("--buy-price", required=True)
    run.add_argument("--take-profit-price", required=True)
    run.add_argument("--stop-price", required=True)
    run.add_argument("--buy-timeout-seconds", type=int, default=120)
    run.add_argument("--oco-timeout-seconds", type=int, default=900)
    run.add_argument("--poll-seconds", type=float, default=2)
    run.add_argument("--evidence-root", default="artifacts/binance-spot-testnet-evidence")
    run.add_argument("--confirm-testnet-cycle", required=True, choices=("TESTNET_ONLY",))

    watch = commands.add_parser("watch", help="Continue monitoring a previously placed OCO; sends no orders.")
    watch.add_argument("--symbol", required=True)
    watch.add_argument("--list-client-order-id", required=True)
    watch.add_argument("--database", required=True)
    watch.add_argument("--evidence-dir", required=True)
    watch.add_argument("--timeout-seconds", type=int, default=1800)
    watch.add_argument("--poll-seconds", type=float, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "run":
            code = asyncio.run(run_cycle(args))
        else:
            code = asyncio.run(watch_existing(args))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
