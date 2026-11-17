from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPECTED_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
)
PUBLIC_SOURCE = "BINANCE_PUBLIC_MARKET_DATA"


def _rows(connection: sqlite3.Connection, table: str, source_filter: bool = True) -> list[dict[str, Any]]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not exists:
        return []
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if "data_source" in columns and source_filter:
        query = f"SELECT * FROM {table} WHERE data_source=? ORDER BY 1"
        rows = connection.execute(query, (PUBLIC_SOURCE,)).fetchall()
    else:
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    return [dict(row) for row in rows]


def export_archive(database: str | Path, archive_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    db_path = Path(database).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        ticks = _rows(connection, "bot_market_ticks")
        decisions = _rows(connection, "bot_agent_decisions")
        fills = _rows(connection, "bot_fills")
    finally:
        connection.close()

    ticks_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tick_symbols: dict[int, set[str]] = defaultdict(set)
    for row in ticks:
        symbol = str(row["symbol"]).upper()
        ticks_by_symbol[symbol].append(row)
        tick_symbols[int(row["tick"])].add(symbol)

    unique_closes: dict[str, set[int]] = defaultdict(set)
    for row in ticks:
        if row.get("candle_close_time_ms") is not None:
            unique_closes[str(row["symbol"]).upper()].add(int(row["candle_close_time_ms"]))

    per_symbol: dict[str, Any] = {}
    total_duplicate_closes = 0
    total_missing_minute_slots = 0
    largest_gap_ms = 0
    for symbol in EXPECTED_SYMBOLS:
        rows = ticks_by_symbol.get(symbol, [])
        stamps = sorted(unique_closes.get(symbol, set()))
        duplicates = sum(
            1 for row in rows if row.get("candle_close_time_ms") is not None
        ) - len(stamps)
        gaps = [right - left for left, right in zip(stamps, stamps[1:])]
        symbol_largest = max(gaps, default=0)
        missing = sum(max(0, (gap // 60_000) - 1) for gap in gaps if gap > 60_000)
        total_duplicate_closes += duplicates
        total_missing_minute_slots += missing
        largest_gap_ms = max(largest_gap_ms, symbol_largest)
        per_symbol[symbol] = {
            "market_snapshot_rows": len(rows),
            "unique_closed_candles": len(stamps),
            "duplicate_candle_snapshots": duplicates,
            "estimated_missing_one_minute_slots": missing,
            "first_candle_close_ms": stamps[0] if stamps else None,
            "last_candle_close_ms": stamps[-1] if stamps else None,
            "largest_observed_gap_ms": symbol_largest,
        }

    macro_complete_rows = 0
    macro_timestamp_aligned_rows = 0
    minute_timestamp_aligned_rows = 0
    for row in ticks:
        closes = json.loads(row.get("macro_hourly_closes_json") or "[]")
        close_times = json.loads(row.get("macro_hourly_close_times_json") or "[]")
        received_at = int(row.get("received_at_ms") or 0)
        candle_close = row.get("candle_close_time_ms")
        if candle_close is not None and int(candle_close) < received_at:
            minute_timestamp_aligned_rows += 1
        if len(closes) == 21:
            macro_complete_rows += 1
        if (
            len(closes) == 21
            and len(close_times) == 21
            and all(
                int(left) < int(right)
                for left, right in zip(close_times, close_times[1:])
            )
            and all(int(value) < received_at for value in close_times)
        ):
            macro_timestamp_aligned_rows += 1

    complete_cycles = sum(set(symbols) == set(EXPECTED_SYMBOLS) for symbols in tick_symbols.values())
    observed_cycles = len(tick_symbols)
    all_symbols_present = all(ticks_by_symbol.get(symbol) for symbol in EXPECTED_SYMBOLS)
    all_macro_inputs_present = bool(ticks) and macro_complete_rows == len(ticks)
    all_macro_timestamps_aligned = bool(ticks) and macro_timestamp_aligned_rows == len(ticks)
    replay_ready = (
        all_symbols_present
        and observed_cycles >= 30
        and complete_cycles == observed_cycles
        and all_macro_inputs_present
        and all_macro_timestamps_aligned
        and minute_timestamp_aligned_rows == len(ticks)
        and total_duplicate_closes == 0
        and total_missing_minute_slots == 0
    )
    archive_path = Path(archive_path)
    manifest_path = Path(manifest_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    with gzip.GzipFile(filename="", mode="wb", fileobj=archive_path.open("wb"), mtime=0) as zipped:
        for kind, rows in (("market_snapshot", ticks), ("agent_decision", decisions), ("paper_fill", fills)):
            for row in rows:
                record = {"record_type": kind, "record": row}
                zipped.write((json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    generated = datetime.now(UTC).isoformat()
    timestamps = [
        int(row["candle_close_time_ms"]) for row in ticks
        if row.get("candle_close_time_ms") is not None
    ]
    manifest = {
        "schema_version": 1,
        "generated_at_utc": generated,
        "source": PUBLIC_SOURCE,
        "archive_sha256": digest,
        "archive_bytes": archive_path.stat().st_size,
        "record_counts": {
            "market_snapshots": len(ticks),
            "agent_decisions": len(decisions),
            "paper_fills": len(fills),
        },
        "expected_symbols": list(EXPECTED_SYMBOLS),
        "symbols_present": sorted(ticks_by_symbol),
        "all_expected_symbols_present": all_symbols_present,
        "observed_cycle_count": observed_cycles,
        "complete_eight_symbol_cycles": complete_cycles,
        "incomplete_cycle_count": observed_cycles - complete_cycles,
        "per_symbol": per_symbol,
        "quality": {
            "duplicate_candle_snapshots": total_duplicate_closes,
            "estimated_missing_one_minute_slots": total_missing_minute_slots,
            "largest_observed_gap_ms": largest_gap_ms,
            "first_candle_close_ms": min(timestamps) if timestamps else None,
            "last_candle_close_ms": max(timestamps) if timestamps else None,
            "synthetic_rows_excluded": True,
            "historical_depth_available_only_at_recorded_snapshots": True,
            "raw_21_hourly_closes_persisted": all_macro_inputs_present,
            "raw_21_hourly_close_timestamp_alignment_passed": all_macro_timestamps_aligned,
            "one_minute_close_timestamp_alignment_passed": bool(ticks) and minute_timestamp_aligned_rows == len(ticks),
            "minute_timestamp_aligned_rows": minute_timestamp_aligned_rows,
            "macro_complete_rows": macro_complete_rows,
            "macro_timestamp_aligned_rows": macro_timestamp_aligned_rows,
            "replay_ready": replay_ready,
            "holdout_eligible": False,
            "status": "OBSERVATION_ARCHIVE_ONLY",
        },
        "limitations": [
            "The archive includes only recorded public Paper snapshots; missing intervals are not reconstructed.",
            "Order-book depth exists only at captured snapshot times and is not historical exchange depth for skipped intervals.",
            "Legacy snapshots created before the macro-input schema migration contain no raw hourly closes or close timestamps and cannot be independently replayed.",
            "Replay readiness requires 21 closed hourly values and their exact close timestamps for every row, complete eight-symbol cycles, at least 30 cycles, and no missing one-minute slots or duplicate candle snapshots.",
            "The current schedule is best-effort and is not a guaranteed continuous 24/7 feed.",
            "This archive is not a sealed Holdout and must not be used to claim out-of-sample profitability.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a provenance-labelled Paper observation archive.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    manifest = export_archive(args.db, args.archive, args.manifest)
    print(json.dumps({
        "archive_sha256": manifest["archive_sha256"],
        "record_counts": manifest["record_counts"],
        "quality": manifest["quality"],
    }, indent=2))


if __name__ == "__main__":
    main()
