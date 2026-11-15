from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from config.config import Settings
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_testnet_execution import BinanceSpotTestnetExecution
from mumin_trader.exchanges.binance.testnet_order_preflight import BinanceSpotTestnetOrderPreflight


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit operator CLI for isolated Binance Spot Testnet. Never connects to Mainnet."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    reconcile = commands.add_parser("reconcile", help="Read account, open orders and trade history; send no order.")
    reconcile.add_argument("--database", help="Optional dedicated SQLite order-ledger path.")

    preflight = commands.add_parser("preflight", help="Validate and sign a non-executing /order/test request.")
    preflight.add_argument("--symbol", required=True)
    preflight.add_argument("--side", required=True, choices=("BUY", "SELL"))
    preflight.add_argument("--quantity", required=True)
    preflight.add_argument("--price", required=True)
    preflight.add_argument("--client-order-id", required=True)

    submit = commands.add_parser("submit", help="Submit one explicitly confirmed Spot Testnet LIMIT order.")
    submit.add_argument("--symbol", required=True)
    submit.add_argument("--side", required=True, choices=("BUY", "SELL"))
    submit.add_argument("--quantity", required=True)
    submit.add_argument("--price", required=True)
    submit.add_argument("--client-order-id", required=True)
    submit.add_argument("--confirm-testnet-order", required=True, choices=("TESTNET_ONLY",))

    cancel = commands.add_parser("cancel", help="Cancel one locally recorded Spot Testnet order.")
    cancel.add_argument("--symbol", required=True)
    cancel.add_argument("--client-order-id", required=True)
    cancel.add_argument("--confirm-testnet-cancel", required=True, choices=("TESTNET_ONLY",))

    stop = commands.add_parser("emergency-stop", help="Persistently halt execution and cancel exchange open orders.")
    stop.add_argument("--reason", default="MANUAL_EMERGENCY_STOP")
    stop.add_argument("--database", help="Optional dedicated SQLite order-ledger path.")
    return parser


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    database = getattr(args, "database", None) or settings.DATABASE_URL

    if args.command == "preflight":
        validator = BinanceSpotTestnetOrderPreflight(settings)
        try:
            result = await validator.preflight_limit_order(
                symbol=args.symbol,
                side=args.side,
                quantity=args.quantity,
                price=args.price,
                client_order_id=args.client_order_id,
            )
            emit({"preflight": "passed", "matching_engine_order_sent": False, "response": result})
            return 0
        finally:
            await validator.close()

    store = SQLiteOrderStore(database)
    client = BinanceSpotTestnetExecution(settings, store)
    try:
        if args.command == "reconcile":
            emit(await client.reconcile())
            return 0

        if args.command == "submit":
            reconciliation = await client.reconcile()
            if reconciliation["discrepancies"] or reconciliation["halted"]:
                raise RuntimeError(
                    "Pre-submit reconciliation did not produce a clean, unhalted account; no order was sent."
                )
            result = await client.submit_limit_order(
                symbol=args.symbol,
                side=args.side,
                quantity=args.quantity,
                price=args.price,
                client_order_id=args.client_order_id,
            )
            emit({"testnet_order": result, "mainnet_used": False})
            return 0

        if args.command == "cancel":
            result = await client.cancel_order(
                symbol=args.symbol,
                client_order_id=args.client_order_id,
            )
            emit({"testnet_cancel": result, "mainnet_used": False})
            return 0

        if args.command == "emergency-stop":
            emit(await client.emergency_stop(args.reason))
            return 0

        raise RuntimeError("Unsupported operator command.")
    finally:
        await client.close()


def main() -> None:
    args = build_parser().parse_args()
    try:
        exit_code = asyncio.run(run(args))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
