import pytest
from fastapi.testclient import TestClient

from mumin_trader.main import app


def test_dashboard_api_requires_server_token(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHBOARD_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get("/api/dashboard")
    assert response.status_code == 503
    assert "DASHBOARD_ACCESS_TOKEN" in response.json()["detail"]


def test_dashboard_api_rejects_wrong_token(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "wrong-token"}
    )
    assert response.status_code == 401


def test_dashboard_api_returns_read_only_empty_ledger_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "not-created.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "waiting_for_ledger"
    assert payload["account"] is None
    assert payload["positions"] == []
    assert payload["fills"] == []
