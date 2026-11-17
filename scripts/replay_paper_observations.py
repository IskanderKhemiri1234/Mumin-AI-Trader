from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger
from scripts.audit_paper_ledger import build_audit
from scripts.run_continuous_paper import PUBLIC_SOURCE, SYMBOLS, ContinuousPaperBot


def _load_archive(archive_path: Path, manifest_path: Path, require_ready: bool) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("archive_sha256") != digest:
        raise ValueError("Archive SHA-256 does not match its manifest.")
    if require_ready and not manifest.get("quality", {}).get("replay_ready", False):
        raise ValueError("Dataset is not marked replay_ready; refusing a formal replay.")
    records = []
    with gzip.open(archive_path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}.") from exc
            if item.get("record_type") == "market_snapshot":
                row = item.get("record")
                if row and row.get("data_source") == PUBLIC_SOURCE:
                    records.append(row)
    if not records:
        raise ValueError("Archive contains no public market snapshots.")
    return records, manifest, digest


def _prepare_cycles(records: list[dict[str, Any]]) -> list[tuple[int, int, dict[str, dict[str, Any]]]]:
    grouped: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in records:
        tick = int(row["tick"])
        symbol = str(row["symbol"]).upper()
        if symbol in grouped[tick]:
            raise ValueError(f"Duplicate snapshot for {symbol} at source tick {tick}.")
        grouped[tick][symbol] = row

    cycles = []
    previous_close: dict[str, int] = {}
    for tick in sorted(grouped):
        symbols = grouped[tick]
        if set(symbols) != set(SYMBOLS):
            missing = sorted(set(SYMBOLS) - set(symbols))
            extra = sorted(set(symbols) - set(SYMBOLS))
            raise ValueError(f"Incomplete eight-symbol cycle {tick}; missing={missing}, extra={extra}.")
        received_values = [int(row["received_at_ms"]) for row in symbols.values()]
        received_at = max(received_values)
        for symbol, row in symbols.items():
            candle_close = int(row["candle_close_time_ms"])
            if candle_close >= received_at or received_at - candle_close > 180_000:
                raise ValueError(f"Non-causal or stale one-minute candle for {symbol} at tick {tick}.")
            prior = previous_close.get(symbol)
            if prior is not None and candle_close <= prior:
                raise ValueError(f"Non-increasing candle close time for {symbol} at tick {tick}.")
            previous_close[symbol] = candle_close

            closes = json.loads(row.get("macro_hourly_closes_json") or "[]")
            close_times = json.loads(row.get("macro_hourly_close_times_json") or "[]")
            if len(closes) != 21 or len(close_times) != 21:
                raise ValueError(f"Missing raw hourly macro input for {symbol} at tick {tick}.")
            if any(int(left) >= int(right) for left, right in zip(close_times, close_times[1:])):
                raise ValueError(f"Hourly macro timestamps are not strictly increasing for {symbol} at tick {tick}.")
            if any(int(value) >= received_at for value in close_times):
                raise ValueError(f"Hourly macro look-ahead detected for {symbol} at tick {tick}.")
            bid, ask = Decimal(row["bid"]), Decimal(row["ask"])
            if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid:
                raise ValueError(f"Invalid bid/ask for {symbol} at tick {tick}.")
        cycles.append((tick, received_at, symbols))
    return cycles


def replay_archive(
    archive_path: str | Path,
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    require_ready: bool = True,
) -> dict[str, Any]:
    archive = Path(archive_path)
    manifest_file = Path(manifest_path)
    records, manifest, digest = _load_archive(archive, manifest_file, require_ready)
    cycles = _prepare_cycles(records)
    if require_ready and len(cycles) < 30:
        raise ValueError("At least 30 complete cycles are required for formal replay.")

    with tempfile.TemporaryDirectory(prefix="mumin-paper-replay-") as temporary:
        database = Path(temporary) / "replay.sqlite3"
        bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
        for _, received_at, symbols in cycles:
            prices = {}
            quotes = {}
            candle_times = {}
            features = {}
            for symbol, row in symbols.items():
                prices[symbol] = Decimal(row["price"])
                quotes[symbol] = (Decimal(row["bid"]), Decimal(row["ask"]))
                candle_times[symbol] = int(row["candle_close_time_ms"])
                features[symbol] = {
                    "open": Decimal(row["candle_open"]),
                    "high": Decimal(row["candle_high"]),
                    "low": Decimal(row["candle_low"]),
                    "volume": Decimal(row["candle_volume"]),
                    "bid_depth_qty": Decimal(row["bid_depth_qty"]),
                    "ask_depth_qty": Decimal(row["ask_depth_qty"]),
                    "macro_hourly_closes": [Decimal(value) for value in json.loads(row["macro_hourly_closes_json"])],
                    "macro_hourly_close_times_ms": [int(value) for value in json.loads(row["macro_hourly_close_times_json"])],
                }
            event_time = datetime.fromtimestamp(received_at / 1000, tz=timezone.utc)
            bot.step(
                prices,
                source=PUBLIC_SOURCE,
                quotes=quotes,
                candle_close_times=candle_times,
                received_at_ms=received_at,
                market_features=features,
                event_time=event_time,
            )

        bot.finalize_database_batch()
        report = bot.report()
        connection = bot._connect()
        try:
            reconciliation = reconcile_paper_ledger(connection)
            equity_rows = connection.execute(
                "SELECT equity FROM bot_equity ORDER BY tick"
            ).fetchall()
        finally:
            connection.close()
        accounting = build_audit(database)
        peak = Decimal("70.00")
        max_drawdown = Decimal("0")
        for row in equity_rows:
            equity = Decimal(row["equity"])
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak * Decimal("100"))
        gross_wins = sum(
            (Decimal(item["net_pnl_usdt"]) for item in accounting["round_trips"]
             if Decimal(item["net_pnl_usdt"]) > 0), Decimal("0")
        )
        gross_losses = -sum(
            (Decimal(item["net_pnl_usdt"]) for item in accounting["round_trips"]
             if Decimal(item["net_pnl_usdt"]) < 0), Decimal("0")
        )
        metrics = {
            "cycles_replayed": len(cycles),
            "first_received_at_ms": cycles[0][1],
            "last_received_at_ms": cycles[-1][1],
            "initial_capital_usdt": "70.00",
            "final_equity_usdt": str(report["equity"]),
            "realized_net_pnl_usdt": str(report["realized_pnl"]),
            "return_percent": str((report["equity"] / Decimal("70.00") - 1) * 100),
            "fees_usdt": str(report["fees"]),
            "closed_trades": report["trades"],
            "wins": report["wins"],
            "losses": report["losses"],
            "win_rate_percent": str(
                Decimal(report["wins"]) / Decimal(report["trades"]) * 100
                if report["trades"] else Decimal("0")
            ),
            "profit_factor_net": str(gross_wins / gross_losses) if gross_losses else None,
            "max_drawdown_percent": str(max_drawdown),
            "accounting_status": accounting["accounting_status"],
            "per_symbol": accounting["per_symbol"],
        }
        if reconciliation.get("status") != "PASS" or accounting["accounting_status"] != "PASS":
            raise RuntimeError("Replay ledger reconciliation failed; refusing to publish metrics.")

    result = {
        "schema_version": 1,
        "evaluation": "FORMAL_REPLAY" if require_ready else "INCOMPLETE_DIAGNOSTIC_ONLY",
        "strategy": "FROZEN_PUBLIC_PAPER_MICRO_ALPHA",
        "source": PUBLIC_SOURCE,
        "source_archive_sha256": digest,
        "source_manifest": manifest,
        "metrics": metrics,
        "limitations": [
            "Replay uses only captured snapshots and does not fill unobserved intervals.",
            "Simulated fees, slippage, stop and target behavior are those of the frozen Paper engine, not actual exchange executions.",
            "This replay is not a sealed Holdout unless the input dataset was separately frozen and access-controlled before evaluation.",
            "A positive result on this dataset alone does not authorize Mainnet execution.",
        ],
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Causal replay of archived public Paper snapshots.")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-incomplete-diagnostic", action="store_true")
    args = parser.parse_args()
    result = replay_archive(
        args.archive, args.manifest, args.output,
        require_ready=not args.allow_incomplete_diagnostic,
    )
    print(json.dumps({
        "evaluation": result["evaluation"],
        "metrics": result["metrics"],
        "source_archive_sha256": result["source_archive_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
