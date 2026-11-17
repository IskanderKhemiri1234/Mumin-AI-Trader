from __future__ import annotations

import gzip
import json
import sqlite3

from scripts.export_paper_research_archive import EXPECTED_SYMBOLS, export_archive


def _ledger(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE bot_market_ticks (
            id INTEGER PRIMARY KEY, tick INTEGER, symbol TEXT,
            candle_close_time_ms INTEGER, data_source TEXT, received_at_ms INTEGER,
            macro_hourly_closes_json TEXT, macro_hourly_close_times_json TEXT
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
            "INSERT INTO bot_market_ticks VALUES (?,?,?,?,?,?,?,?)",
            (index, 1, symbol, 1_800_000_000_000, "BINANCE_PUBLIC_MARKET_DATA",
             1_800_000_000_100, closes, close_times),
        )
    db.execute(
        "INSERT INTO bot_market_ticks VALUES (9,1,'BTCUSDT',1800000000000,'MOCK_SYNTHETIC',1800000000100,'[]','[]')"
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
    assert manifest["quality"]["macro_complete_rows"] == 8
    assert manifest["quality"]["replay_ready"] is False
    assert manifest["quality"]["holdout_eligible"] is False


def test_archive_does_not_modify_source_database(tmp_path):
    database = tmp_path / "paper.db"
    _ledger(database)
    before = database.read_bytes()

    export_archive(database, tmp_path / "observations.jsonl.gz", tmp_path / "manifest.json")

    assert database.read_bytes() == before
