from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from export_paper_report import load_snapshot


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a read-only Paper state JSON snapshot.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_snapshot(args.db)
    payload = {
        "schema_version": 1,
        "mode": "PAPER_ONLY",
        "initial_capital_usdt": "70.00",
        "risk": {
            "stop_loss_percent": "0.50",
            "take_profit_percent": "1.25",
            "max_trade_allocation_usdt": "5.00",
            "max_spread_bps": "12.00",
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
