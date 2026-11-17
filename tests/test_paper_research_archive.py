from __future__ import annotations

import gzip
import json
import sqlite3

from scripts.export_paper_research_archive import EXPECTED_SYMBOLS, export_archive
from scripts.replay_paper_observations import replay_archive


def _ledger(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE bot_market_ticks (
            id INTEGER PRIMARY KEY, tick INTEGER, symbol TEXT,
            candle_close_time_ms INTEGER, data_source TEXT, received_at_ms INTEGER,
            macro_hourly_closes_json TEXT, macro_hourly_close_times_json TEXT,
            price TEXT, bid TEXT, ask TEXT, candle_open TEXT, candle_high TEXT,
            candle_low TEXT, candle_volume TEXT, bid_depth_qty TEXT, ask_depth_qty TEXT
        );
        CREATE TABLE bot_agent_decisions (
            decision_id INTEGER PRIMARY KEY, tick INTEGER, symbol TEXT,
            data_source TEXT, final_signal TEXT
        );
        CREATE TABLE bot_fills (
            fill_id INTEGER PRIMARY KEY, tick INTEGER, symbol TEXT,
            data_source TEXT, side TEXT
        );
    """)
    closes = json.dumps(["100"] * 21)
    close_times = json.dumps([1_799_000_000_000 + i * 3_600_000 for i in range(21)])
    for index, symbol in enumerate(EXPECTED_SYMBOLS, start=1):
        db.execute(
            "INSERT INTO bot_market_ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (index, 1, symbol, 1_800_000_000_000, "BINANCE_PUBLIC_MARKET_DATA",
             1_800_000_000_100, closes, close_times, "100", "99", "101",
             "100", "102", "98", "10", "500", "400"),
        )
    db.execute(
        "INSERT INTO bot_market_ticks (id,tick,symbol,candle_close_time_ms,data_source,received_at_ms,macro_hourly_closes_json,macro_hourly_close_times_json) VALUES (9,1,'BTCUSDT',1800000000000,'MOCK_SYNTHETIC',1800000000100,'[]','[]')"
    )
    db.execute(
        "INSERT INTO bot_agent_decisions VALUES (1,1,'BTCUSDT','BINANCE_PUBLIC_MARKET_DATA','HOLD')"
    )
    db.execute(
        "INSERT INTO bot_agent_decisions VALUES (2,1,'BTCUSDT','MOCK_SYNTHETIC','BUY')"
    )
    db.execute(
        "INSERT INTO bot_fills VALUES (1,1,'BTCUSDT','BINANCE_PUBLIC_MARKET_DATA','BUY')"
    )
    db.execute(
        "INSERT INTO bot_fills VALUES (2,1,'BTCUSDT','MOCK_SYNTHETIC','SELL')"
    )
    db.commit()
    db.close()


def _add_cycle(db_path, tick, candle_close, received_at, id_start):
    db = sqlite3.connect(db_path)
    closes = json.dumps(["100"] * 21)
    close_times = json.dumps([
        candle_close - 21 * 3_600_000 + i * 3_600_000 for i in range(21)
    ])
    for offset, symbol in enumerate(EXPECTED_SYMBOLS):
        db.execute(
            "INSERT INTO bot_market_ticks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (id_start + offset, tick, symbol, candle_close,
             "BINANCE_PUBLIC_MARKET_DATA", received_at, closes, close_times,
             "100", "99", "101", "100", "102", "98", "10", "500", "400"),
        )
    db.commit()
    db.close()


def test_archive_is_deterministic_and_excludes_synthetic_rows(tmp_path):
    database = tmp_path / "paper.db"
    archive = tmp_path / "observations.jsonl.gz"
    manifest_path = tmp_path / "manifest.json"
    _ledger(database)

    manifest = export_archive(database, archive, manifest_path)
    with gzip.open(archive, "rt", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]

    assert manifest["all_expected_symbols_present"] is True
    assert manifest["complete_eight_symbol_cycles"] == 1
    assert manifest["record_counts"] == {
        "market_snapshots": 8, "agent_decisions": 1, "paper_fills": 1
    }
    assert all("MOCK_SYNTHETIC" not in json.dumps(row) for row in records)
    assert manifest["quality"]["raw_21_hourly_closes_persisted"] is True
    assert manifest["quality"]["raw_21_hourly_close_timestamp_alignment_passed"] is True
    assert manifest["quality"]["one_minute_close_timestamp_alignment_passed"] is True
    assert manifest["quality"]["replay_numeric_quote_rows"] == 8
    assert manifest["quality"]["replay_macro_numeric_rows"] == 8
    assert manifest["quality"]["macro_complete_rows"] == 8
    assert manifest["quality"]["replay_ready"] is False
    assert manifest["quality"]["holdout_eligible"] is False


def test_archive_does_not_modify_source_database(tmp_path):
    database = tmp_path / "paper.db"
    _ledger(database)
    before = database.read_bytes()

    export_archive(database, tmp_path / "observations.jsonl.gz", tmp_path / "manifest.json")

    assert database.read_bytes() == before


def test_archive_deduplicates_repeated_same_minute_cycles(tmp_path):
    database = tmp_path / "paper.db"
    archive = tmp_path / "observations.jsonl.gz"
    manifest_path = tmp_path / "manifest.json"
    _ledger(database)
    _add_cycle(database, 2, 1_800_000_000_000, 1_800_000_000_200, 100)
    _add_cycle(database, 3, 1_800_000_060_000, 1_800_000_060_200, 200)

    manifest = export_archive(database, archive, manifest_path)
    with gzip.open(archive, "rt", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    market = [item["record"] for item in records if item["record_type"] == "market_snapshot"]

    assert manifest["selection"]["source_tick_count"] == 3
    assert manifest["selection"]["selected_source_ticks"] == [2, 3]
    assert manifest["selection"]["source_duplicate_or_excluded_snapshot_rows"] == 8
    assert manifest["record_counts"]["market_snapshots"] == 16
    assert {row["tick"] for row in market} == {2, 3}
    assert manifest["quality"]["duplicate_candle_snapshots"] == 0


def test_archive_uses_only_latest_gap_free_suffix_and_discloses_older_gap(tmp_path):
    database = tmp_path / "paper.db"
    archive = tmp_path / "observations.jsonl.gz"
    manifest_path = tmp_path / "manifest.json"
    _ledger(database)
    _add_cycle(database, 2, 1_800_000_060_000, 1_800_000_060_200, 100)
    _add_cycle(database, 3, 1_800_000_180_000, 1_800_000_180_200, 200)

    manifest = export_archive(database, archive, manifest_path)
    with gzip.open(archive, "rt", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    market = [item["record"] for item in records if item["record_type"] == "market_snapshot"]

    assert manifest["selection"]["source_gap_count"] == 1
    assert manifest["selection"]["excluded_preceding_contiguous_segment_candles"] == 2
    assert manifest["selection"]["selected_source_ticks"] == [3]
    assert len(market) == 8
    assert {row["tick"] for row in market} == {3}
    assert manifest["quality"]["estimated_missing_one_minute_slots"] == 0
    assert manifest["quality"]["replay_ready"] is False



def test_thirty_clean_cycles_are_replay_ready_and_replay_without_lookahead(tmp_path):
    database = tmp_path / "paper.db"
    archive = tmp_path / "observations.jsonl.gz"
    manifest_path = tmp_path / "manifest.json"
    replay_path = tmp_path / "replay.json"
    _ledger(database)

    base_close = 1_800_000_000_000
    for offset in range(30):
        tick = offset + 2
        close_time = base_close + offset * 60_000
        _add_cycle(
            database,
            tick,
            close_time,
            close_time + 100,
            100 + offset * len(EXPECTED_SYMBOLS),
        )

    manifest = export_archive(database, archive, manifest_path)
    assert manifest["observed_cycle_count"] == 30
    assert manifest["quality"]["replay_ready"] is True

    replay = replay_archive(
        archive,
        manifest_path,
        replay_path,
        require_ready=True,
        warmup_cycles=0,
    )
    assert replay["metrics"]["cycles_replayed"] == 30
    assert replay["metrics"]["accounting_status"] == "PASS"
    assert replay["source_archive_sha256"] == manifest["archive_sha256"]
