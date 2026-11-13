from __future__ import annotations

import csv
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

DB_PATH = Path(os.getenv("MUMIN_DB_PATH", "data/mumin_mock_actions.sqlite3"))
REPORT_DIR = Path("reports")


def money(value: object) -> str:
    return f"{Decimal(str(value or '0')):.8f}"


def cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def export_csv(connection: sqlite3.Connection, query: str, destination: Path) -> int:
    cursor = connection.execute(query)
    rows = cursor.fetchall()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow([column[0] for column in cursor.description])
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    if not DB_PATH.is_file():
        raise SystemExit(f"SQLite ledger not found: {DB_PATH}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        sources = {
            row["data_source"]
            for row in db.execute("SELECT DISTINCT data_source FROM bot_market_ticks")
        }
        if not sources or sources != {"MOCK_SYNTHETIC"}:
            raise SystemExit(f"Refusing to publish a non-Mock or empty ledger: {sorted(sources)}")

        account = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
        if account is None:
            raise SystemExit("Paper account row is missing.")

        equity_row = db.execute(
            "SELECT equity,daily_pnl FROM bot_equity ORDER BY tick DESC LIMIT 1"
        ).fetchone()
        fills = db.execute(
            """SELECT fill_id,tick,symbol,side,quantity,reference_price,fill_price,
                      gross_quote,fee_quote,net_pnl,data_source,created_at
               FROM bot_fills ORDER BY fill_id DESC"""
        ).fetchall()
        sells = [row for row in fills if row["side"] == "SELL"]
        wins = sum(Decimal(row["net_pnl"]) > 0 for row in sells)
        losses = sum(Decimal(row["net_pnl"]) < 0 for row in sells)
        positions = db.execute(
            """SELECT p.symbol,p.quantity,p.entry_price,m.price AS mark_price
               FROM bot_positions p
               LEFT JOIN bot_market_ticks m
                 ON m.id=(SELECT MAX(x.id) FROM bot_market_ticks x WHERE x.symbol=p.symbol)
               ORDER BY p.symbol"""
        ).fetchall()

        tick_count = db.execute("SELECT COUNT(*) FROM bot_equity").fetchone()[0]
        market_ticks = db.execute("SELECT COUNT(*) FROM bot_market_ticks").fetchone()[0]
        reports = db.execute("SELECT COUNT(*) FROM bot_reports").fetchone()[0]

        export_csv(
            db,
            """SELECT fill_id,tick,symbol,side,quantity,reference_price,fill_price,
                      gross_quote,fee_quote,net_pnl,data_source,created_at
               FROM bot_fills ORDER BY fill_id""",
            REPORT_DIR / "trades.csv",
        )
        export_csv(
            db,
            """SELECT tick,price,cash,equity,realized_pnl,daily_pnl,signal,action,created_at
               FROM bot_equity ORDER BY tick""",
            REPORT_DIR / "equity.csv",
        )

        summary = {
            "market_data_source": "MOCK_SYNTHETIC",
            "mode": "PAPER_ONLY",
            "live_orders_enabled": False,
            "tick_count": int(account["tick_count"]),
            "market_ticks": int(market_ticks),
            "initial_cash": account["initial_cash"],
            "cash": account["cash"],
            "equity": equity_row["equity"] if equity_row else account["cash"],
            "realized_net_pnl": account["realized_pnl"],
            "daily_net_pnl": equity_row["daily_pnl"] if equity_row else "0",
            "fees_total": account["fees_paid"],
            "closed_trades": len(sells),
            "wins": wins,
            "losses": losses,
            "open_positions": [dict(row) for row in positions],
            "halted": bool(account["halted"]),
            "halt_reason": account["halt_reason"],
            "reports_saved": int(reports),
            "fills_saved": len(fills),
        }
        (REPORT_DIR / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
        )

        lines = [
            "## Mumin AI Trader — Mock Paper report",
            "",
            "> Synthetic prices only · Paper fills only · No Binance API keys · No live orders",
            "",
            "| Metric | Value |",
            "|---|---:|",
            "| Market data | MOCK_SYNTHETIC |",
            "| Engine mode | PAPER_ONLY |",
            f"| Cycles | {summary['tick_count']} |",
            f"| Starting balance | {money(summary['initial_cash'])} USDT |",
            f"| Cash | {money(summary['cash'])} USDT |",
            f"| Equity (incl. open positions) | {money(summary['equity'])} USDT |",
            f"| Realized net PnL | {money(summary['realized_net_pnl'])} USDT |",
            f"| Daily net PnL | {money(summary['daily_net_pnl'])} USDT |",
            f"| Estimated fees | {money(summary['fees_total'])} USDT |",
            f"| Closed trades | {len(sells)} |",
            f"| Wins / losses | {wins} / {losses} |",
            f"| Open positions | {len(positions)} |",
            f"| Risk halted | {'YES — ' + cell(account['halt_reason']) if account['halted'] else 'No'} |",
            "",
            "### Open positions",
            "",
            "| Symbol | Quantity | Entry price | Latest mark |",
            "|---|---:|---:|---:|",
        ]
        if positions:
            for row in positions:
                lines.append(
                    f"| {cell(row['symbol'])} | {cell(row['quantity'])} | "
                    f"{cell(row['entry_price'])} | {cell(row['mark_price'])} |"
                )
        else:
            lines.append("| None | — | — | — |")

        lines.extend([
            "",
            "### Recent simulated fills",
            "",
            "| Tick | Symbol | Side | Quantity | Fill price | Fee (USDT) | Net PnL (USDT) |",
            "|---:|---|---|---:|---:|---:|---:|",
        ])
        if fills:
            for row in fills[:15]:
                lines.append(
                    f"| {row['tick']} | {cell(row['symbol'])} | {cell(row['side'])} | "
                    f"{cell(row['quantity'])} | {cell(row['fill_price'])} | "
                    f"{cell(row['fee_quote'])} | {cell(row['net_pnl'])} |"
                )
        else:
            lines.append("| — | No fills in this ledger yet | — | — | — | — | — |")

        lines.extend([
            "",
            "### Downloadable files",
            "",
            "- trades.csv — all simulated fills",
            "- equity.csv — equity history",
            "- summary.json — machine-readable summary",
            "- SQLite database — full local paper ledger",
            "",
            "The GitHub Actions cache is best-effort continuity, not permanent storage. Download the artifact to retain a durable copy.",
        ])

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
