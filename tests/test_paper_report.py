from decimal import Decimal

from scripts.export_paper_report import load_snapshot, render_html, render_markdown
from scripts.run_continuous_paper import ContinuousPaperBot


def test_mobile_report_exports_paper_snapshot(tmp_path):
    database = tmp_path / "paper.db"
    bot = ContinuousPaperBot(str(database), initial_cash=Decimal("70.00"))
    result = bot.step()

    snapshot = load_snapshot(str(database))
    markdown = render_markdown(snapshot)
    page = render_html(snapshot)

    assert result.equity == Decimal("70.00")
    assert snapshot["initial_cash"] == Decimal("70.00")
    assert snapshot["tick"] == 1
    assert set(snapshot["market_prices"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
    assert snapshot["signals"]["BTCUSDT"] == "HOLD"
    assert "مراقبة السوق وإشارات Smart Sniper" in markdown
    assert "BTCUSDT" in page
    assert "Paper فقط" in page
    assert "0.50%" in page
    assert "صافي الخروج بعد رسوم الدخول والخروج والانزلاق السعري" in markdown
    assert "1.00%" in page
    assert "viewport-fit=cover" in page
    assert "nav aria-label=" in page
    assert "asset-grid" in page
    assert "prefers-reduced-motion" in page
    assert "لا تُرسل أوامر إلى Binance" in page
    assert "2.00%" in markdown
    assert "70.0000 USDT" in markdown
    assert "لا توجد أوامر Binance حقيقية" in markdown
