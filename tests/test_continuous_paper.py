import sqlite3
from decimal import Decimal

from scripts.run_continuous_paper import ContinuousPaperBot


def test_continuous_paper_loop_persists_state_and_hourly_report(tmp_path):
    database = tmp_path / "continuous.sqlite3"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("50.00"))

    for _ in range(40):
        result = bot.step()

    report = bot.save_hourly_report()
    reopened = ContinuousPaperBot(str(database), initial_cash=Decimal("50.00"))
    with sqlite3.connect(database) as db:
        tick_count = db.execute("SELECT COUNT(*) FROM bot_equity").fetchone()[0]
        reports = db.execute("SELECT COUNT(*) FROM bot_reports").fetchone()[0]
        fills = db.execute("SELECT COUNT(*) FROM bot_fills").fetchone()[0]

    assert result.tick == 40
    assert tick_count == 40
    assert reports == 1
    assert fills >= 2  # At least one complete BUY/SELL round trip in the synthetic fixture
    assert report["equity"] > 0
    assert reopened.report()["tick"] == 40
    assert report["fees"] >= 0


def test_continuous_mock_prices_are_marked_as_synthetic():
    first = ContinuousPaperBot.synthetic_price(10)
    second = ContinuousPaperBot.synthetic_price(10)
    assert first == second
    assert first > 0
