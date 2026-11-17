from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any

from config.config import Settings, TradingEnvironment, TradingMode
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_testnet_oco import validate_fixed_exit_risk
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
                "apikey", "api_key", "secret", "api_secret", "secret_key",
                "signature", "x-mbx-apikey", "binance_api_key",
                "binance_api_secret", "binance_secret",
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


def require_testnet(
    settings: Settings,
    *,
    allow_trade_cap_override: bool = False,
) -> None:
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

    fixed_limits = {
        "INITIAL_CAPITAL_USD": Decimal("70.00"),
        "MAX_CAPITAL_ALLOCATION_USD": Decimal("70.00"),
        "MIN_TRADE_ALLOCATION_USD": Decimal("5.00"),
        "CAPITAL_RESERVE_PERCENT": Decimal("20.00"),
        "MAX_RISK_PER_TRADE_PERCENT": Decimal("0.50"),
        "MAX_DAILY_LOSS_PERCENT": Decimal("2.00"),
        "MAX_DRAWDOWN_PERCENT": Decimal("5.00"),
        "MAX_OPEN_POSITIONS": 2,
    }
    for name, expected in fixed_limits.items():
        if getattr(settings, name) != expected:
            raise SpotExecutionError(
                f"Refusing: {name} differs from the approved fixed Testnet risk policy."
            )
    trade_cap = settings.MAX_TRADE_ALLOCATION_USD
    if trade_cap != Decimal("5.00"):
        if (
            not allow_trade_cap_override
            or trade_cap <= Decimal("5.00")
            or trade_cap > Decimal("6.00")
        ):
            raise SpotExecutionError(
                "Refusing: Testnet trade cap is fixed at 5.00 USDT; a one-run override requires explicit operator action and cannot exceed 6.00 USDT."
            )
    if settings.QUOTE_ASSET.upper() != "USDT":
        raise SpotExecutionError("Refusing: Testnet execution quote asset is fixed to USDT.")
    if settings.BNB_FEE_ASSET.upper() != "BNB":
        raise SpotExecutionError("Refusing: Testnet fee reserve asset is fixed to BNB.")
    if not settings.ENABLE_BNB_FEE_DISCOUNT or not settings.REQUIRE_BNB_BALANCE_FOR_DISCOUNT:
        raise SpotExecutionError("Refusing: explicit BNB fee-discount and balance-reserve checks are required.")


def safe_error_message(exc: Exception, settings: Settings) -> str:
    message = str(exc)
    for secret in (
        settings.BINANCE_API_KEY.get_secret_value(),
        settings.BINANCE_API_SECRET.get_secret_value(),
    ):
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return re.sub(
        r"(?i)(signature|api[_-]?key|secret)=([^&\s]+)",
        r"\1=[REDACTED]",
        message,
    )


def make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"spot-testnet-{stamp}-{uuid.uuid4().hex[:8]}"


def round_down_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise SpotExecutionError("Exchange LOT_SIZE stepSize is invalid.")
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def opoco_capability(metadata: dict[str, Any]) -> dict[str, Any]:
    """Report exchange-declared OPOCO eligibility without implying order acceptance."""
    flags = {
        "oto_allowed": metadata.get("otoAllowed") is True,
        "opo_allowed": metadata.get("opoAllowed") is True,
        "oco_allowed": metadata.get("ocoAllowed") is True,
    }
    return {
        **flags,
        "supported_by_exchange_info": all(flags.values()),
        "evidence_scope": "EXCHANGE_INFO_CAPABILITY_ONLY",
    }


def commission_asset_mismatches(
    fills: list[dict[str, Any]], expected_asset: str = "BNB",
) -> list[dict[str, str]]:
    """Return positive-fee fills charged in an asset other than the preflighted asset."""
    expected = expected_asset.upper()
    mismatches: list[dict[str, str]] = []
    for fill in fills:
        asset = str(fill.get("commission_asset", "")).upper()
        try:
            amount = Decimal(str(fill.get("commission", "0")))
        except (InvalidOperation, ValueError):
            mismatches.append({
                "trade_id": str(fill.get("trade_id", "")),
                "commission": str(fill.get("commission", "")),
                "commission_asset": asset,
                "reason": "invalid_commission_amount",
            })
            continue
        if not amount.is_finite() or amount < 0:
            mismatches.append({
                "trade_id": str(fill.get("trade_id", "")),
                "commission": str(fill.get("commission", "")),
                "commission_asset": asset,
                "reason": "invalid_commission_amount",
            })
        elif amount > 0 and asset != expected:
            mismatches.append({
                "trade_id": str(fill.get("trade_id", "")),
                "commission": str(amount),
                "commission_asset": asset,
                "reason": "unexpected_commission_asset",
            })
    return mismatches


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
    order_list_id = str(list_response.get("orderListId", ""))
    list_status = str(list_response.get("listOrderStatus", "")).upper()
    child_ids = {item["orderId"] for item in normalized}
    mutual_cancel = (
        bool(order_list_id)
        and list_status == "ALL_DONE"
        and len(normalized) == 2
        and len(child_ids) == 2
        and all(child_ids)
        and len(filled) == 1
        and len(siblings) == 1
    )
    return {
        "orderListId": order_list_id,
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


def exit_risk_metrics(
    average_entry_price: Decimal,
    take_profit_price: Decimal,
    stop_price: Decimal,
) -> dict[str, Any]:
    if (
        not average_entry_price.is_finite()
        or average_entry_price <= 0
        or take_profit_price <= average_entry_price
        or stop_price >= average_entry_price
    ):
        return {
            "average_entry_price": str(average_entry_price),
            "actual_stop_loss_percent": None,
            "actual_take_profit_percent": None,
            "exit_risk_policy_match": False,
        }
    stop_percent = (
        (average_entry_price - stop_price) / average_entry_price * Decimal("100")
    )
    take_profit_percent = (
        (take_profit_price - average_entry_price) / average_entry_price * Decimal("100")
    )
    tolerance = Decimal("0.02")
    return {
        "average_entry_price": str(average_entry_price),
        "actual_stop_loss_percent": str(stop_percent),
        "actual_take_profit_percent": str(take_profit_percent),
        "exit_risk_policy_match": (
            abs(stop_percent - Decimal("0.50")) <= tolerance
            and abs(take_profit_percent - Decimal("1.25")) <= tolerance
        ),
    }


def opoco_exit_risk_metrics(
    average_entry_price: Decimal,
    protective_children: list[dict[str, Any]],
) -> dict[str, Any]:
    above = next(
        (item for item in protective_children if item.get("type") == "LIMIT_MAKER"),
        None,
    )
    below = next(
        (item for item in protective_children if item.get("type") == "STOP_LOSS_LIMIT"),
        None,
    )
    if above is None or below is None:
        result = exit_risk_metrics(average_entry_price, Decimal("0"), Decimal("0"))
        result["exchange_exit_prices_verified"] = False
        return result
    try:
        take_profit = Decimal(str(above["price"]))
        stop_price = Decimal(str(below["stopPrice"]))
    except (KeyError, ArithmeticError, ValueError):
        result = exit_risk_metrics(average_entry_price, Decimal("0"), Decimal("0"))
        result["exchange_exit_prices_verified"] = False
        return result
    result = exit_risk_metrics(average_entry_price, take_profit, stop_price)
    result["exchange_take_profit_price"] = str(take_profit)
    result["exchange_stop_price"] = str(stop_price)
    result["exchange_exit_prices_verified"] = (
        take_profit.is_finite() and stop_price.is_finite()
        and take_profit > 0 and stop_price > 0
    )
    result["exit_risk_policy_match"] = (
        result["exit_risk_policy_match"] and result["exchange_exit_prices_verified"]
    )
    return result


def opoco_result(list_response: dict[str, Any]) -> dict[str, Any]:
    reports = list_response.get("orderReports", [])
    normalized = [
        {
            "orderId": str(item.get("orderId", "")),
            "clientOrderId": str(item.get("clientOrderId", "")),
            "type": str(item.get("type", "")).upper(),
            "side": str(item.get("side", "")).upper(),
            "status": str(item.get("status", "")).upper(),
            "executedQty": str(item.get("executedQty", "0")),
            "origQty": str(item.get("origQty", "0")),
            "price": str(item.get("price", "0")),
            "stopPrice": str(item.get("stopPrice", "0")),
        }
        for item in reports if isinstance(item, dict)
    ]
    working = next(
        (item for item in normalized if item["side"] == "BUY" and item["type"] == "LIMIT"),
        None,
    )
    above = next((item for item in normalized if item["type"] == "LIMIT_MAKER"), None)
    below = next((item for item in normalized if item["type"] == "STOP_LOSS_LIMIT"), None)
    order_list_id = str(list_response.get("orderListId", ""))
    list_status = str(list_response.get("listOrderStatus", "")).upper()
    ids = [item["orderId"] for item in normalized]
    mutual_cancel = bool(
        working and above and below
        and order_list_id and list_response.get("listClientOrderId")
        and list_status == "ALL_DONE"
        and len(normalized) == 3
        and len(set(ids)) == 3 and all(ids)
        and working["status"] == "FILLED"
        and (
            (above["status"] == "FILLED" and below["status"] in {"CANCELED", "EXPIRED"})
            or (below["status"] == "FILLED" and above["status"] in {"CANCELED", "EXPIRED"})
        )
    )
    return {
        "orderListId": order_list_id,
        "listClientOrderId": str(list_response.get("listClientOrderId", "")),
        "listOrderStatus": list_status,
        "working_order": working,
        "protective_children": [item for item in (above, below) if item],
        "children": normalized,
        "mutual_cancel_proven": mutual_cancel,
    }


async def watch_opoco(
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
        last = await client.opoco.get_order_list(
            symbol=symbol,
            list_client_order_id=list_client_order_id,
        )
        if last is None:
            raise SpotOrderOutcomeUnknown("OPOCO list is not visible; its exchange outcome is unresolved.")
        evidence.record("opoco_order_list_observed", last)
        state = client.opoco._state(last)
        if state in {"COMPLETE", "CANCELED", "PARTIAL_FILL_REVIEW"}:
            return last
        await asyncio.sleep(poll_seconds)
    return last or {}


async def run_cycle(args: argparse.Namespace) -> int:
    if args.testnet_max_order_usdt is None:
        settings = Settings()
    else:
        testnet_cap = Decimal(args.testnet_max_order_usdt)
        if not testnet_cap.is_finite() or testnet_cap <= 0 or testnet_cap > Decimal("6.00"):
            raise SpotExecutionError("The Testnet-only order-cap override must be between 0 and 6.00 USDT.")
        settings = Settings(MAX_TRADE_ALLOCATION_USD=testnet_cap)
    require_testnet(settings, allow_trade_cap_override=args.testnet_max_order_usdt is not None)
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
        "testnet_max_order_usdt": str(settings.MAX_TRADE_ALLOCATION_USD),
        "testnet_cap_override_used": args.testnet_max_order_usdt is not None,
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

        # Validate the configured 0.50% stop and 1.25% target before exposing any Testnet capital.
        validate_fixed_exit_risk(buy_price, take_profit, stop_price)
        buy_params = await client._validator.validate_limit_order(
            symbol=symbol, side="BUY", quantity=quantity,
            price=buy_price, client_order_id=buy_client_id,
        )
        oco_params = await client.oco._validate_request(
            symbol=symbol, quantity=quantity,
            take_profit_price=take_profit, stop_price=stop_price,
            stop_limit_price=None, stop_type="STOP_LOSS",
        )
        worst_oco_exit_notional = quantity * max(take_profit, stop_price)
        bnb_fee_preflight = await client._bnb_fee_readiness(
            symbol=symbol,
            buy_notional=notional,
            sell_notional=worst_oco_exit_notional,
        )
        evidence.record("pre_submit_validation", {
            "buy_order": buy_params,
            "oco_sell_template": oco_params,
            "bnb_fee_preflight": bnb_fee_preflight,
        })

        try:
            buy_response = await client.submit_limit_order(
                symbol=symbol, side="BUY", quantity=quantity,
                price=buy_price, client_order_id=buy_client_id,
            )
        except SpotOrderOutcomeUnknown:
            # Resolve by the same durable client ID; never create a replacement ID.
            buy_response = await client.get_order(
                symbol=symbol, client_order_id=buy_client_id,
            )
            if buy_response is None:
                raise
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
        buy_status = str(buy_order.get("status", "")).upper()
        if buy_status not in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
            raise SpotOrderOutcomeUnknown(
                "BUY may still be active after cancellation attempt; do not place OCO or another BUY."
            )
        buy_order_id = buy_order.get("orderId")
        if buy_order_id is None:
            raise SpotOrderOutcomeUnknown("BUY response has no exchange orderId; do not retry.")
        await client.sync_trade_history()
        buy_summary = store.order_fill_summary(
            symbol, int(buy_order_id), base_asset=base_asset,
        )
        gross_buy_quantity = Decimal(buy_summary["gross_quantity"])
        average_entry_price = (
            Decimal(buy_summary["quote_quantity"]) / gross_buy_quantity
            if gross_buy_quantity > 0 else Decimal("0")
        )
        exit_risk = exit_risk_metrics(average_entry_price, take_profit, stop_price)
        buy_fills = store.trade_fills_for_order(symbol, int(buy_order_id))
        buy_fee_asset_mismatches = commission_asset_mismatches(buy_fills, settings.BNB_FEE_ASSET)
        evidence.record("buy_fills_from_myTrades", {
            "summary": buy_summary,
            "fills": buy_fills,
            "fee_asset_mismatches": buy_fee_asset_mismatches,
        })
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
        sellable_net_quantity = round_down_to_step(net_quantity, step)
        if oco_quantity != sellable_net_quantity:
            raise SpotExecutionError("Protective OCO quantity does not cover the complete net sellable quantity.")
        unprotected_dust = net_quantity - oco_quantity
        if unprotected_dust > 0:
            evidence.record("base_fee_or_step_dust", {
                "net_bought_quantity": net_quantity,
                "oco_protected_quantity": oco_quantity,
                "unprotected_dust": unprotected_dust,
            })

        # Revalidate against the current Testnet ticker immediately before OCO.
        await client.oco._validate_request(
            symbol=symbol, quantity=oco_quantity,
            take_profit_price=take_profit, stop_price=stop_price,
            stop_limit_price=None, stop_type="STOP_LOSS",
        )
        try:
            oco_response = await client.oco.place_sell_oco(
                symbol=symbol, quantity=oco_quantity,
                take_profit_price=take_profit, stop_price=stop_price,
                list_client_order_id=list_client_id,
            )
        except SpotOrderOutcomeUnknown:
            # Query the existing list by its original listClientOrderId.
            # The adapter never retries an ambiguous OCO POST.
            oco_response = await client.oco.get_order_list(
                symbol=symbol, list_client_order_id=list_client_id,
            )
            if oco_response is None:
                raise
        evidence.record("oco_submission_response", oco_response)
        if not oco_response.get("orderListId") or len(oco_response.get("orders", [])) != 2:
            raise SpotOrderOutcomeUnknown("OCO response lacks its parent or two child order IDs.")
        oco_final = await watch_oco(
            client, symbol=symbol, list_client_order_id=list_client_id,
            timeout_seconds=args.oco_timeout_seconds,
            poll_seconds=args.poll_seconds, evidence=evidence,
        )
        evidence.record("oco_final_exchange_state", oco_final)
        await client.sync_trade_history()
        ids = [str(item.get("orderId", "")) for item in oco_final.get("orders", [])]
        child_fills = {
            order_id: store.trade_fills_for_order(symbol, int(order_id))
            for order_id in ids if order_id.isdigit()
        }
        oco_fee_asset_mismatches = [
            mismatch
            for fills in child_fills.values()
            for mismatch in commission_asset_mismatches(fills, settings.BNB_FEE_ASSET)
        ]
        evidence.record("oco_child_myTrades_fills", {
            "fills_by_order_id": child_fills,
            "fee_asset_mismatches": oco_fee_asset_mismatches,
        })
        pnl = store.realized_pnl_report(settings.QUOTE_ASSET)
        evidence.record("fifo_realized_pnl_report", pnl)
        reconciliation = await client.reconcile()
        evidence.record("final_reconciliation", reconciliation)
        fill_discrepancies = store.order_fill_quantity_discrepancies()
        unmatched = store.unmatched_trade_order_ids()
        proof = child_result(oco_final)
        oco_children_cover_quantity = (
            len(proof["children"]) == 2
            and all(
                Decimal(str(item["origQty"])) == oco_quantity
                for item in proof["children"]
            )
        )
        all_fee_asset_mismatches = buy_fee_asset_mismatches + oco_fee_asset_mismatches
        fifo_complete = bool(pnl.get("complete"))
        open_inventory_quantity = Decimal(
            str(pnl.get("symbols", {}).get(symbol, {}).get("open_quantity", "0"))
        )
        reconciliation_clean = not reconciliation["discrepancies"] and not reconciliation["halted"]
        summary.update({
            "buy_order_id": str(buy_order_id),
            "buy_status": str(buy_order.get("status", "")).upper(),
            "buy_fill_summary": buy_summary,
            "actual_entry_average_price": exit_risk["average_entry_price"],
            "actual_stop_loss_percent": exit_risk["actual_stop_loss_percent"],
            "actual_take_profit_percent": exit_risk["actual_take_profit_percent"],
            "exit_risk_policy_match": exit_risk["exit_risk_policy_match"],
            "bnb_fee_preflight": bnb_fee_preflight,
            "buy_fee_asset_mismatches": buy_fee_asset_mismatches,
            "oco_fee_asset_mismatches": oco_fee_asset_mismatches,
            "all_fee_asset_mismatches": all_fee_asset_mismatches,
            "oco_protected_quantity": str(oco_quantity),
            "net_sellable_quantity": str(sellable_net_quantity),
            "oco_children_cover_quantity": oco_children_cover_quantity,
            "order_list_id": str(oco_final.get("orderListId", oco_response.get("orderListId", ""))),
            "oco_children": proof["children"],
            "mutual_cancel_proven": proof["mutual_cancel_proven"],
            "fifo_complete": fifo_complete,
            "fifo_open_inventory_quantity": str(open_inventory_quantity),
            "unprotected_dust_quantity": str(unprotected_dust),
            "fifo_realized_pnl": pnl,
            "fill_discrepancies": fill_discrepancies,
            "unmatched_trade_orders": unmatched,
            "reconciliation_discrepancies": reconciliation["discrepancies"],
            "reconciliation_clean": reconciliation_clean,
            "status": (
                "TESTNET_CYCLE_VERIFIED"
                if proof["mutual_cancel_proven"] and fifo_complete
                and open_inventory_quantity == 0 and unprotected_dust == 0
                and exit_risk["exit_risk_policy_match"]
                and oco_children_cover_quantity
                and not all_fee_asset_mismatches
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
            "error": safe_error_message(exc, settings),
            "finished_at": utc_now(),
        })
        evidence.record("run_error", {
            "type": type(exc).__name__,
            "message": safe_error_message(exc, settings),
        })
        return 2
    finally:
        evidence.summary(summary)
        await client.close()
        print(json.dumps({
            "status": summary["status"],
            "error": summary.get("error"),
            "run_id": run_id,
            "symbol": symbol,
            "buy_client_order_id": buy_client_id,
            "oco_list_client_order_id": list_client_id,
            "database": str(database),
            "evidence_directory": str(directory),
            "summary_file": str(evidence.summary_path),
            "mainnet_used": False,
            "secrets_written": False,
        }, indent=2))


async def run_opoco_cycle(args: argparse.Namespace) -> int:
    if args.testnet_max_order_usdt is None:
        settings = Settings()
    else:
        testnet_cap = Decimal(args.testnet_max_order_usdt)
        if not testnet_cap.is_finite() or testnet_cap <= 0 or testnet_cap > Decimal("6.00"):
            raise SpotExecutionError("The Testnet-only order-cap override must be between 0 and 6.00 USDT.")
        settings = Settings(MAX_TRADE_ALLOCATION_USD=testnet_cap)
    require_testnet(settings, allow_trade_cap_override=args.testnet_max_order_usdt is not None)
    if args.confirm_testnet_cycle != "TESTNET_ONLY":
        raise SpotExecutionError("Explicit TESTNET_ONLY confirmation is required.")

    symbol = args.symbol.upper()
    if symbol not in settings.watchlist_symbols:
        raise SpotExecutionError("The selected symbol is not in the configured watchlist.")
    quantity = Decimal(args.quantity)
    working_price = Decimal(args.working_price)
    take_profit = Decimal(args.take_profit_price)
    stop_price = Decimal(args.stop_price)
    stop_limit = Decimal(args.stop_limit_price)
    values = (quantity, working_price, take_profit, stop_price, stop_limit)
    if not all(value.is_finite() and value > 0 for value in values):
        raise SpotExecutionError("OPOCO quantity and prices must be finite positive decimals.")
    if (
        args.buy_timeout_seconds < 10 or args.buy_timeout_seconds > 3600
        or args.oco_timeout_seconds < 10 or args.oco_timeout_seconds > 3600
        or args.poll_seconds <= 0 or args.poll_seconds > 60
    ):
        raise SpotExecutionError("OPOCO timeouts or polling interval are outside safe bounds.")

    run_id = make_run_id()
    directory = Path(args.evidence_root).expanduser() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    os.chmod(directory, 0o700)
    database = directory / "testnet-ledger.sqlite3"
    os.chmod(directory, 0o700)
    store = SQLiteOrderStore(str(database))
    client = BinanceSpotTestnetExecution(settings, store)
    list_client_id = f"mumin-opoco-{uuid.uuid4().hex[:10]}"
    working_client_id = f"mumin-work-{uuid.uuid4().hex[:10]}"
    above_client_id = f"mumin-tp-{uuid.uuid4().hex[:10]}"
    below_client_id = f"mumin-sl-{uuid.uuid4().hex[:10]}"
    evidence = Evidence(directory)
    summary: dict[str, Any] = {
        "run_id": run_id,
        "environment": "BINANCE_SPOT_TESTNET",
        "protection_mode": "OPOCO",
        "rest_base_url": TESTNET_REST_BASE_URL,
        "symbol": symbol,
        "testnet_max_order_usdt": str(settings.MAX_TRADE_ALLOCATION_USD),
        "testnet_cap_override_used": args.testnet_max_order_usdt is not None,
        "opoco_list_client_order_id": list_client_id,
        "working_client_order_id": working_client_id,
        "pending_above_client_order_id": above_client_id,
        "pending_below_client_order_id": below_client_id,
        "database": str(database),
        "evidence_directory": str(directory),
        "started_at": utc_now(),
        "status": "RUNNING",
        "mainnet_used": False,
        "secrets_written": False,
    }
    evidence.record("opoco_run_started", {
        "run_id": run_id,
        "symbol": symbol,
        "quantity": quantity,
        "working_price": working_price,
        "take_profit_price": take_profit,
        "stop_price": stop_price,
        "stop_limit_price": stop_limit,
        "rest_base_url": TESTNET_REST_BASE_URL,
    })
    try:
        initial = await client.reconcile()
        evidence.record("initial_reconciliation", initial)
        if initial["discrepancies"] or initial["halted"]:
            raise SpotExecutionError(
                "Initial Testnet reconciliation is not clean. No OPOCO was submitted; inspect evidence."
            )
        if not initial["realized_pnl"]["complete"]:
            raise SpotExecutionError("Initial FIFO/fee ledger is incomplete; no OPOCO was submitted.")

        ticker = await client._ticker_price(symbol)
        symbol_info = await client._exchange_symbol(symbol)
        capability = opoco_capability(symbol_info)
        base_asset = str(symbol_info.get("baseAsset", "")).upper()
        quote_asset = str(symbol_info.get("quoteAsset", "")).upper()
        if quote_asset != settings.QUOTE_ASSET.upper():
            raise SpotExecutionError("OPOCO lifecycle runner only permits the configured USDT quote asset.")
        if not capability["supported_by_exchange_info"]:
            raise SpotExecutionError(
                "Exchange Information does not enable OPOCO for this symbol; no order was submitted."
            )
        working_notional = quantity * working_price
        if (
            working_notional < settings.MIN_TRADE_ALLOCATION_USD
            or working_notional > settings.MAX_TRADE_ALLOCATION_USD
        ):
            raise SpotExecutionError("OPOCO working BUY notional is outside configured bounds.")

        params, validated_metadata = await client.opoco._validate_request(
            symbol=symbol,
            quantity=quantity,
            working_price=working_price,
            take_profit_price=take_profit,
            stop_price=stop_price,
            stop_limit_price=stop_limit,
            list_client_order_id=list_client_id,
            working_client_order_id=working_client_id,
            pending_above_client_order_id=above_client_id,
            pending_below_client_order_id=below_client_id,
        )
        fee_preflight = await client._bnb_fee_readiness(
            symbol=symbol,
            buy_notional=working_notional,
            sell_notional=quantity * max(take_profit, stop_limit),
        )
        evidence.record("opoco_pre_submit_validation", {
            "exchange_capability": capability,
            "working_order_template": params,
            "symbol_info": validated_metadata,
            "last_price": ticker,
            "bnb_fee_preflight": fee_preflight,
        })

        initial_response = await client.opoco.place_buy_opoco(
            symbol=symbol,
            quantity=quantity,
            working_price=working_price,
            take_profit_price=take_profit,
            stop_price=stop_price,
            stop_limit_price=stop_limit,
            list_client_order_id=list_client_id,
            working_client_order_id=working_client_id,
            pending_above_client_order_id=above_client_id,
            pending_below_client_order_id=below_client_id,
        )
        evidence.record("opoco_submission_response", initial_response)

        observed = await watch_opoco(
            client,
            symbol=symbol,
            list_client_order_id=list_client_id,
            timeout_seconds=args.buy_timeout_seconds,
            poll_seconds=args.poll_seconds,
            evidence=evidence,
        )
        state = client.opoco._state(observed)
        proof = opoco_result(observed)
        working_report = proof["working_order"]
        working_status = str((working_report or {}).get("status", "")).upper()

        if state == "WORKING":
            observed = await client.opoco.cancel_order_list(
                symbol=symbol,
                list_client_order_id=list_client_id,
            )
            evidence.record("opoco_working_order_cancel_after_timeout", observed)
            state = client.opoco._state(observed)
            proof = opoco_result(observed)
            working_report = proof["working_order"]
            working_status = str((working_report or {}).get("status", "")).upper()
        elif state == "PARTIAL_FILL_REVIEW" and working_status != "FILLED":
            observed = await client.opoco.cancel_order_list(
                symbol=symbol,
                list_client_order_id=list_client_id,
            )
            evidence.record("opoco_partial_working_order_cancel", observed)
            state = client.opoco._state(observed)
            proof = opoco_result(observed)
            working_report = proof["working_order"]
            working_status = str((working_report or {}).get("status", "")).upper()

        if state == "PROTECTION_ARMED":
            observed = await watch_opoco(
                client,
                symbol=symbol,
                list_client_order_id=list_client_id,
                timeout_seconds=args.oco_timeout_seconds,
                poll_seconds=args.poll_seconds,
                evidence=evidence,
            )
            state = client.opoco._state(observed)
            proof = opoco_result(observed)
        evidence.record("opoco_final_exchange_state", observed)

        await client.sync_trade_history()
        proof = opoco_result(observed)
        working_report = proof["working_order"]
        protective_children = proof["protective_children"]
        working_order_id = (
            int(working_report["orderId"])
            if working_report and str(working_report.get("orderId", "")).isdigit()
            else None
        )
        buy_summary = (
            store.order_fill_summary(symbol, working_order_id, base_asset=base_asset)
            if working_order_id is not None else None
        )
        gross_buy_quantity = (
            Decimal(buy_summary["gross_quantity"])
            if buy_summary is not None else Decimal("0")
        )
        average_entry_price = (
            Decimal(buy_summary["quote_quantity"]) / gross_buy_quantity
            if gross_buy_quantity > 0 else Decimal("0")
        )
        exit_risk = opoco_exit_risk_metrics(
            average_entry_price, protective_children,
        )
        buy_fills = (
            store.trade_fills_for_order(symbol, working_order_id)
            if working_order_id is not None else []
        )
        buy_fee_mismatches = commission_asset_mismatches(buy_fills, settings.BNB_FEE_ASSET)
        child_fills: dict[str, list[dict[str, Any]]] = {}
        child_fee_mismatches: list[dict[str, str]] = []
        for child in protective_children:
            child_id = str(child.get("orderId", ""))
            if child_id.isdigit():
                fills = store.trade_fills_for_order(symbol, int(child_id))
                child_fills[child_id] = fills
                child_fee_mismatches.extend(
                    commission_asset_mismatches(fills, settings.BNB_FEE_ASSET)
                )
        pnl = store.realized_pnl_report(settings.QUOTE_ASSET)
        reconciliation = await client.reconcile()
        fill_discrepancies = store.order_fill_quantity_discrepancies()
        unmatched = store.unmatched_trade_order_ids()
        fifo_open = Decimal(
            str(pnl.get("symbols", {}).get(symbol, {}).get("open_quantity", "0"))
        )
        net_quantity = (
            Decimal(buy_summary["net_base_quantity"])
            if buy_summary is not None else Decimal("0")
        )
        filters = {
            item.get("filterType"): item
            for item in symbol_info.get("filters", [])
            if isinstance(item, dict)
        }
        lot = filters.get("LOT_SIZE", {})
        step = Decimal(str(lot.get("stepSize", "0")))
        sellable_net = round_down_to_step(net_quantity, step) if net_quantity > 0 else Decimal("0")
        child_quantities = [
            Decimal(str(child.get("origQty", "0")))
            for child in protective_children
        ]
        protected_quantity = min(child_quantities) if len(child_quantities) == 2 else Decimal("0")
        unprotected_dust = net_quantity - protected_quantity
        children_cover = (
            len(child_quantities) == 2
            and child_quantities[0] == child_quantities[1] == sellable_net
            and protected_quantity <= net_quantity
        )
        all_fee_mismatches = buy_fee_mismatches + child_fee_mismatches
        reconciliation_clean = (
            not reconciliation["discrepancies"] and not reconciliation["halted"]
        )
        verified = (
            proof["mutual_cancel_proven"]
            and bool(pnl.get("complete"))
            and fifo_open == 0
            and unprotected_dust == net_quantity - sellable_net
            and unprotected_dust == 0
            and children_cover
            and exit_risk["exit_risk_policy_match"]
            and not all_fee_mismatches
            and not fill_discrepancies
            and not unmatched
            and reconciliation_clean
        )
        summary.update({
            "opoco_state": state,
            "order_list_id": proof["orderListId"],
            "working_order_id": str(working_order_id or ""),
            "working_status": working_status,
            "working_fill_summary": buy_summary,
            "actual_entry_average_price": exit_risk["average_entry_price"],
            "actual_stop_loss_percent": exit_risk["actual_stop_loss_percent"],
            "actual_take_profit_percent": exit_risk["actual_take_profit_percent"],
            "exit_risk_policy_match": exit_risk["exit_risk_policy_match"],
            "working_fee_asset_mismatches": buy_fee_mismatches,
            "opoco_children": proof["children"],
            "protective_child_fills": child_fills,
            "protective_fee_asset_mismatches": child_fee_mismatches,
            "mutual_cancel_proven": proof["mutual_cancel_proven"],
            "net_base_quantity": str(net_quantity),
            "protected_quantity": str(protected_quantity),
            "sellable_net_quantity": str(sellable_net),
            "unprotected_dust_quantity": str(unprotected_dust),
            "opoco_children_cover_quantity": children_cover,
            "fifo_complete": bool(pnl.get("complete")),
            "fifo_open_inventory_quantity": str(fifo_open),
            "fifo_realized_pnl": pnl,
            "fill_discrepancies": fill_discrepancies,
            "unmatched_trade_orders": unmatched,
            "reconciliation_discrepancies": reconciliation["discrepancies"],
            "reconciliation_clean": reconciliation_clean,
            "all_fee_asset_mismatches": all_fee_mismatches,
            "status": "TESTNET_OPOCO_CYCLE_VERIFIED" if verified else "TESTNET_OPOCO_CYCLE_INCOMPLETE",
            "finished_at": utc_now(),
        })
        evidence.record("opoco_fifo_realized_pnl_report", pnl)
        evidence.record("opoco_final_reconciliation", reconciliation)
        return 0 if verified else 2
    except Exception as exc:
        store.halt("TESTNET_OPOCO_LIFECYCLE_INCOMPLETE")
        summary.update({
            "status": "TESTNET_OPOCO_LIFECYCLE_INCOMPLETE",
            "error_type": type(exc).__name__,
            "error": safe_error_message(exc, settings),
            "finished_at": utc_now(),
        })
        evidence.record("opoco_run_error", {
            "type": type(exc).__name__,
            "message": safe_error_message(exc, settings),
        })
        return 2
    finally:
        evidence.summary(summary)
        await client.close()
        print(json.dumps({
            "status": summary["status"],
            "error": summary.get("error"),
            "run_id": run_id,
            "symbol": symbol,
            "opoco_list_client_order_id": list_client_id,
            "working_client_order_id": working_client_id,
            "database": str(database),
            "evidence_directory": str(directory),
            "summary_file": str(evidence.summary_path),
            "mainnet_used": False,
            "secrets_written": False,
        }, indent=2))


async def inspect_market(args: argparse.Namespace) -> int:
    settings = Settings()
    require_testnet(settings)
    symbol = args.symbol.upper()
    if symbol not in settings.watchlist_symbols:
        raise SpotExecutionError("The selected symbol is not in the configured watchlist.")
    store = SQLiteOrderStore(args.database)
    client = BinanceSpotTestnetExecution(settings, store)
    try:
        ticker = await client._ticker_price(symbol)
        metadata = await client._exchange_symbol(symbol)
        filters = {item.get("filterType"): item for item in metadata.get("filters", [])}
        fee_readiness = None
        if args.fee_buy_notional_usdt is not None or args.fee_sell_notional_usdt is not None:
            if args.fee_buy_notional_usdt is None or args.fee_sell_notional_usdt is None:
                raise SpotExecutionError(
                    "Both --fee-buy-notional-usdt and --fee-sell-notional-usdt are required together."
                )
            fee_readiness = await client._bnb_fee_readiness(
                symbol=symbol,
                buy_notional=Decimal(args.fee_buy_notional_usdt),
                sell_notional=Decimal(args.fee_sell_notional_usdt),
            )
        result = {
            "environment": "BINANCE_SPOT_TESTNET",
            "rest_base_url": TESTNET_REST_BASE_URL,
            "symbol": symbol,
            "last_price": str(ticker),
            "base_asset": metadata.get("baseAsset"),
            "quote_asset": metadata.get("quoteAsset"),
            "status": metadata.get("status"),
            "is_spot_trading_allowed": metadata.get("isSpotTradingAllowed"),
            "opoco_capability": opoco_capability(metadata),
            "price_filter": filters.get("PRICE_FILTER"),
            "lot_size_filter": filters.get("LOT_SIZE"),
            "notional_filter": filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL"),
            "configured_min_trade_usdt": str(settings.MIN_TRADE_ALLOCATION_USD),
            "configured_max_trade_usdt": str(settings.MAX_TRADE_ALLOCATION_USD),
            "bnb_fee_readiness": fee_readiness,
            "matching_engine_order_sent": False,
            "mainnet_used": False,
            "secrets_written": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    finally:
        await client.close()


async def watch_existing(args: argparse.Namespace) -> int:
    settings = Settings()
    require_testnet(settings)
    directory = Path(args.evidence_dir).expanduser()
    evidence = Evidence(directory)
    evidence.summary_path = directory / "watch-summary.json"
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
            "fifo_open_inventory_quantity": str(
                Decimal(str(pnl.get("symbols", {}).get(args.symbol.upper(), {}).get("open_quantity", "0")))
            ),
            "fifo_realized_pnl": pnl,
            "fill_discrepancies": store.order_fill_quantity_discrepancies(),
            "unmatched_trade_orders": store.unmatched_trade_order_ids(),
            "reconciliation_discrepancies": reconciliation["discrepancies"],
            "reconciliation_clean": not reconciliation["discrepancies"] and not reconciliation["halted"],
            "status": "TESTNET_CYCLE_VERIFIED" if (
                proof["mutual_cancel_proven"] and pnl.get("complete")
                and Decimal(str(pnl.get("symbols", {}).get(args.symbol.upper(), {}).get("open_quantity", "0"))) == 0
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


async def watch_opoco_existing(args: argparse.Namespace) -> int:
    settings = Settings()
    require_testnet(settings)
    directory = Path(args.evidence_dir).expanduser()
    evidence = Evidence(directory)
    evidence.summary_path = directory / "watch-opoco-summary.json"
    store = SQLiteOrderStore(args.database)
    client = BinanceSpotTestnetExecution(settings, store)
    try:
        result = await watch_opoco(
            client,
            symbol=args.symbol.upper(),
            list_client_order_id=args.list_client_order_id,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            evidence=evidence,
        )
        await client.sync_trade_history()
        pnl = store.realized_pnl_report(settings.QUOTE_ASSET)
        reconciliation = await client.reconcile()
        proof = opoco_result(result)
        symbol_info = await client._exchange_symbol(args.symbol.upper())
        base_asset = str(symbol_info.get("baseAsset", "")).upper()
        working_report = proof["working_order"]
        working_order_id = (
            int(working_report["orderId"])
            if working_report and str(working_report.get("orderId", "")).isdigit()
            else None
        )
        buy_summary = (
            store.order_fill_summary(
                args.symbol.upper(), working_order_id, base_asset=base_asset
            )
            if working_order_id is not None else None
        )
        gross_buy_quantity = (
            Decimal(buy_summary["gross_quantity"])
            if buy_summary is not None else Decimal("0")
        )
        average_entry_price = (
            Decimal(buy_summary["quote_quantity"]) / gross_buy_quantity
            if gross_buy_quantity > 0 else Decimal("0")
        )
        exit_risk = opoco_exit_risk_metrics(
            average_entry_price, proof["protective_children"],
        )
        net_quantity = (
            Decimal(buy_summary["net_base_quantity"])
            if buy_summary is not None else Decimal("0")
        )
        filters = {
            item.get("filterType"): item
            for item in symbol_info.get("filters", [])
            if isinstance(item, dict)
        }
        step = Decimal(str(filters.get("LOT_SIZE", {}).get("stepSize", "0")))
        sellable_net = round_down_to_step(net_quantity, step) if net_quantity > 0 else Decimal("0")
        child_quantities = [
            Decimal(str(child.get("origQty", "0")))
            for child in proof["protective_children"]
        ]
        protected_quantity = min(child_quantities) if len(child_quantities) == 2 else Decimal("0")
        unprotected_dust = net_quantity - protected_quantity
        children_cover = (
            len(child_quantities) == 2
            and child_quantities[0] == child_quantities[1] == sellable_net
            and protected_quantity <= net_quantity
        )
        fifo_open = Decimal(
            str(pnl.get("symbols", {}).get(args.symbol.upper(), {}).get("open_quantity", "0"))
        )
        fee_mismatches = []
        for child in proof["children"]:
            child_id = str(child.get("orderId", ""))
            if child_id.isdigit():
                fee_mismatches.extend(
                    commission_asset_mismatches(
                        store.trade_fills_for_order(args.symbol.upper(), int(child_id)),
                        settings.BNB_FEE_ASSET,
                    )
                )
        fill_discrepancies = store.order_fill_quantity_discrepancies()
        unmatched = store.unmatched_trade_order_ids()
        reconciliation_clean = (
            not reconciliation["discrepancies"] and not reconciliation["halted"]
        )
        verified = (
            proof["mutual_cancel_proven"]
            and bool(pnl.get("complete"))
            and fifo_open == 0
            and unprotected_dust == 0
            and children_cover
            and exit_risk["exit_risk_policy_match"]
            and not fee_mismatches
            and not fill_discrepancies
            and not unmatched
            and reconciliation_clean
        )
        summary = {
            "environment": "BINANCE_SPOT_TESTNET",
            "protection_mode": "OPOCO",
            "symbol": args.symbol.upper(),
            "order_list_id": proof["orderListId"],
            "opoco_list_client_order_id": args.list_client_order_id,
            "opoco_state": client.opoco._state(result),
            "opoco_children": proof["children"],
            "mutual_cancel_proven": proof["mutual_cancel_proven"],
            "working_order_id": str(working_order_id or ""),
            "working_fill_summary": buy_summary,
            "actual_entry_average_price": exit_risk["average_entry_price"],
            "actual_stop_loss_percent": exit_risk["actual_stop_loss_percent"],
            "actual_take_profit_percent": exit_risk["actual_take_profit_percent"],
            "exit_risk_policy_match": exit_risk["exit_risk_policy_match"],
            "net_base_quantity": str(net_quantity),
            "protected_quantity": str(protected_quantity),
            "sellable_net_quantity": str(sellable_net),
            "unprotected_dust_quantity": str(unprotected_dust),
            "opoco_children_cover_quantity": children_cover,
            "fifo_complete": bool(pnl.get("complete")),
            "fifo_open_inventory_quantity": str(fifo_open),
            "fifo_realized_pnl": pnl,
            "fee_asset_mismatches": fee_mismatches,
            "fill_discrepancies": fill_discrepancies,
            "unmatched_trade_orders": unmatched,
            "reconciliation_discrepancies": reconciliation["discrepancies"],
            "reconciliation_clean": reconciliation_clean,
            "status": "TESTNET_OPOCO_CYCLE_VERIFIED" if verified else "TESTNET_OPOCO_CYCLE_INCOMPLETE",
            "finished_at": utc_now(),
        }
        evidence.summary(summary)
        print(json.dumps({
            "status": summary["status"],
            "order_list_id": summary["order_list_id"],
            "opoco_state": summary["opoco_state"],
            "mutual_cancel_proven": summary["mutual_cancel_proven"],
            "evidence_directory": str(directory),
            "summary_file": str(evidence.summary_path),
            "mainnet_used": False,
            "secrets_written": False,
        }, indent=2))
        return 0 if verified else 2
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
    run.add_argument(
        "--testnet-max-order-usdt",
        help="Optional one-run Testnet-only cap override, hard-limited to 6.00 USDT; does not change .env or Paper limits.",
    )
    run.add_argument("--evidence-root", default="artifacts/binance-spot-testnet-evidence")
    run.add_argument("--confirm-testnet-cycle", required=True, choices=("TESTNET_ONLY",))

    opoco = commands.add_parser(
        "run-opoco",
        help="Run one explicitly confirmed Spot Testnet OPOCO lifecycle.",
    )
    opoco.add_argument("--symbol", default="BTCUSDT")
    opoco.add_argument("--quantity", required=True)
    opoco.add_argument("--working-price", required=True)
    opoco.add_argument("--take-profit-price", required=True)
    opoco.add_argument("--stop-price", required=True)
    opoco.add_argument("--stop-limit-price", required=True)
    opoco.add_argument("--buy-timeout-seconds", type=int, default=120)
    opoco.add_argument("--oco-timeout-seconds", type=int, default=900)
    opoco.add_argument("--poll-seconds", type=float, default=2)
    opoco.add_argument(
        "--testnet-max-order-usdt",
        help="Optional one-run Testnet-only cap override, hard-limited to 6.00 USDT.",
    )
    opoco.add_argument("--evidence-root", default="artifacts/binance-spot-testnet-evidence")
    opoco.add_argument("--confirm-testnet-cycle", required=True, choices=("TESTNET_ONLY",))

    inspect = commands.add_parser("inspect", help="Read the current Testnet price and filters; sends no orders.")
    inspect.add_argument("--symbol", default="BTCUSDT")
    inspect.add_argument("--database", default="data/testnet-inspect.sqlite3")
    inspect.add_argument(
        "--fee-buy-notional-usdt",
        help="Optional read-only BNB fee preflight BUY notional; must be paired with --fee-sell-notional-usdt.",
    )
    inspect.add_argument(
        "--fee-sell-notional-usdt",
        help="Optional read-only BNB fee preflight worst OCO exit notional; sends no orders.",
    )

    watch = commands.add_parser("watch", help="Continue monitoring a previously placed OCO; sends no orders.")
    watch.add_argument("--symbol", required=True)
    watch.add_argument("--list-client-order-id", required=True)
    watch.add_argument("--database", required=True)
    watch.add_argument("--evidence-dir", required=True)
    watch.add_argument("--timeout-seconds", type=int, default=1800)
    watch.add_argument("--poll-seconds", type=float, default=3)
    watch_opoco = commands.add_parser(
        "watch-opoco",
        help="Continue monitoring a previously submitted OPOCO list; sends no new orders.",
    )
    watch_opoco.add_argument("--symbol", required=True)
    watch_opoco.add_argument("--list-client-order-id", required=True)
    watch_opoco.add_argument("--database", required=True)
    watch_opoco.add_argument("--evidence-dir", required=True)
    watch_opoco.add_argument("--timeout-seconds", type=int, default=1800)
    watch_opoco.add_argument("--poll-seconds", type=float, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "run":
            code = asyncio.run(run_cycle(args))
        elif args.command == "run-opoco":
            code = asyncio.run(run_opoco_cycle(args))
        elif args.command == "inspect":
            code = asyncio.run(inspect_market(args))
        elif args.command == "watch-opoco":
            code = asyncio.run(watch_opoco_existing(args))
        else:
            code = asyncio.run(watch_existing(args))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
