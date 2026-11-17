from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger

EXPECTED_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
}


def _age_seconds(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())


def inspect_node(
    database: str | Path,
    *,
    now: datetime | None = None,
    max_heartbeat_age_seconds: float = 120.0,
    max_market_age_seconds: float = 180.0,
    require_running: bool = True,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    current = current.astimezone(timezone.utc)
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(path)

    checks: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append({"name": "sqlite_integrity", "status": "PASS" if integrity == "ok" else "FAIL", "detail": integrity})

        account = db.execute(
            "SELECT initial_cash, tick_count, halted, halt_reason FROM bot_account WHERE id=1"
        ).fetchone()
        if account is None:
            raise RuntimeError("Paper account row is missing.")
        capital_ok = str(account["initial_cash"]) == "70.00"
        checks.append({
            "name": "paper_capital_lock",
            "status": "PASS" if capital_ok else "FAIL",
            "detail": {"initial_cash_usdt": account["initial_cash"], "expected_usdt": "70.00"},
        })

        runtime = db.execute(
            """SELECT desired_state, supervisor_heartbeat_at, supervisor_instance_id,
                      worker_pid, heartbeat_at, worker_instance_id
               FROM bot_runtime_control WHERE id=1"""
        ).fetchone()
        if runtime is None:
            raise RuntimeError("Paper runtime control row is missing.")
        heartbeat_age = _age_seconds(runtime["supervisor_heartbeat_at"], current)
        heartbeat_ok = heartbeat_age is not None and heartbeat_age <= max_heartbeat_age_seconds
        checks.append({
            "name": "supervisor_heartbeat",
            "status": "PASS" if heartbeat_ok else "FAIL",
            "detail": {
                "age_seconds": heartbeat_age,
                "max_age_seconds": max_heartbeat_age_seconds,
                "instance": runtime["supervisor_instance_id"],
                "worker_pid": runtime["worker_pid"],
            },
        })

        desired_ok = runtime["desired_state"] == "RUNNING" if require_running else runtime["desired_state"] in {"RUNNING", "PAUSED"}
        checks.append({
            "name": "desired_state",
            "status": "PASS" if desired_ok else "FAIL",
            "detail": runtime["desired_state"],
        })

        latest = db.execute(
            "SELECT MAX(tick) AS tick, MAX(received_at_ms) AS received_at_ms FROM bot_market_ticks WHERE data_source='BINANCE_PUBLIC_MARKET_DATA'"
        ).fetchone()
        latest_tick = latest["tick"] if latest else None
        received_ms = latest["received_at_ms"] if latest else None
        symbols = set()
        if latest_tick is not None:
            symbols = {
                row[0] for row in db.execute(
                    """SELECT symbol FROM bot_market_ticks
                       WHERE tick=? AND data_source='BINANCE_PUBLIC_MARKET_DATA'""",
                    (latest_tick,),
                ).fetchall()
            }
        market_age = (
            max(0.0, current.timestamp() - (int(received_ms) / 1000.0))
            if received_ms is not None else None
        )
        market_ok = market_age is not None and market_age <= max_market_age_seconds
        complete_cycle = symbols == EXPECTED_SYMBOLS
        checks.append({
            "name": "public_market_feed",
            "status": "PASS" if market_ok and complete_cycle else "FAIL",
            "detail": {
                "latest_tick": latest_tick,
                "age_seconds": market_age,
                "max_age_seconds": max_market_age_seconds,
                "symbols_present": sorted(symbols),
                "expected_symbol_count": len(EXPECTED_SYMBOLS),
                "complete_eight_symbol_cycle": complete_cycle,
            },
        })

        accounting = reconcile_paper_ledger(db)
        checks.append({
            "name": "paper_accounting_reconciliation",
            "status": accounting.get("status", "FAIL"),
            "detail": accounting,
        })
        halted = bool(account["halted"])
        checks.append({
            "name": "risk_halt",
            "status": "WARN" if halted else "PASS",
            "detail": {"halted": halted, "reason": account["halt_reason"]},
        })

    statuses = [item["status"] for item in checks]
    overall = "FAIL" if "FAIL" in statuses else ("WARN" if "WARN" in statuses else "PASS")
    return {
        "schema_version": 1,
        "checked_at_utc": current.isoformat(),
        "database": str(path),
        "overall": overall,
        "checks": checks,
        "execution_mode": "PAPER_ONLY",
        "mainnet_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Mumin Paper node health and ledger monitor.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--max-heartbeat-age-seconds", type=float, default=120.0)
    parser.add_argument("--max-market-age-seconds", type=float, default=180.0)
    parser.add_argument("--allow-paused", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.max_heartbeat_age_seconds <= 0 or args.max_market_age_seconds <= 0:
        parser.error("Age thresholds must be positive.")
    result = inspect_node(
        args.db,
        max_heartbeat_age_seconds=args.max_heartbeat_age_seconds,
        max_market_age_seconds=args.max_market_age_seconds,
        require_running=not args.allow_paused,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if result["overall"] == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
