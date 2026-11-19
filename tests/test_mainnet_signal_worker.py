from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.config import Settings
from mumin_trader.execution.mainnet_signal_worker import (
    MainnetSignalLedger,
    MainnetSignalWorker,
)


def test_mainnet_signal_ledger_persists_and_deduplicates_candle_bucket(tmp_path) -> None:
    path = tmp_path / "signals.sqlite3"
    ledger = MainnetSignalLedger(path)
    candidate = SimpleNamespace(
        symbol="BTCUSDT",
        signal="BUY_CANDIDATE",
        strategy_score=88,
        last_price="50000",
        stop_loss_price="49750",
        take_profit_price="50625",
        filter_reasons=(),
    )

    assert ledger.record_candidate(candidate, "2026-11-19T10:00:00+00:00", "SHADOW_PROPOSAL")
    assert ledger.record_candidate(candidate, "2026-11-19T10:00:00+00:00", "SHADOW_PROPOSAL")

    with ledger._connect() as db:
        row = db.execute("SELECT * FROM mainnet_signal_events").fetchone()
    assert row["symbol"] == "BTCUSDT"
    assert row["disposition"] == "SHADOW_PROPOSAL"
    assert row["score"] == 88

    reopened = MainnetSignalLedger(path)
    assert reopened.desired_state() == "RUNNING"
    reopened.supervisor_heartbeat(123, 456)
    reopened.worker_heartbeat(456)
    reopened.set_desired_state("STOPPED", "test")
    assert reopened.desired_state() == "STOPPED"
    with reopened._connect() as db:
        runtime = db.execute("SELECT supervisor_pid,worker_pid,supervisor_heartbeat_at,worker_heartbeat_at FROM mainnet_worker_runtime WHERE id=1").fetchone()
        assert runtime["supervisor_pid"] == 123
        assert runtime["worker_pid"] == 456
        assert runtime["supervisor_heartbeat_at"] is not None
        assert runtime["worker_heartbeat_at"] is not None
        assert db.execute("SELECT COUNT(*) FROM mainnet_signal_events").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_mainnet_signal_worker_defaults_to_public_data_shadow_only(tmp_path) -> None:
    worker = MainnetSignalWorker(
        Settings(_env_file=None),
        ledger_path=tmp_path / "shadow.sqlite3",
        autonomous=False,
    )
    try:
        assert worker.execution is None
        assert worker.order_store is None
        assert str(worker._market_http.base_url) == "https://api.binance.com/"
        assert "X-MBX-APIKEY" not in worker._market_http.headers
    finally:
        await worker.market.close()
        await worker._market_http.aclose()


def test_mainnet_autonomous_worker_refuses_non_mainnet_configuration(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="separately gated Mainnet Spot environment"):
        MainnetSignalWorker(
            Settings(_env_file=None),
            ledger_path=tmp_path / "autonomous.sqlite3",
            autonomous=True,
        )
