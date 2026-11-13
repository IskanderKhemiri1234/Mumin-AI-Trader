from __future__ import annotations

import hmac
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from config.config import get_settings

PROJECT_ROOT = (
    Path("/app")
    if Path("/app/dashboard").is_dir()
    else Path(__file__).resolve().parents[2]
)
DASHBOARD_FILE = PROJECT_ROOT / "dashboard" / "index.html"
DEFAULT_PAPER_DB = PROJECT_ROOT / "data" / "mumin_public_paper.db"


app = FastAPI(
    title="Mumin AI Trader",
    version="0.2.0",
    description="Private, read-only trading operations dashboard and safety-first Spot research service.",
)


def _dashboard_database() -> Path:
    configured = os.getenv("MUMIN_PAPER_DB_PATH", str(DEFAULT_PAPER_DB))
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_dashboard_snapshot() -> dict[str, Any]:
    database = _dashboard_database()
    if not database.is_file():
        return {
            "status": "waiting_for_ledger",
            "message": "Paper ledger database has not been created yet.",
            "updated_at": None,
            "mode": "PAPER",
            "environment": "TESTNET",
            "market_data_source": None,
            "account": None,
            "positions": [],
            "fills": [],
            "equity_history": [],
        }

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        required = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "bot_account", "bot_positions", "bot_fills",
            "bot_equity", "bot_market_ticks",
        }
        if not expected.issubset(required):
            return {
                "status": "ledger_schema_unavailable",
                "message": "The selected database is not a Mumin Paper ledger.",
                "updated_at": None,
                "mode": "PAPER",
                "environment": "TESTNET",
                "market_data_source": None,
                "account": None,
                "positions": [],
                "fills": [],
                "equity_history": [],
            }

        account = connection.execute(
            "SELECT * FROM bot_account WHERE id=1"
        ).fetchone()
        if account is None:
            return {
                "status": "waiting_for_ledger",
                "message": "Paper ledger has no account snapshot yet.",
                "updated_at": None,
                "mode": "PAPER",
                "environment": "TESTNET",
                "market_data_source": None,
                "account": None,
                "positions": [],
                "fills": [],
                "equity_history": [],
            }

        latest_prices = {
            row["symbol"]: float(row["price"])
            for row in connection.execute(
                """SELECT m.symbol, m.price
                   FROM bot_market_ticks AS m
                   JOIN (
                       SELECT symbol, MAX(id) AS max_id
                       FROM bot_market_ticks GROUP BY symbol
                   ) AS latest ON latest.max_id=m.id"""
            ).fetchall()
        }
        positions = []
        for row in connection.execute(
            "SELECT symbol, quantity, entry_price, opened_at FROM bot_positions ORDER BY symbol"
        ).fetchall():
            quantity = float(row["quantity"])
            entry = float(row["entry_price"])
            mark = latest_prices.get(row["symbol"])
            positions.append({
                "symbol": row["symbol"],
                "quantity": quantity,
                "entry_price": entry,
                "mark_price": mark,
                "market_value": quantity * mark if mark is not None else None,
                "unrealized_pnl": quantity * (mark - entry) if mark is not None else None,
                "opened_at": row["opened_at"],
            })

        fill_rows = connection.execute(
            """SELECT tick, symbol, side, quantity, reference_price, fill_price,
                      gross_quote, fee_quote, net_pnl, data_source, created_at
               FROM bot_fills ORDER BY fill_id DESC LIMIT 50"""
        ).fetchall()
        fills = [dict(row) for row in fill_rows]

        equity_rows = connection.execute(
            """SELECT tick, cash, equity, realized_pnl, daily_pnl, action, created_at
               FROM bot_equity ORDER BY tick DESC LIMIT 240"""
        ).fetchall()
        equity_history = [dict(row) for row in reversed(equity_rows)]

        stats = connection.execute(
            """SELECT COUNT(*) AS closed_trades,
                      COALESCE(SUM(CASE WHEN CAST(net_pnl AS REAL)>0 THEN 1 ELSE 0 END),0) AS wins,
                      COALESCE(SUM(CASE WHEN CAST(net_pnl AS REAL)<0 THEN 1 ELSE 0 END),0) AS losses
               FROM bot_fills WHERE side='SELL'"""
        ).fetchone()
        source_row = connection.execute(
            "SELECT data_source FROM bot_market_ticks ORDER BY id DESC LIMIT 1"
        ).fetchone()

        cash = float(account["cash"])
        equity = cash + sum(
            float(item["quantity"]) * (latest_prices.get(item["symbol"]) or 0.0)
            for item in positions
        )
        return {
            "status": "ready",
            "message": "Read-only Paper ledger snapshot.",
            "updated_at": account["updated_at"],
            "mode": "PAPER",
            "environment": "TESTNET",
            "market_data_source": source_row["data_source"] if source_row else None,
            "account": {
                "tick": int(account["tick_count"]),
                "initial_cash": float(account["initial_cash"]),
                "cash": cash,
                "equity": equity,
                "realized_pnl": float(account["realized_pnl"]),
                "daily_pnl": equity - float(account["daily_start_equity"]),
                "fees_paid": float(account["fees_paid"]),
                "high_watermark": float(account["high_watermark"]),
                "closed_trades": int(stats["closed_trades"]),
                "wins": int(stats["wins"]),
                "losses": int(stats["losses"]),
                "halted": bool(account["halted"]),
                "halt_reason": account["halt_reason"],
                "open_positions": len(positions),
            },
            "positions": positions,
            "fills": fills,
            "equity_history": equity_history,
        }
    finally:
        connection.close()


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.TRADING_ENV.value,
        "mode": settings.TRADING_MODE.value,
        "execution": "disabled_in_foundation",
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "Mumin AI Trader",
        "status": "foundation",
        "dashboard": "/dashboard",
        "api": "/api/dashboard",
    }


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not DASHBOARD_FILE.is_file():
        raise HTTPException(status_code=503, detail="Dashboard asset is missing.")
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


@app.get("/api/dashboard")
async def dashboard_snapshot(
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    access_token = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
    if not access_token:
        raise HTTPException(
            status_code=503,
            detail="Dashboard access is not configured. Set DASHBOARD_ACCESS_TOKEN on the server.",
        )
    if not x_dashboard_token or not hmac.compare_digest(x_dashboard_token, access_token):
        raise HTTPException(status_code=401, detail="Dashboard access token is invalid.")
    return _read_dashboard_snapshot()
