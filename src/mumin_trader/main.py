from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from config.config import get_settings

app = FastAPI(
    title="Mumin AI Autotrader — Mock Demo",
    version="0.2.0",
    description="Synthetic-market Paper trading demo. No exchange orders are possible.",
)


def _database_path() -> Path:
    return Path(os.getenv("MUMIN_DB_PATH", "/tmp/mumin_render_demo.sqlite3"))


def _read_rows(sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    database = _database_path()
    if not database.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Demo ledger temporarily unavailable: {exc}") from exc


@app.get("/health")
async def health() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.TRADING_ENV.value,
        "mode": "PAPER",
        "market_data_source": "MOCK_SYNTHETIC",
        "live_orders_enabled": False,
    }


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "Mumin AI Autotrader",
        "status": "mock_demo",
        "mode": "PAPER_ONLY",
        "market_data_source": "MOCK_SYNTHETIC",
        "live_orders_enabled": False,
        "endpoints": {
            "health": "/health",
            "status": "/api/demo/status",
            "trades": "/api/demo/trades",
            "equity": "/api/demo/equity",
            "docs": "/docs",
        },
    }


@app.get("/api/demo/status")
async def demo_status() -> dict[str, Any]:
    rows = _read_rows(
        """SELECT initial_cash, cash, realized_pnl, fees_paid, high_watermark,
                  tick_count, halted, halt_reason, updated_at
           FROM bot_account WHERE id=1"""
    )
    if not rows:
        return {
            "engine_status": "starting",
            "market_data_source": "MOCK_SYNTHETIC",
            "mode": "PAPER_ONLY",
            "live_orders_enabled": False,
            "tick": 0,
            "initial_cash": "50.00",
            "cash": "50.00",
            "equity": "50.00",
            "realized_pnl": "0",
            "daily_pnl": "0",
            "fees": "0",
            "closed_trades": 0,
            "positions": [],
        }

    account = rows[0]
    equity_rows = _read_rows(
        "SELECT equity, daily_pnl FROM bot_equity ORDER BY tick DESC LIMIT 1"
    )
    trade_rows = _read_rows(
        "SELECT COUNT(*) AS count FROM bot_fills WHERE side='SELL'"
    )
    positions = _read_rows(
        """SELECT p.symbol, p.quantity, p.entry_price, p.opened_at,
                  (SELECT m.price FROM bot_market_ticks m
                   WHERE m.symbol=p.symbol ORDER BY m.id DESC LIMIT 1) AS mark_price
           FROM bot_positions p ORDER BY p.symbol"""
    )
    latest = equity_rows[0] if equity_rows else {}
    return {
        "engine_status": "running",
        "market_data_source": "MOCK_SYNTHETIC",
        "mode": "PAPER_ONLY",
        "live_orders_enabled": False,
        "tick": account["tick_count"],
        "initial_cash": account["initial_cash"],
        "cash": account["cash"],
        "equity": latest.get("equity", account["cash"]),
        "realized_pnl": account["realized_pnl"],
        "daily_pnl": latest.get("daily_pnl", "0"),
        "fees": account["fees_paid"],
        "closed_trades": trade_rows[0]["count"] if trade_rows else 0,
        "halted": bool(account["halted"]),
        "halt_reason": account["halt_reason"],
        "updated_at": account["updated_at"],
        "positions": positions,
    }


@app.get("/api/demo/trades")
async def demo_trades(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
    trades = _read_rows(
        """SELECT fill_id, tick, symbol, side, quantity, reference_price, fill_price,
                  gross_quote, fee_quote, net_pnl, data_source, created_at
           FROM bot_fills ORDER BY fill_id DESC LIMIT ?""",
        (limit,),
    )
    return {
        "market_data_source": "MOCK_SYNTHETIC",
        "mode": "PAPER_ONLY",
        "live_orders_enabled": False,
        "count": len(trades),
        "trades": trades,
    }


@app.get("/api/demo/equity")
async def demo_equity(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    points = _read_rows(
        """SELECT tick, price, cash, equity, realized_pnl, daily_pnl,
                  signal, action, created_at
           FROM bot_equity ORDER BY tick DESC LIMIT ?""",
        (limit,),
    )
    points.reverse()
    return {
        "market_data_source": "MOCK_SYNTHETIC",
        "mode": "PAPER_ONLY",
        "live_orders_enabled": False,
        "count": len(points),
        "points": points,
    }
