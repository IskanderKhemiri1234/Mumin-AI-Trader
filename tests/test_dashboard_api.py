from fastapi.testclient import TestClient

from mumin_trader.main import app
from scripts.run_continuous_paper import ContinuousPaperBot


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


def test_dashboard_api_returns_waiting_state_without_creating_database(
    monkeypatch, tmp_path
):
    database = tmp_path / "not-created.sqlite3"
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "waiting_for_ledger"
    assert payload["account"] is None
    assert payload["positions"] == []
    assert payload["fills"] == []
    assert not database.exists()


def test_dashboard_api_reads_paper_ledger_without_mutating_it(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.step()
    before = bot.report()["tick"]

    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["mode"] == "PAPER"
    assert payload["environment"] == "TESTNET"
    assert payload["account"]["tick"] == before
    assert payload["account"]["equity"] > 0
    assert isinstance(payload["positions"], list)
    assert isinstance(payload["fills"], list)
    assert bot.report()["tick"] == before
