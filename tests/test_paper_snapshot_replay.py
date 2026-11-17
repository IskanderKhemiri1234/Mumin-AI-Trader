from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from scripts.replay_paper_observations import replay_archive
from scripts.run_paper_walk_forward import run_walk_forward
from scripts.run_continuous_paper import PUBLIC_SOURCE, SYMBOLS, ContinuousPaperBot


def _write_archive(tmp_path, *, future_macro_time=False, cycles=16, replay_ready=False):
    archive = tmp_path / "observations.jsonl.gz"
    manifest = tmp_path / "manifest.json"
    rows = []
    base_close = 1_800_000_000_000
    for tick in range(1, cycles + 1):
        received = base_close + tick * 60_000 + 1_000
        close = received - 1_000
        macro_times = [base_close - 90_000_000 + index * 3_600_000 for index in range(21)]
        if future_macro_time and tick == 1:
            macro_times[-1] = received + 1
        macro_closes = ["100"] * 21
        for symbol in SYMBOLS:
            rows.append({
                "record_type": "market_snapshot",
                "record": {
                    "tick": tick,
                    "symbol": symbol,
                    "data_source": PUBLIC_SOURCE,
                    "received_at_ms": received,
                    "candle_close_time_ms": close,
                    "price": "100",
                    "bid": "99.99",
                    "ask": "100.01",
                    "candle_open": "100",
                    "candle_high": "100.2",
                    "candle_low": "99.8",
                    "candle_volume": "10",
                    "bid_depth_qty": "50",
                    "ask_depth_qty": "50",
                    "macro_hourly_closes_json": json.dumps(macro_closes),
                    "macro_hourly_close_times_json": json.dumps(macro_times),
                },
            })
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            for row in rows:
                zipped.write((json.dumps(row, sort_keys=True) + "\n").encode())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest.write_text(json.dumps({
        "archive_sha256": digest,
        "quality": {"replay_ready": replay_ready},
    }), encoding="utf-8")
    return archive, manifest


def test_paper_step_uses_explicit_timezone_aware_event_time(tmp_path):
    database = tmp_path / "event-time.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    event_time = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    bot.step({"BTCUSDT": Decimal("100")}, event_time=event_time)

    with sqlite3.connect(database) as db:
        stored = db.execute("SELECT created_at FROM bot_equity WHERE tick=1").fetchone()[0]
    assert stored == event_time.isoformat()


def test_paper_step_rejects_naive_event_time(tmp_path):
    bot = ContinuousPaperBot(str(tmp_path / "naive.sqlite3"), initial_cash=Decimal("70.00"))
    with pytest.raises(ValueError, match="timezone-aware"):
        bot.step(
            {"BTCUSDT": Decimal("100")},
            event_time=datetime(2025, 1, 2, 3, 4, 5),
        )


def test_snapshot_replay_runs_chronologically_and_reconciles(tmp_path):
    archive, manifest = _write_archive(tmp_path)

    result = replay_archive(
        archive, manifest, tmp_path / "replay.json", require_ready=False
    )

    assert result["evaluation"] == "INCOMPLETE_DIAGNOSTIC_ONLY"
    assert result["metrics"]["cycles_replayed"] == 16
    assert result["metrics"]["accounting_status"] == "PASS"
    assert result["metrics"]["closed_trades"] == 0
    assert result["source_archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_snapshot_replay_rejects_hourly_macro_lookahead(tmp_path):
    archive, manifest = _write_archive(tmp_path, future_macro_time=True)

    with pytest.raises(ValueError, match="look-ahead"):
        replay_archive(
            archive, manifest, tmp_path / "replay.json", require_ready=False
        )


def test_formal_replay_refuses_unqualified_dataset(tmp_path):
    archive, manifest = _write_archive(tmp_path)

    with pytest.raises(ValueError, match="not marked replay_ready"):
        replay_archive(
            archive, manifest, tmp_path / "replay.json", require_ready=True
        )

def test_walk_forward_uses_prior_30_cycles_as_entry_disabled_warmup(tmp_path):
    archive, manifest = _write_archive(tmp_path, cycles=60, replay_ready=True)

    result = run_walk_forward(
        archive, manifest, tmp_path / "walk-forward.json", folds=1
    )

    assert result["evaluation"] == "WALK_FORWARD_OOS"
    assert result["aggregate"]["fold_count"] == 1
    assert result["aggregate"]["total_closed_trades"] == 0
    assert result["tuning_performed"] is False
    assert result["holdout_used"] is False
    assert result["folds"][0]["evaluation_cycles"] == 30
    assert result["folds"][0]["accounting_status"] == "PASS"
