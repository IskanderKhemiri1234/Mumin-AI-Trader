from __future__ import annotations

from fastapi.testclient import TestClient

from mumin_trader.main import app
from scripts.run_continuous_paper import ContinuousPaperBot


def test_render_demo_api_exposes_mock_paper_ledger(tmp_path, monkeypatch):
    database = tmp_path / "render-demo.sqlite3"
    monkeypatch.setenv("MUMIN_DB_PATH", str(database))
    bot = ContinuousPaperBot(str(database))
    for _ in range(12):
        bot.step()

    client = TestClient(app)
    health = client.get("/health").json()
    status = client.get("/api/demo/status").json()
    trades = client.get("/api/demo/trades").json()
    equity = client.get("/api/demo/equity").json()

    assert health["market_data_source"] == "MOCK_SYNTHETIC"
    assert health["live_orders_enabled"] is False
    assert status["engine_status"] == "running"
    assert status["mode"] == "PAPER_ONLY"
    assert status["tick"] == 12
    assert trades["market_data_source"] == "MOCK_SYNTHETIC"
    assert trades["live_orders_enabled"] is False
    assert trades["count"] >= 1
    assert all(item["data_source"] == "MOCK_SYNTHETIC" for item in trades["trades"])
    assert equity["count"] == 12
