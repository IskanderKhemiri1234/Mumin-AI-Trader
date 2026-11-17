from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.backup_paper_ledger import create_backup, verify_backup


def _database(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE bot_account (
                id INTEGER PRIMARY KEY,
                initial_cash TEXT NOT NULL,
                tick_count INTEGER NOT NULL,
                halted INTEGER NOT NULL
            );
            CREATE TABLE bot_market_ticks (
                tick INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                data_source TEXT NOT NULL
            );
            CREATE TABLE bot_fills (
                fill_id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL
            );
            INSERT INTO bot_account VALUES (1, '70.00', 1, 0);
            INSERT INTO bot_market_ticks VALUES (1, 'BTCUSDT', 'BINANCE_PUBLIC_MARKET_DATA');
            """
        )


def test_backup_is_online_verified_and_manifested(tmp_path: Path) -> None:
    source = tmp_path / "paper.sqlite3"
    backup_dir = tmp_path / "backups"
    _database(source)

    result = create_backup(
        source,
        backup_dir,
        now=datetime(2026, 11, 17, tzinfo=timezone.utc),
    )
    backup = backup_dir / result["backup_file"]

    assert backup.is_file()
    assert (backup_dir / result["manifest_file"]).is_file()
    assert result["snapshot"]["initial_cash_usdt"] == "70.00"
    assert verify_backup(backup)["status"] == "PASS"


def test_backup_verification_detects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "paper.sqlite3"
    backup_dir = tmp_path / "backups"
    _database(source)
    result = create_backup(source, backup_dir)
    backup = backup_dir / result["backup_file"]
    backup.write_bytes(backup.read_bytes() + b"tamper")

    with pytest.raises(RuntimeError, match="SHA-256"):
        verify_backup(backup)


def test_backup_refuses_changed_paper_capital(tmp_path: Path) -> None:
    source = tmp_path / "paper.sqlite3"
    _database(source)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE bot_account SET initial_cash='71.00' WHERE id=1")

    with pytest.raises(RuntimeError, match="Unexpected Paper initial capital"):
        create_backup(source, tmp_path / "backups")
