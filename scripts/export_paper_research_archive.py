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
ONE_MINUTE_MS = 60_000


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


def _latest_contiguous_cycles(
    source_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select one complete eight-symbol cycle per closed minute, then the latest gap-free suffix."""
    by_tick: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in source_rows:
        by_tick[int(row["tick"])][str(row["symbol"]).upper()] = row

    complete_by_close: dict[int, tuple[int, dict[str, dict[str, Any]]]] = {}
    incomplete_ticks = 0
    mismatched_close_ticks = 0
    for tick, symbols in by_tick.items():
        if set(symbols) != set(EXPECTED_SYMBOLS):
            incomplete_ticks += 1
            continue
        closes = {int(row["candle_close_time_ms"]) for row in symbols.values()}
        if len(closes) != 1:
            mismatched_close_ticks += 1
            continue
        close_time = next(iter(closes))
        current = complete_by_close.get(close_time)
        if current is None or tick > current[0]:
            complete_by_close[close_time] = (tick, symbols)

    ordered_closes = sorted(complete_by_close)
    last_gap_index = 0
    gap_count = 0
    for index, (left, right) in enumerate(zip(ordered_closes, ordered_closes[1:])):
        if right - left > ONE_MINUTE_MS:
            gap_count += 1
            last_gap_index = index + 1

    selected_closes = ordered_closes[last_gap_index:]
    selected_rows: list[dict[str, Any]] = []
    selected_ticks: set[int] = set()
    for close_time in selected_closes:
        tick, symbols = complete_by_close[close_time]
        selected_ticks.add(tick)
        selected_rows.extend(symbols[symbol] for symbol in EXPECTED_SYMBOLS)

    source_duplicate_rows = max(0, len(source_rows) - len(complete_by_close) * len(EXPECTED_SYMBOLS))
    selection = {
        "policy": (
            "For each closed one-minute candle, select the latest complete eight-symbol "
            "source tick; retain only the latest contiguous suffix after the most recent "
            "missing-minute gap. No missing candles are synthesized."
        ),
        "source_market_snapshot_rows": len(source_rows),
        "source_tick_count": len(by_tick),
        "source_incomplete_tick_count": incomplete_ticks,
        "source_mismatched_close_tick_count": mismatched_close_ticks,
        "source_complete_candle_count": len(complete_by_close),
        "source_duplicate_or_excluded_snapshot_rows": source_duplicate_rows,
        "source_gap_count": gap_count,
        "excluded_preceding_contiguous_segment_candles": last_gap_index,
        "selected_contiguous_candle_count": len(selected_closes),
        "selected_source_ticks": sorted(selected_ticks),
        "selected_start_candle_close_ms": selected_closes[0] if selected_closes else None,
        "selected_end_candle_close_ms": selected_closes[-1] if selected_closes else None,
    }
    return selected_rows, selection


def export_archive(database: str | Path, archive_path: str | Path, manifest_path: str | Path) -> dict[str, Any]:
    db_path = Path(database).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        source_ticks = _rows(connection, "bot_market_ticks")
        all_decisions = _rows(connection, "bot_agent_decisions")
        all_fills = _rows(connection, "bot_fills")
    finally:
        connection.close()

    ticks, selection = _latest_contiguous_cycles(source_ticks)
    selected_tick_ids = set(selection["selected_source_ticks"])
    decisions = [row for row in all_decisions if int(row["tick"]) in selected_tick_ids]
    fills = [row for row in all_fills if int(row["tick"]) in selected_tick_ids]

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
        missing = sum(max(0, (gap // ONE_MINUTE_MS) - 1) for gap in gaps if gap > ONE_MINUTE_MS)
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
        "schema_version": 2,
        "generated_at_utc": generated,
        "source": PUBLIC_SOURCE,
        "source_database": str(db_path),
        "archive_sha256": digest,
        "archive_bytes": archive_path.stat().st_size,
        "record_counts": {
            "market_snapshots": len(ticks),
            "agent_decisions": len(decisions),
            "paper_fills": len(fills),
        },
        "selection": selection,
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
            "Only public-market observations are included; synthetic rows are excluded.",
            "For each minute, the latest complete eight-symbol snapshot is selected; earlier same-minute snapshots are excluded.",
            "Only the latest contiguous one-minute suffix is retained. All preceding gaps are disclosed in the selection metadata and no missing data is reconstructed.",
            "Order-book depth is available only at captured snapshot times and is not historical exchange depth for skipped intervals.",
            "Replay readiness requires at least 30 complete cycles, 21 causally aligned hourly closes per row, minute timestamp alignment, and no gaps or duplicates within the selected suffix.",
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
        "selection": manifest["selection"],
        "quality": manifest["quality"],
    }, indent=2))


if __name__ == "__main__":
    main()
