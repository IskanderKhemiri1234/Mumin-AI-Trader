from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mumin_trader.operations_chat import (
    classify_intent,
    read_operations_snapshot,
)


@pytest.fixture
def paper_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "paper.db"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE bot_account (
                id INTEGER PRIMARY KEY, initial_cash TEXT, cash TEXT,
                realized_pnl TEXT, fees_paid TEXT, halted INTEGER,
                halt_reason TEXT, tick_count INTEGER
            );
            CREATE TABLE bot_positions (
                symbol TEXT PRIMARY KEY, quantity TEXT, entry_price TEXT,
                entry_gross TEXT, entry_fee TEXT, opened_at TEXT
            );
            CREATE TABLE bot_fills (
                fill_id INTEGER PRIMARY KEY, symbol TEXT, side TEXT,
                quantity TEXT, fill_price TEXT, gross_quote TEXT,
                fee_quote TEXT, net_pnl TEXT
            );
            CREATE TABLE bot_market_ticks (
                id INTEGER PRIMARY KEY, symbol TEXT, price TEXT,
                data_source TEXT, received_at_ms INTEGER
            );
            CREATE TABLE bot_runtime_control (
                id INTEGER PRIMARY KEY, desired_state TEXT, heartbeat_at TEXT,
                supervisor_heartbeat_at TEXT, worker_pid INTEGER
            );
            CREATE TABLE bot_runtime_audit (
                audit_id INTEGER PRIMARY KEY, event_type TEXT, actor TEXT,
                desired_state TEXT, detail TEXT, created_at TEXT
            );
            INSERT INTO bot_account VALUES
                (1, '100.00', '100.00', '0', '0', 0, NULL, 12);
            """
        )
        db.execute(
            "INSERT INTO bot_runtime_control VALUES (1, 'RUNNING', ?, ?, 1234)",
            (now, now),
        )
        db.execute(
            "INSERT INTO bot_runtime_audit VALUES (1, 'SUPERVISOR_STARTED', 'test', 'RUNNING', 'fixture started', ?)",
            (now,),
        )
    return path


def test_classifies_arabic_and_english_questions() -> None:
    assert classify_intent("أين المحرك الآن؟") == "engine"
    assert classify_intent("Show my open positions") == "positions"
    assert classify_intent("كم الربح المحقق والعائم؟") == "pnl"
    assert classify_intent("Show recent alerts") == "alerts"
    assert classify_intent("سجل الأحداث") == "events"
    assert classify_intent("portfolio balance") == "portfolio"
    assert classify_intent("ملخص الحالة") == "overview"
    assert classify_intent("Status summary") == "overview"
    assert classify_intent("رصيد Binance الحقيقي") == "mainnet"
    assert classify_intent("Show Binance Mainnet orders") == "mainnet"


def test_reads_real_ledger_and_audit_events_read_only(paper_ledger: Path) -> None:
    snapshot = read_operations_snapshot(paper_ledger)

    assert snapshot["database_status"] == "ready"
    assert snapshot["engine"]["worker_status"] == "running"
    assert snapshot["account"]["cash"] == "100.00"
    assert snapshot["account"]["equity"] == "100.00"
    assert snapshot["account"]["realized_pnl"] == "0"
    assert snapshot["reconciliation"]["status"] == "PASS"
    assert snapshot["events"][0]["event_type"] == "SUPERVISOR_STARTED"

    uri = paper_ledger.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE bot_account SET cash='0' WHERE id=1")


def test_missing_ledger_returns_actionable_alert(tmp_path: Path) -> None:
    snapshot = read_operations_snapshot(tmp_path / "missing.db")
    assert snapshot["database_status"] == "missing"
    assert snapshot["alerts"][0]["code"] == "LEDGER_MISSING"


def test_chat_api_requires_shared_dashboard_password(monkeypatch: pytest.MonkeyPatch, paper_ledger: Path) -> None:
    from mumin_trader.main import app

    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-dashboard-password-123456")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(paper_ledger))
    client = TestClient(app)

    denied = client.post("/api/operations-chat", json={"message": "engine status"})
    assert denied.status_code == 401

    legacy = client.post(
        "/api/operations-chat",
        headers={"X-Dashboard-Token": "test-dashboard-password-123456"},
        json={"message": "engine status"},
    )
    assert legacy.status_code == 401

    response = client.post(
        "/api/operations-chat",
        headers={"X-Dashboard-Password": "test-dashboard-password-123456"},
        json={"message": "حالة المحرك"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["external_services_used"] is False
    assert payload["intent"] == "engine"
    assert "Paper" in payload["answer"]
    assert payload["snapshot"]["mainnet"]["status"] == "LOCKED"
    asset = client.get("/operations-chat.js")
    assert asset.status_code == 200
    assert "api/operations-chat" in asset.text
    assert "X-Dashboard-Password" in asset.text
    assert "mumin_dashboard_password" in asset.text
    assert "X-Dashboard-Token" not in asset.text
    mainnet_asset = client.get("/mainnet-operations.js")
    assert mainnet_asset.status_code == 200
    assert "/api/mainnet/entries/protected" in mainnet_asset.text
