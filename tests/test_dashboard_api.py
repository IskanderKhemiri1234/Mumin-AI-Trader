from fastapi.testclient import TestClient

import mumin_trader.main as main_module
from mumin_trader.main import app
from scripts.run_continuous_paper import ContinuousPaperBot


def test_root_reports_public_market_paper_only_without_account_link():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "paper_only"
    assert response.json()["market_data"] == "BINANCE_PUBLIC_SPOT"
    assert response.json()["execution"] == "SIMULATED_PAPER_ONLY"
    assert response.json()["account_link"] == "NONE"


def test_dashboard_api_requires_server_password(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get("/api/dashboard")
    assert response.status_code == 503
    assert "DASHBOARD_PASSWORD" in response.json()["detail"]


def test_dashboard_api_rejects_wrong_password(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "wrong-password"}
    )
    assert response.status_code == 401



def test_legacy_dashboard_token_is_revoked_and_not_accepted(monkeypatch):
    monkeypatch.setenv("DASHBOARD_ACCESS_TOKEN", "old-token-that-must-stop-working")
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    client = TestClient(app)

    legacy_environment_only = client.get(
        "/api/dashboard",
        headers={"X-Dashboard-Token": "old-token-that-must-stop-working"},
    )
    assert legacy_environment_only.status_code == 503

    monkeypatch.setenv("DASHBOARD_PASSWORD", "new-owner-password-123")
    legacy_header = client.get(
        "/api/dashboard",
        headers={"X-Dashboard-Token": "old-token-that-must-stop-working"},
    )
    assert legacy_header.status_code == 401


def test_dashboard_password_must_be_at_least_twelve_characters(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "too-short")
    response = TestClient(app).get(
        "/api/dashboard",
        headers={"X-Dashboard-Password": "too-short"},
    )
    assert response.status_code == 503
    assert "at least 12 characters" in response.json()["detail"]

def test_dashboard_api_returns_waiting_state_without_creating_database(
    monkeypatch, tmp_path
):
    database = tmp_path / "not-created.sqlite3"
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "server-only-password"}
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

    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "server-only-password"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["mode"] == "PAPER"
    assert payload["environment"] == "PAPER_ISOLATED"
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
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "server-only-password"}
    )
    assert response.status_code == 200
    control = response.json()["control_plane"]
    assert control["read_only"] is False
    assert control["execution_authorized"] is False
    assert control["paper_order_submission"] == "CONFIRMED_PAPER_ONLY"
    assert control["testnet_order_submission"] == "DISABLED_PENDING_EXPLICIT_OPERATIONAL_APPROVAL"
    assert control["mainnet_order_submission"] == "BLOCKED"
    assert "PAPER_BUY" in control["available_actions"]
    assert "PAPER_SELL" in control["available_actions"]


def test_dashboard_page_exposes_live_control_center():
    response = TestClient(app).get("/dashboard")
    assert response.status_code == 200
    page = response.text
    assert "X-Dashboard-Password" in page
    assert "mumin_dashboard_password" in page
    assert "sessionStorage.removeItem('mumin_dashboard_token')" in page
    assert 'id="control"' in page
    assert 'id="marketRows"' in page
    assert 'id="marketReconnect"' in page
    assert "/api/market" in page
    assert "new WebSocket(" not in page
    assert 'id="workerAuditRows"' in page
    assert "approvalRequired" in page



def test_paper_worker_pause_and_resume_require_live_heartbeat(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.heartbeat("test-paper-worker")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("TRADING_ENV", "TESTNET")
    monkeypatch.setenv("ALLOW_MAINNET", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    client = TestClient(app)
    headers = {"X-Dashboard-Password": "server-only-password"}

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
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).post(
        "/api/paper-control",
        json={"action": "pause"},
        headers={"X-Dashboard-Password": "server-only-password"},
    )
    assert response.status_code == 409
    assert "heartbeat is stale" in response.json()["detail"]


def test_paper_worker_controls_reject_live_or_mainnet_configuration(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.heartbeat("test-paper-worker")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    response = TestClient(app).post(
        "/api/paper-control",
        json={"action": "pause"},
        headers={"X-Dashboard-Password": "server-only-password"},
    )
    assert response.status_code == 409
    assert "TRADING_MODE=PAPER" in response.json()["detail"]


def test_paper_worker_snapshot_reports_desired_state(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    bot.heartbeat("test-paper-worker")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "server-only-password"}
    )
    assert response.status_code == 200
    assert response.json()["paper_worker"]["status"] == "online"
    assert response.json()["paper_worker"]["desired_state"] == "RUNNING"



def test_market_api_requires_dashboard_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    response = TestClient(app).get("/api/market")
    assert response.status_code == 503


def test_market_api_returns_shared_server_stream(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
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
        "/api/market", headers={"X-Dashboard-Password": "server-only-password"}
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
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("TRADING_ENV", "TESTNET")
    monkeypatch.setenv("ALLOW_MAINNET", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    response = TestClient(app).post(
        "/api/paper-control",
        json={"action": "resume"},
        headers={"X-Dashboard-Password": "server-only-password"},
    )

    assert response.status_code == 200
    assert response.json()["desired_state"] == "RUNNING"
    with bot._connect() as connection:
        audit = connection.execute(
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
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))

    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "server-only-password"}
    )

    assert response.status_code == 200
    worker = response.json()["paper_worker"]
    assert worker["supervisor_status"] == "online"
    assert worker["worker_status"] == "starting"
    assert worker["audit"][0]["event_type"] == "WORKER_STARTED"


def test_dashboard_exposes_locked_strategy_and_risk_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    response = TestClient(app).get(
        "/api/dashboard", headers={"X-Dashboard-Password": "server-only-password"}
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



def test_paper_command_api_executes_and_lists_fifo_order(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    import time
    with bot._connect() as db:
        db.execute(
            """INSERT INTO bot_market_ticks
               (tick,symbol,price,bid,ask,data_source,received_at_ms)
               VALUES (0,'BTCUSDT','60000','59999','60001','MOCK_SYNTHETIC',?)""",
            (int(time.time() * 1000),),
        )
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("TRADING_ENV", "TESTNET")
    monkeypatch.setenv("ALLOW_MAINNET", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    client = TestClient(app)
    headers = {
        "X-Dashboard-Password": "server-only-password",
        "Idempotency-Key": "api-paper-buy-001",
    }
    response = client.post(
        "/api/commands",
        json={"side": "BUY", "symbol": "BTCUSDT", "quote_amount": "5", "confirmed": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert response.json()["result"]["fifo_status"] == "PASS"
    listed = client.get(
        "/api/commands", headers={"X-Dashboard-Password": "server-only-password"}
    )
    assert listed.status_code == 200
    assert listed.json()["commands"][0]["status"] == "COMPLETED"


def test_testnet_order_api_is_locked(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    response = TestClient(app).post(
        "/api/testnet/orders",
        headers={"X-Dashboard-Password": "server-only-password"},
    )
    assert response.status_code == 423
    assert "explicit operational approval" in response.json()["detail"]


def test_paper_command_api_requires_idempotency_and_confirmation(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    import time
    with bot._connect() as db:
        db.execute(
            """INSERT INTO bot_market_ticks
               (tick,symbol,price,bid,ask,data_source,received_at_ms)
               VALUES (0,'BTCUSDT','60000','59999','60001','MOCK_SYNTHETIC',?)""",
            (int(time.time() * 1000),),
        )
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    client = TestClient(app)
    response = client.post(
        "/api/commands",
        json={"side": "BUY", "symbol": "BTCUSDT", "quote_amount": "5"},
        headers={"X-Dashboard-Password": "server-only-password"},
    )
    assert response.status_code == 422
    response = client.post(
        "/api/commands",
        json={"side": "BUY", "symbol": "BTCUSDT", "quote_amount": "5", "confirmed": False},
        headers={
            "X-Dashboard-Password": "server-only-password",
            "Idempotency-Key": "missing-confirmation",
        },
    )
    assert response.status_code == 428



def test_emergency_stop_api_latches_without_closing_positions(monkeypatch, tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    import time
    with bot._connect() as db:
        db.execute(
            """INSERT INTO bot_market_ticks
               (tick,symbol,price,bid,ask,data_source,received_at_ms)
               VALUES (0,'BTCUSDT','60000','59999','60001','MOCK_SYNTHETIC',?)""",
            (int(time.time() * 1000),),
        )
    from mumin_trader.execution.command_plane import submit_paper_order
    submit_paper_order(
        database, idempotency_key="api-before-stop", side="BUY",
        symbol="BTCUSDT", quote_amount="5", confirmed=True,
    )
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("MUMIN_PAPER_DB_PATH", str(database))
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("TRADING_ENV", "TESTNET")
    monkeypatch.setenv("ALLOW_MAINNET", "false")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    response = TestClient(app).post(
        "/api/emergency-stop",
        json={"confirmed": True},
        headers={
            "X-Dashboard-Password": "server-only-password",
            "Idempotency-Key": "api-emergency-stop",
        },
    )
    assert response.status_code == 200
    assert response.json()["open_positions_closed"] is False
    with bot._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bot_positions").fetchone()[0] == 1
