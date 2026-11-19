from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from config.config import Settings
from mumin_trader.execution.mainnet_approval_audit import (
    begin_mainnet_approval,
    finish_mainnet_approval,
    list_mainnet_approval_events,
    prepare_mainnet_approval,
)


def _settings(path) -> Settings:
    return Settings(
        _env_file=None,
        MAINNET_ORDER_DATABASE_URL=f"sqlite:///{path}",
    )


def test_chat_approval_is_one_time_bound_to_exact_proposal_and_audited(tmp_path) -> None:
    settings = _settings(tmp_path / "mainnet-approvals.sqlite3")
    proposal = {
        "type": "protected_buy",
        "symbol": "BTCUSDT",
        "quantity": "0.0001",
        "working_price": "50000",
        "take_profit_price": "50625",
        "stop_price": "49750",
        "stop_limit_price": "49700",
        "list_client_order_id": "mumin-list-123",
        "working_client_order_id": "mumin-work-123",
        "pending_above_client_order_id": "mumin-tp-123",
        "pending_below_client_order_id": "mumin-sl-123",
    }

    approval_id = prepare_mainnet_approval(settings, proposal)
    assert len(approval_id) == 32
    assert begin_mainnet_approval(settings, approval_id, proposal) == proposal
    finish_mainnet_approval(settings, approval_id, "SUBMITTED", result={"orderListId": 123})

    events = list_mainnet_approval_events(settings, approval_id)
    assert [item["event_type"] for item in events] == [
        "PROPOSAL_PREPARED",
        "USER_CONFIRMED_SUBMISSION_STARTED",
        "SUBMITTED",
    ]

    with pytest.raises(ValueError, match="already consumed"):
        begin_mainnet_approval(settings, approval_id, proposal)


def test_chat_approval_rejects_changed_order_parameters(tmp_path) -> None:
    settings = _settings(tmp_path / "approval-tamper.sqlite3")
    proposal = {
        "type": "sell_limit",
        "side": "SELL",
        "symbol": "BTCUSDT",
        "quantity": "0.0001",
        "price": "50000",
        "client_order_id": "mumin-sell-123",
    }
    approval_id = prepare_mainnet_approval(settings, proposal)
    changed = {**proposal, "price": "49999"}

    with pytest.raises(ValueError, match="does not exactly match"):
        begin_mainnet_approval(settings, approval_id, changed)

    # A tampered attempt does not consume the valid approval.
    assert begin_mainnet_approval(settings, approval_id, proposal) == proposal


def test_expired_chat_approval_cannot_be_submitted(tmp_path) -> None:
    settings = _settings(tmp_path / "approval-expired.sqlite3")
    proposal = {
        "type": "sell_limit",
        "side": "SELL",
        "symbol": "BTCUSDT",
        "quantity": "0.0001",
        "price": "50000",
        "client_order_id": "mumin-sell-expired",
    }
    approval_id = prepare_mainnet_approval(settings, proposal)
    from mumin_trader.execution.mainnet_approval_audit import _database_path

    with sqlite3.connect(_database_path(settings)) as db:
        db.execute(
            "UPDATE mainnet_chat_approvals SET expires_at=? WHERE approval_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), approval_id),
        )

    with pytest.raises(ValueError, match="expired"):
        begin_mainnet_approval(settings, approval_id, proposal)

    with sqlite3.connect(_database_path(settings)) as db:
        state = db.execute(
            "SELECT state FROM mainnet_chat_approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()[0]
    assert state == "EXPIRED"
