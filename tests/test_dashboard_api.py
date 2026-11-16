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

def test_dashboard_page_exposes_readiness_gate_center():
    response = TestClient(app).get("/dashboard")

    assert response.status_code == 200
    page = response.text
    assert 'id="readiness"' in page
    assert "0 / 5" in page
    assert "NO-GO" in page
    for gate in (
        "gate1Title",
        "gate2Title",
        "gate3Title",
        "gate4Title",
        "gate5Title",
    ):
        assert gate in page
    assert 'data-page="readiness"' in page



def test_dashboard_api_exposes_read_only_control_plane(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )
    assert response.status_code == 200
    control = response.json()["control_plane"]
    assert control["read_only"] is True
    assert control["execution_authorized"] is False
    assert control["testnet_order_submission"] == "DISABLED"
    assert control["mainnet_order_submission"] == "BLOCKED"
    assert control["available_actions"] == []


def test_dashboard_page_exposes_live_control_center():
    response = TestClient(app).get("/dashboard")
    assert response.status_code == 200
    page = response.text
    assert 'id="control"' in page
    assert 'id="marketRows"' in page
    assert 'id="marketReconnect"' in page
    assert "wss://stream.binance.com:9443/stream" in page
    assert "no fake browser-only start button" in page
