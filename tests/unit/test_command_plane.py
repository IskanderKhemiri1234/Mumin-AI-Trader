from datetime import datetime, timezone
from decimal import Decimal

import pytest

from mumin_trader.execution.command_plane import (
    CommandRejected,
    latch_emergency_stop,
    submit_paper_order,
)
from scripts.run_continuous_paper import ContinuousPaperBot


def _ledger(tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database))
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    with bot._connect() as db:
        db.execute(
            """INSERT INTO bot_market_ticks
               (tick,symbol,price,bid,ask,data_source,received_at_ms)
               VALUES (0,'BTCUSDT','60000','59999','60001','MOCK_SYNTHETIC',?)""",
            (now_ms,),
        )
    return database, bot


def test_manual_paper_buy_persists_fill_and_reconciles_fifo(tmp_path):
    database, _ = _ledger(tmp_path)
    result = submit_paper_order(
        database, idempotency_key="buy-1", side="BUY", symbol="BTCUSDT",
        quote_amount="5", confirmed=True,
    )
    assert result["status"] == "COMPLETED"
    assert result["result"]["fifo_status"] == "PASS"
    assert Decimal(result["result"]["fee_quote"]) > 0


def test_manual_paper_sell_closes_fifo_position_and_realizes_net_pnl(tmp_path):
    database, bot = _ledger(tmp_path)
    bought = submit_paper_order(
        database, idempotency_key="buy-2", side="BUY", symbol="BTCUSDT",
        quote_amount="5", confirmed=True,
    )
    quantity = bought["result"]["quantity"]
    sold = submit_paper_order(
        database, idempotency_key="sell-2", side="SELL", symbol="BTCUSDT",
        quantity=quantity, confirmed=True,
    )
    assert sold["status"] == "COMPLETED"
    assert sold["result"]["fifo_status"] == "PASS"
    assert Decimal(sold["result"]["net_pnl"]) < 0
    with bot._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bot_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM bot_fills").fetchone()[0] == 2


def test_idempotency_key_prevents_duplicate_paper_fill(tmp_path):
    database, bot = _ledger(tmp_path)
    kwargs = dict(
        idempotency_key="same-order", side="BUY", symbol="BTCUSDT",
        quote_amount="5", confirmed=True,
    )
    first = submit_paper_order(database, **kwargs)
    second = submit_paper_order(database, **kwargs)
    assert first["command_id"] == second["command_id"]
    assert second["duplicate"] is True
    with bot._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bot_fills").fetchone()[0] == 1


def test_idempotency_key_rejects_changed_payload(tmp_path):
    database, _ = _ledger(tmp_path)
    submit_paper_order(
        database, idempotency_key="same-key", side="BUY", symbol="BTCUSDT",
        quote_amount="5", confirmed=True,
    )
    with pytest.raises(CommandRejected, match="different payload"):
        submit_paper_order(
            database, idempotency_key="same-key", side="BUY", symbol="ETHUSDT",
            quote_amount="5", confirmed=True,
        )


def test_paper_order_requires_explicit_confirmation(tmp_path):
    database, bot = _ledger(tmp_path)
    with pytest.raises(CommandRejected, match="confirmation"):
        submit_paper_order(
            database, idempotency_key="not-confirmed", side="BUY",
            symbol="BTCUSDT", quote_amount="5", confirmed=False,
        )
    with bot._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM bot_fills").fetchone()[0] == 0


def test_sell_cannot_exceed_fifo_inventory(tmp_path):
    database, _ = _ledger(tmp_path)
    with pytest.raises(CommandRejected, match="exceeds available FIFO"):
        submit_paper_order(
            database, idempotency_key="oversell", side="SELL",
            symbol="BTCUSDT", quantity="0.01", confirmed=True,
        )



def test_emergency_stop_latches_entries_without_closing_positions(tmp_path):
    database, bot = _ledger(tmp_path)
    bought = submit_paper_order(
        database, idempotency_key="pre-stop-buy", side="BUY", symbol="BTCUSDT",
        quote_amount="5", confirmed=True,
    )
    result = latch_emergency_stop(
        database, idempotency_key="emergency-1", confirmed=True,
    )
    assert result["halted"] is True
    assert result["open_positions_closed"] is False
    with bot._connect() as db:
        account = db.execute("SELECT halted,halt_reason FROM bot_account WHERE id=1").fetchone()
        assert account["halted"] == 1
        assert account["halt_reason"] == "MANUAL_EMERGENCY_STOP"
        assert db.execute("SELECT COUNT(*) FROM bot_positions").fetchone()[0] == 1
    with pytest.raises(CommandRejected, match="risk halt"):
        submit_paper_order(
            database, idempotency_key="post-stop-buy", side="BUY", symbol="ETHUSDT",
            quote_amount="5", confirmed=True,
        )
    sold = submit_paper_order(
        database, idempotency_key="post-stop-sell", side="SELL", symbol="BTCUSDT",
        quantity=bought["result"]["quantity"], confirmed=True,
    )
    assert sold["status"] == "COMPLETED"



def test_manual_buy_is_blocked_when_paper_engine_is_paused(tmp_path):
    database, bot = _ledger(tmp_path)
    with bot._connect() as db:
        db.execute(
            "UPDATE bot_runtime_control SET desired_state='PAUSED' WHERE id=1"
        )
    with pytest.raises(CommandRejected, match="engine is paused"):
        submit_paper_order(
            database, idempotency_key="paused-buy", side="BUY",
            symbol="BTCUSDT", quote_amount="5", confirmed=True,
        )
