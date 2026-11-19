from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from config.config import Settings
from mumin_trader.exchanges.binance.spot_mainnet_execution import SpotExecutionError
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
    assert not ledger.record_candidate(candidate, "2026-11-19T10:00:00+00:00", "SHADOW_PROPOSAL")

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


def test_signal_submission_is_claimed_once_per_symbol_and_candle(tmp_path) -> None:
    ledger = MainnetSignalLedger(tmp_path / "one-attempt.sqlite3")
    candidate = SimpleNamespace(
        symbol="BTCUSDT", signal="BUY_CANDIDATE", strategy_score=90,
        last_price="50000", stop_loss_price="49750", take_profit_price="50625",
        filter_reasons=(),
    )
    bucket = "2026-11-19T10:00:00+00:00"
    assert ledger.record_candidate(candidate, bucket, "SHADOW_PROPOSAL")
    assert ledger.claim_submission("BTCUSDT", bucket, "mfirst-order-id")
    assert not ledger.claim_submission("BTCUSDT", bucket, "msecond-order-id")
    ledger.attach_order("BTCUSDT", bucket, "mfirst-order-id", "submitted")
    with ledger._connect() as db:
        row = db.execute(
            "SELECT disposition,order_list_client_id FROM mainnet_signal_events WHERE symbol='BTCUSDT' AND candle_bucket=?",
            (bucket,),
        ).fetchone()
    assert row["disposition"] == "ORDER_SUBMITTED"
    assert row["order_list_client_id"] == "mfirst-order-id"
    assert not ledger.record_candidate(candidate, bucket, "SHADOW_PROPOSAL")



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
        assert worker.scanner.allowed_symbols == {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"}
        assert worker.scanner.interval == "15m"
        assert worker._interval_milliseconds() == 900_000
        candle_open = int(datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        next_candle_open = int(datetime(2026, 11, 19, 10, 15, tzinfo=timezone.utc).timestamp() * 1000)
        assert worker._bucket(candle_open) == "2026-11-19T10:00:00+00:00"
        assert worker._bucket(next_candle_open) == "2026-11-19T10:15:00+00:00"
        assert str(worker._market_http.base_url) == "https://api.binance.com"
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


@pytest.mark.asyncio
async def test_mainnet_worker_refuses_strategy_exit_levels_outside_execution_policy(tmp_path) -> None:
    worker = MainnetSignalWorker(
        Settings(_env_file=None),
        ledger_path=tmp_path / "strategy-policy.sqlite3",
        autonomous=False,
    )
    worker.execution = object()
    candidate = SimpleNamespace(
        symbol="BTCUSDT",
        last_price=Decimal("50000"),
        stop_loss_price=Decimal("49750"),
        take_profit_price=Decimal("51000"),
    )
    try:
        with pytest.raises(SpotExecutionError, match="do not match the protected Mainnet risk policy"):
            await worker._submit_protected(candidate, "2026-11-19T10:00:00+00:00")
    finally:
        await worker.market.close()
        await worker._market_http.aclose()


def test_unknown_submission_outcome_is_durable_and_never_reclaimed(tmp_path) -> None:
    ledger = MainnetSignalLedger(tmp_path / "unknown-outcome.sqlite3")
    candidate = SimpleNamespace(
        symbol="BTCUSDT", signal="BUY_CANDIDATE", strategy_score=90,
        last_price="50000", stop_loss_price="49750", take_profit_price="50625",
        filter_reasons=(),
    )
    bucket = "2026-11-19T10:00:00+00:00"
    assert ledger.record_candidate(candidate, bucket, "SHADOW_PROPOSAL")
    assert ledger.claim_submission("BTCUSDT", bucket, "munknown-order-id")
    ledger.finish_submission("BTCUSDT", bucket, "OUTCOME_UNKNOWN", "mock timeout after submit")
    assert not ledger.claim_submission("BTCUSDT", bucket, "mretry-order-id")
    with ledger._connect() as db:
        row = db.execute(
            "SELECT disposition,order_list_client_id,detail FROM mainnet_signal_events WHERE symbol='BTCUSDT' AND candle_bucket=?",
            (bucket,),
        ).fetchone()
    assert row["disposition"] == "OUTCOME_UNKNOWN"
    assert row["order_list_client_id"] == "munknown-order-id"
    assert row["detail"] == "mock timeout after submit"
