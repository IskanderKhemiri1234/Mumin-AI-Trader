from fastapi.testclient import TestClient

import mumin_trader.main as main_module
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
    assert control["available_actions"] == ["PAPER_PAUSE", "PAPER_RESUME"]


def test_dashboard_page_exposes_live_control_center():
    response = TestClient(app).get("/dashboard")
    assert response.status_code == 200
    page = response.text
    assert 'id="control"' in page
    assert 'id="marketRows"' in page
    assert 'id="marketReconnect"' in page
    assert "/api/market" in page
    assert "new WebSocket(" not in page
    assert "no fake browser-only start button" in page



def test_paper_worker_pause_and_resume_require_live_heartbeat(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.heartbeat("test-paper-worker")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("TRADING_ENV", "TESTNET")
    monkeypatch.setenv("ALLOW_MAINNET", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    client = TestClient(app)
    headers = {"X-Dashboard-Token": "server-only-token"}

    paused = client.post("/api/paper-control", json={"action": "pause"}, headers=headers)
    assert paused.status_code == 200
    assert paused.json()["desired_state"] == "PAUSED"
    assert paused.json()["execution_authorized"] is False

    assert bot.heartbeat("test-paper-worker") == "PAUSED"
    resumed = client.post("/api/paper-control", json={"action": "resume"}, headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["desired_state"] == "RUNNING"
    assert bot.heartbeat("test-paper-worker") == "RUNNING"


def test_paper_worker_controls_reject_stale_or_missing_heartbeat(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    ContinuousPaperBot(str(database))
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).post(
        "/api/paper-control",
        json={"action": "pause"},
        headers={"X-Dashboard-Token": "server-only-token"},
    )
    assert response.status_code == 409
    assert "heartbeat is stale" in response.json()["detail"]


def test_paper_worker_controls_reject_live_or_mainnet_configuration(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.heartbeat("test-paper-worker")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    response = TestClient(app).post(
        "/api/paper-control",
        json={"action": "pause"},
        headers={"X-Dashboard-Token": "server-only-token"},
    )
    assert response.status_code == 409
    assert "TRADING_MODE=PAPER" in response.json()["detail"]


def test_paper_worker_snapshot_reports_desired_state(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.heartbeat("test-paper-worker")
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )
    assert response.status_code == 200
    assert response.json()["paper_worker"]["status"] == "online"
    assert response.json()["paper_worker"]["desired_state"] == "RUNNING"



def test_market_api_requires_dashboard_token(monkeypatch):
    monkeypatch.delenv("DASHBOARD_ACCESS_TOKEN", raising=False)
    response = TestClient(app).get("/api/market")
    assert response.status_code == 503


def test_market_api_returns_shared_server_stream(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    started = []

    async def fake_start():
        started.append(True)

    async def fake_snapshot():
        return {
            "status": "live",
            "source": "BINANCE_PUBLIC_SERVER_STREAM",
            "updated_at": "2026-11-16T23:00:00+00:00",
            "age_seconds": 0.1,
            "assets": [
                {"symbol": "BTCUSDT", "price": 90000.0, "change": 1.25}
            ],
        }

    monkeypatch.setattr(main_module._market_stream, "start", fake_start)
    monkeypatch.setattr(main_module._market_stream, "snapshot", fake_snapshot)
    response = TestClient(app).get(
        "/api/market", headers={"X-Dashboard-Token": "server-only-token"}
    )

    assert response.status_code == 200
    assert started == [True]
    assert response.json()["source"] == "BINANCE_PUBLIC_SERVER_STREAM"
    assert response.json()["assets"][0]["symbol"] == "BTCUSDT"


def test_live_supervisor_allows_resume_without_worker_and_records_audit(
    monkeypatch, tmp_path
):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.supervisor_heartbeat("test-supervisor", None)
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("TRADING_ENV", "TESTNET")
    monkeypatch.setenv("ALLOW_MAINNET", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    response = TestClient(app).post(
        "/api/paper-control",
        json={"action": "resume"},
        headers={"X-Dashboard-Token": "server-only-token"},
    )

    assert response.status_code == 200
    assert response.json()["desired_state"] == "RUNNING"
    audit = bot._connect().execute(
        "SELECT event_type, desired_state FROM bot_runtime_audit ORDER BY audit_id DESC LIMIT 1"
    ).fetchone()
    assert audit["event_type"] == "CONTROL_REQUESTED"
    assert audit["desired_state"] == "RUNNING"


def test_dashboard_reports_supervisor_and_worker_audit(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.supervisor_heartbeat("test-supervisor", 12345)
    bot.record_runtime_audit(
        "WORKER_STARTED",
        "test-supervisor",
        desired_state="RUNNING",
        detail="pid=12345",
    )
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))

    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )

    assert response.status_code == 200
    worker = response.json()["paper_worker"]
    assert worker["supervisor_status"] == "online"
    assert worker["worker_status"] == "starting"
    assert worker["audit"][0]["event_type"] == "WORKER_STARTED"


def test_dashboard_exposes_locked_strategy_and_risk_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "server-only-token")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Token": "server-only-token"}
    )

    assert response.status_code == 200
    settings = response.json()["strategy_settings"]
    assert settings["symbols"] == [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
        "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    ]
    assert settings["strategy"]["entry_consensus"] == "3/3"
    assert settings["risk_limits"]["max_trade_usdt"] == "5.00"
    assert settings["risk_limits"]["reserve_percent"] == "20.00"
    assert settings["risk_limits"]["max_daily_loss_percent"] == "2.00"
    assert settings["risk_limits"]["max_drawdown_percent"] == "5.00"
    assert settings["risk_limits"]["max_spread_bps"] == "8.00"
    assert settings["risk_limits_locked"] is True
    assert settings["approval_required_for_changes"] is True
    assert settings["settings_mutation_enabled"] is False
