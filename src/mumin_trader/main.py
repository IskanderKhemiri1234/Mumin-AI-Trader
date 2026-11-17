from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
import os
import sqlite3
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config.config import get_settings
from mumin_trader.exchanges.binance.market_stream_service import MarketStreamService, WATCH_SYMBOLS
from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger
from mumin_trader.operations_chat import router as operations_chat_router
from mumin_trader.execution.command_plane import (
    CommandRejected,
    get_command,
    latch_emergency_stop,
    list_command_events,
    list_commands,
    submit_paper_order,
)

PROJECT_ROOT = (
    Path("/app")
    if Path("/app/dashboard").is_dir()
    else Path(__file__).resolve().parents[2]
)
DASHBOARD_FILE = PROJECT_ROOT / "dashboard" / "index.html"
DEFAULT_PAPER_DB = PROJECT_ROOT / "data" / "mumin_public_paper.db"


_market_stream = MarketStreamService()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    try:
        yield
    finally:
        await _market_stream.close()


app = FastAPI(
    title="Mumin AI Trader",
    version="0.2.0",
    description="Private, read-only trading operations dashboard and safety-first Spot research service.",
    lifespan=app_lifespan,
)

app.include_router(operations_chat_router)


def _dashboard_database() -> Path:
    configured = os.getenv("MUMIN_PAPER_DB_PATH", str(DEFAULT_PAPER_DB))
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path




class PaperControlRequest(BaseModel):
    action: Literal["pause", "resume"]


class PaperOrderCommand(BaseModel):
    side: Literal["BUY", "SELL"]
    symbol: str
    quantity: str | None = None
    quote_amount: str | None = None
    confirmed: bool = False


class EmergencyStopRequest(BaseModel):
    confirmed: bool = False


def _dashboard_access_token(x_dashboard_token: str | None) -> str:
    access_token = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
    if not access_token:
        raise HTTPException(
            status_code=503,
            detail="Dashboard access is not configured. Set DASHBOARD_ACCESS_TOKEN on the server.",
        )
    if not x_dashboard_token or not hmac.compare_digest(x_dashboard_token, access_token):
        raise HTTPException(status_code=401, detail="Dashboard access token is invalid.")
    return access_token


def _paper_worker_snapshot() -> dict[str, Any]:
    database = _dashboard_database()
    if not database.is_file():
        return {
            "status": "offline",
            "supervisor_status": "offline",
            "worker_status": "offline",
            "desired_state": "UNKNOWN",
            "heartbeat_at": None,
            "supervisor_heartbeat_at": None,
            "audit": [],
        }
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_runtime_control'"
        ).fetchone()
        if table is None:
            return {
                "status": "unmanaged",
                "supervisor_status": "unmanaged",
                "worker_status": "unmanaged",
                "desired_state": "UNKNOWN",
                "heartbeat_at": None,
                "supervisor_heartbeat_at": None,
                "audit": [],
            }
        columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(bot_runtime_control)"
            ).fetchall()
        }
        selected = [
            name for name in (
                "desired_state",
                "heartbeat_at",
                "worker_instance_id",
                "supervisor_heartbeat_at",
                "supervisor_instance_id",
                "worker_pid",
            ) if name in columns
        ]
        row = connection.execute(
            "SELECT " + ", ".join(selected) + " FROM bot_runtime_control WHERE id=1"
        ).fetchone()
        if row is None:
            return {
                "status": "unmanaged",
                "supervisor_status": "unmanaged",
                "worker_status": "unmanaged",
                "desired_state": "UNKNOWN",
                "heartbeat_at": None,
                "supervisor_heartbeat_at": None,
                "audit": [],
            }

        def heartbeat_age(value: str | None) -> float | None:
            if not value:
                return None
            try:
                stamp = datetime.fromisoformat(value)
                return (datetime.now(timezone.utc) - stamp).total_seconds()
            except (TypeError, ValueError):
                return None

        worker_heartbeat = row["heartbeat_at"] if "heartbeat_at" in columns else None
        supervisor_heartbeat = (
            row["supervisor_heartbeat_at"]
            if "supervisor_heartbeat_at" in columns else None
        )
        worker_age = heartbeat_age(worker_heartbeat)
        supervisor_age = heartbeat_age(supervisor_heartbeat)
        worker_online = worker_age is not None and -5 <= worker_age <= 90
        supervisor_online = supervisor_age is not None and -5 <= supervisor_age <= 90
        desired = str(row["desired_state"])
        if desired == "PAUSED":
            worker_status = "paused" if supervisor_online else "offline"
        elif worker_online:
            worker_status = "running"
        elif supervisor_online:
            worker_status = "starting"
        else:
            worker_status = "offline"

        audit_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_runtime_audit'"
        ).fetchone()
        audit = []
        if audit_table is not None:
            audit = [
                dict(item)
                for item in connection.execute(
                    """SELECT event_type, actor, desired_state, detail, created_at
                       FROM bot_runtime_audit ORDER BY audit_id DESC LIMIT 20"""
                ).fetchall()
            ]
        return {
            "status": "online" if supervisor_online or worker_online else "offline",
            "supervisor_status": "online" if supervisor_online else "offline",
            "worker_status": worker_status,
            "desired_state": desired,
            "heartbeat_at": worker_heartbeat,
            "heartbeat_age_seconds": worker_age,
            "supervisor_heartbeat_at": supervisor_heartbeat,
            "supervisor_heartbeat_age_seconds": supervisor_age,
            "supervisor_instance_id": (
                row["supervisor_instance_id"]
                if "supervisor_instance_id" in columns else None
            ),
            "worker_instance_id": (
                row["worker_instance_id"] if "worker_instance_id" in columns else None
            ),
            "worker_pid": row["worker_pid"] if "worker_pid" in columns else None,
            "audit": audit,
        }
    finally:
        connection.close()


def _strategy_settings_snapshot() -> dict[str, Any]:
    return {
        "symbols": list(WATCH_SYMBOLS),
        "strategy": {
            "profile": "MUMIN_3_OF_3_SWARM",
            "entry_consensus": "3/3",
            "required_agents": [
                "Liquidity Sweep",
                "Depth Imbalance",
                "Micro-Alpha Filter",
            ],
            "direction": "LONG_ONLY",
        },
        "risk_limits": {
            "initial_capital_usdt": "70.00",
            "max_trade_usdt": "5.00",
            "reserve_percent": "20.00",
            "max_open_positions": 2,
            "max_daily_loss_percent": "2.00",
            "max_drawdown_percent": "5.00",
            "stop_loss_percent": "0.50",
            "take_profit_percent": "1.25",
            "max_spread_bps": "8.00",
        },
        "risk_limits_locked": True,
        "approval_required_for_changes": True,
        "settings_mutation_enabled": False,
        "execution_authorized": False,
    }


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
            "SELECT symbol, quantity, entry_price, entry_gross, entry_fee, opened_at FROM bot_positions ORDER BY symbol"
        ).fetchall():
            quantity_decimal = Decimal(row["quantity"])
            entry = Decimal(row["entry_price"])
            entry_cost = Decimal(row["entry_gross"]) + Decimal(row["entry_fee"])
            mark_value = latest_prices.get(row["symbol"])
            mark = Decimal(str(mark_value)) if mark_value is not None else None
            market_value = quantity_decimal * mark if mark is not None else None
            positions.append({
                "symbol": row["symbol"],
                "quantity": float(quantity_decimal),
                "entry_price": float(entry),
                "mark_price": float(mark) if mark is not None else None,
                "market_value": float(market_value) if market_value is not None else None,
                "unrealized_pnl": float(market_value - entry_cost) if market_value is not None else None,
                "entry_fee": float(Decimal(row["entry_fee"])),
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
        decision_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_agent_decisions'"
        ).fetchone()
        strategy_decisions = []
        if decision_table is not None:
            strategy_decisions = [
                dict(row) for row in connection.execute(
                    """SELECT d.symbol,d.final_signal,d.action,d.liquidity_sweep_vote,
                              d.liquidity_sweep_reason,d.depth_imbalance_vote,
                              d.depth_imbalance_reason,d.micro_alpha_filter_vote,
                              d.micro_alpha_filter_reason,d.spread_bps,d.spread_pass,
                              d.agent_consensus,d.agent_consensus_pass,d.created_at
                       FROM bot_agent_decisions d
                       JOIN (SELECT symbol,MAX(decision_id) AS latest_id
                             FROM bot_agent_decisions GROUP BY symbol) latest
                         ON latest.latest_id=d.decision_id
                       ORDER BY d.symbol"""
                ).fetchall()
            ]

        accounting = reconcile_paper_ledger(connection)
        cash_decimal = Decimal(account["cash"])
        marked_value = sum(
            (
                Decimal(str(latest_prices[item["symbol"]])) * Decimal(str(item["quantity"]))
                for item in positions if latest_prices.get(item["symbol"]) is not None
            ),
            Decimal("0"),
        )
        marks_complete = all(latest_prices.get(item["symbol"]) is not None for item in positions)
        equity_decimal = cash_decimal + marked_value if marks_complete else None
        unrealized_value = accounting.get("unrealized_pnl_including_entry_fees")
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
                "cash": float(cash_decimal),
                "equity": float(equity_decimal) if equity_decimal is not None else None,
                "realized_pnl": float(account["realized_pnl"]),
                "unrealized_pnl": float(unrealized_value) if unrealized_value is not None else None,
                "accounting_status": accounting["status"],
                "daily_pnl": (
                    float(equity_decimal) - float(account["daily_start_equity"])
                    if equity_decimal is not None else None
                ),
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
            "accounting": accounting,
            "strategy_decisions": strategy_decisions,
            "risk_status": {
                "halted": bool(account["halted"]),
                "halt_reason": account["halt_reason"],
                "daily_pnl": (
                    float(equity_decimal) - float(account["daily_start_equity"])
                    if equity_decimal is not None else None
                ),
                "daily_loss_limit_percent": "2.00",
                "max_drawdown_percent": "5.00",
                "max_trade_usdt": "5.00",
                "cash_reserve_percent": "20.00",
            },
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
        "execution": "PAPER_ONLY; TESTNET_DISABLED; MAINNET_BLOCKED",
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


@app.get("/api/market")
async def market_snapshot(
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    await _market_stream.start()
    return await _market_stream.snapshot()


@app.get("/api/dashboard")
async def dashboard_snapshot(
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    snapshot = _read_dashboard_snapshot()
    worker = _paper_worker_snapshot()
    snapshot["paper_worker"] = worker
    worker_status = str(worker.get("worker_status", "offline")).lower()
    if snapshot.get("status") != "ready":
        snapshot["engine_status"] = "OFFLINE"
    elif snapshot.get("account", {}).get("halted"):
        snapshot["engine_status"] = "RISK_HALTED"
    else:
        snapshot["engine_status"] = {
            "running": "RUNNING",
            "paused": "PAUSED",
            "starting": "STARTING",
        }.get(worker_status, "OFFLINE")
    snapshot["strategy_settings"] = _strategy_settings_snapshot()
    snapshot["commands"] = (
        list_commands(_dashboard_database(), limit=20)
        if _dashboard_database().is_file() else []
    )
    snapshot["control_plane"] = {
        "read_only": False,
        "paper_order_submission": "CONFIRMED_PAPER_ONLY",
        "paper_engine_control": "AUTHENTICATED_PAUSE_RESUME_ONLY",
        "testnet_order_submission": "DISABLED_PENDING_EXPLICIT_OPERATIONAL_APPROVAL",
        "mainnet_order_submission": "BLOCKED",
        "execution_authorized": False,
        "available_actions": [
            "PAPER_BUY", "PAPER_SELL", "PAPER_PAUSE", "PAPER_RESUME"
        ],
    }
    return snapshot


@app.post("/api/commands")
async def create_paper_command(
    request: PaperOrderCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    if os.getenv("TRADING_MODE", "PAPER").upper() != "PAPER":
        raise HTTPException(status_code=409, detail="Order commands require TRADING_MODE=PAPER.")
    if os.getenv("TRADING_ENV", "TESTNET").upper() != "TESTNET":
        raise HTTPException(status_code=409, detail="Paper commands require TRADING_ENV=TESTNET.")
    if os.getenv("ALLOW_MAINNET", "false").lower() == "true" or os.getenv(
        "LIVE_TRADING_ENABLED", "false"
    ).lower() == "true":
        raise HTTPException(status_code=409, detail="Paper commands refuse live-enabled configuration.")
    if not _dashboard_database().is_file():
        raise HTTPException(status_code=409, detail="Paper ledger is unavailable.")
    try:
        return submit_paper_order(
            _dashboard_database(),
            idempotency_key=idempotency_key or "",
            side=request.side,
            symbol=request.symbol,
            quantity=request.quantity,
            quote_amount=request.quote_amount,
            confirmed=request.confirmed,
        )
    except CommandRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/emergency-stop")
async def paper_emergency_stop(
    request: EmergencyStopRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    if os.getenv("TRADING_MODE", "PAPER").upper() != "PAPER":
        raise HTTPException(status_code=409, detail="Emergency stop endpoint is Paper-only.")
    if os.getenv("TRADING_ENV", "TESTNET").upper() != "TESTNET":
        raise HTTPException(status_code=409, detail="Paper emergency stop requires TESTNET environment.")
    if os.getenv("ALLOW_MAINNET", "false").lower() == "true" or os.getenv(
        "LIVE_TRADING_ENABLED", "false"
    ).lower() == "true":
        raise HTTPException(status_code=409, detail="Emergency stop refuses live-enabled configuration.")
    if not _dashboard_database().is_file():
        raise HTTPException(status_code=409, detail="Paper ledger is unavailable.")
    try:
        return latch_emergency_stop(
            _dashboard_database(),
            idempotency_key=idempotency_key or "",
            confirmed=request.confirmed,
        )
    except CommandRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/commands")
async def read_paper_commands(
    limit: int = 50,
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    database = _dashboard_database()
    return {"commands": list_commands(database, limit) if database.is_file() else []}


@app.get("/api/commands/{command_id}")
async def read_paper_command(
    command_id: str,
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    database = _dashboard_database()
    command = get_command(database, command_id) if database.is_file() else None
    if command is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    return command


@app.get("/api/commands/{command_id}/events")
async def read_paper_command_events(
    command_id: str,
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    database = _dashboard_database()
    if not database.is_file():
        raise HTTPException(status_code=404, detail="Command not found.")
    command = get_command(database, command_id)
    if command is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    return {"events": list_command_events(database, command_id)}


@app.post("/api/testnet/orders")
async def blocked_testnet_order(
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    raise HTTPException(
        status_code=423,
        detail="Binance Testnet order submission is locked pending a separate explicit operational approval.",
    )


@app.post("/api/paper-control")
async def control_paper_worker(
    request: PaperControlRequest,
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    _dashboard_access_token(x_dashboard_token)
    if os.getenv("TRADING_MODE", "PAPER").upper() != "PAPER":
        raise HTTPException(status_code=409, detail="Paper controls require TRADING_MODE=PAPER.")
    if os.getenv("TRADING_ENV", "TESTNET").upper() != "TESTNET":
        raise HTTPException(status_code=409, detail="Paper controls require TRADING_ENV=TESTNET.")
    if os.getenv("ALLOW_MAINNET", "false").lower() == "true" or os.getenv(
        "LIVE_TRADING_ENABLED", "false"
    ).lower() == "true":
        raise HTTPException(status_code=409, detail="Paper controls refuse live-enabled environments.")

    database = _dashboard_database()
    if not database.is_file():
        raise HTTPException(status_code=409, detail="Paper ledger is unavailable; worker is not running.")
    connection = sqlite3.connect(database, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_runtime_control'"
        ).fetchone()
        if table is None:
            raise HTTPException(
                status_code=409,
                detail="Paper worker does not support remote pause/resume yet.",
            )
        columns = {
            item["name"] for item in connection.execute(
                "PRAGMA table_info(bot_runtime_control)"
            ).fetchall()
        }
        selected = [
            name for name in ("desired_state", "heartbeat_at", "supervisor_heartbeat_at",
                              "worker_instance_id")
            if name in columns
        ]
        row = connection.execute(
            "SELECT " + ", ".join(selected) + " FROM bot_runtime_control WHERE id=1"
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=409,
                detail="Paper worker manager does not support remote control yet.",
            )

        def heartbeat_is_live(value: str | None) -> bool:
            if not value:
                return False
            try:
                stamp = datetime.fromisoformat(value)
                age = (datetime.now(timezone.utc) - stamp).total_seconds()
                return -5 <= age <= 90
            except (TypeError, ValueError):
                return False

        worker_live = (
            heartbeat_is_live(row["heartbeat_at"])
            if "heartbeat_at" in columns else False
        )
        supervisor_live = (
            heartbeat_is_live(row["supervisor_heartbeat_at"])
            if "supervisor_heartbeat_at" in columns else False
        )
        if not supervisor_live and not worker_live:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Paper worker heartbeat is stale and supervisor heartbeat is stale; "
                    "refusing to queue a control request."
                ),
            )

        desired = "PAUSED" if request.action == "pause" else "RUNNING"
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """UPDATE bot_runtime_control SET desired_state=?, requested_at=? WHERE id=1""",
            (desired, now),
        )
        audit_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bot_runtime_audit'"
        ).fetchone()
        if audit_table is not None:
            connection.execute(
                """INSERT INTO bot_runtime_audit
                   (event_type, actor, desired_state, worker_instance_id, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "CONTROL_REQUESTED",
                    "dashboard_authenticated",
                    desired,
                    row["worker_instance_id"] if "worker_instance_id" in columns else None,
                    f"action={request.action}",
                    now,
                ),
            )
        connection.commit()
        return {
            "status": "accepted",
            "desired_state": desired,
            "message": (
                "Persistent supervisor will start or stop the Paper worker "
                "at its next process-control interval."
            ),
            "execution_authorized": False,
        }
    finally:
        connection.close()
