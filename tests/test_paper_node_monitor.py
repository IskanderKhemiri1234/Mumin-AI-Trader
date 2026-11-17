from datetime import datetime, timedelta, timezone
from decimal import Decimal

from scripts.monitor_paper_node import inspect_node
from scripts.run_continuous_paper import PUBLIC_SOURCE, SYMBOLS, ContinuousPaperBot


def _healthy_node(tmp_path):
    database = tmp_path / "paper.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    now = datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc)
    received = int(now.timestamp() * 1000)
    prices = {symbol: Decimal("100") for symbol in SYMBOLS}
    quotes = {symbol: (Decimal("99.99"), Decimal("100.01")) for symbol in SYMBOLS}
    close_times = {symbol: received - 1_000 for symbol in SYMBOLS}
    bot.step(
        prices,
        source=PUBLIC_SOURCE,
        quotes=quotes,
        candle_close_times=close_times,
        received_at_ms=received,
        event_time=now,
    )
    bot.supervisor_heartbeat("test-supervisor", 12345)
    return database, now


def test_paper_node_monitor_passes_healthy_read_only_state(tmp_path):
    database, now = _healthy_node(tmp_path)

    result = inspect_node(database, now=now)

    assert result["overall"] == "PASS"
    assert result["execution_mode"] == "PAPER_ONLY"
    assert result["mainnet_authorized"] is False
    assert all(item["status"] in {"PASS", "WARN"} for item in result["checks"])


def test_paper_node_monitor_fails_stale_market_feed(tmp_path):
    database, now = _healthy_node(tmp_path)

    result = inspect_node(database, now=now + timedelta(minutes=10))

    feed = next(item for item in result["checks"] if item["name"] == "public_market_feed")
    assert feed["status"] == "FAIL"
    assert result["overall"] == "FAIL"


def test_paper_node_monitor_fails_missing_supervisor_heartbeat(tmp_path):
    database, now = _healthy_node(tmp_path)

    import sqlite3
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE bot_runtime_control SET supervisor_heartbeat_at=NULL WHERE id=1"
        )

    result = inspect_node(database, now=now)

    heartbeat = next(item for item in result["checks"] if item["name"] == "supervisor_heartbeat")
    assert heartbeat["status"] == "FAIL"
    assert result["overall"] == "FAIL"
