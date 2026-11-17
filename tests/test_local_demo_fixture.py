from pathlib import Path
import re


ASSET = (
    Path(__file__).resolve().parents[1]
    / "android" / "app" / "src" / "main" / "assets" / "dashboard" / "index.html"
)


def test_offline_demo_fixture_is_arithmetically_consistent():
    html = ASSET.read_text(encoding="utf-8")
    account_match = re.search(r"account:\{([^}]+)\}", html)
    assert account_match, "offline demo account fixture is missing"
    account = {
        key: float(value)
        for key, value in re.findall(
            r"(equity|cash|realized_pnl|daily_pnl|fees_paid|initial_cash|unrealized_pnl):([0-9.]+)",
            account_match.group(1),
        )
    }
    counts = {
        key: int(value)
        for key, value in re.findall(
            r"(closed_trades|wins|losses):([0-9]+)", account_match.group(1)
        )
    }

    fills_section = html.split("fills:[", 1)[1].split("positions:[", 1)[0]
    fill_rows = re.findall(
        r"side:'(BUY|SELL)'.*?fee_quote:'([0-9.]+)'.*?net_pnl:'(-?[0-9.]+)'",
        fills_section,
    )
    positions_section = html.split("positions:[", 1)[1].split("equity_history:[", 1)[0]
    position_rows = re.findall(
        r"market_value:([0-9.]+),unrealized_pnl:([0-9.]+)",
        positions_section,
    )

    assert len(fill_rows) == 4
    assert len(position_rows) == 2
    assert counts["closed_trades"] == sum(side == "SELL" for side, _, _ in fill_rows)
    assert counts["wins"] == sum(
        side == "SELL" and float(net) > 0 for side, _, net in fill_rows
    )
    assert counts["losses"] == sum(
        side == "SELL" and float(net) < 0 for side, _, net in fill_rows
    )
    assert abs(account["fees_paid"] - sum(float(fee) for _, fee, _ in fill_rows)) < 1e-9
    assert abs(account["realized_pnl"] - sum(
        float(net) for side, _, net in fill_rows if side == "SELL"
    )) < 1e-9
    market_value = sum(float(value) for value, _ in position_rows)
    unrealized = sum(float(value) for _, value in position_rows)
    assert abs(account["equity"] - (account["cash"] + market_value)) < 1e-9
    assert abs(account["unrealized_pnl"] - unrealized) < 1e-9
    assert abs(
        account["equity"]
        - (account["initial_cash"] + account["realized_pnl"] + account["unrealized_pnl"])
    ) < 1e-9
    assert "SYNTHETIC · NOT BINANCE" in html
    assert "DEMO · NOT RUNNING" in html
