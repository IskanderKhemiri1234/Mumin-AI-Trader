from __future__ import annotations

import hmac
import json
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
from mumin_trader.exchanges.binance.account_readonly import (
    BinanceReadOnlyAccountError,
    read_binance_spot_account,
)
from mumin_trader.exchanges.binance.order_store import SQLiteOrderStore
from mumin_trader.exchanges.binance.spot_mainnet_execution import (
    BinanceSpotMainnetExecution,
    SpotExecutionError,
    SpotOrderOutcomeUnknown,
)
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
_mainnet_executor: BinanceSpotMainnetExecution | None = None
_mainnet_store: SQLiteOrderStore | None = None


def _get_mainnet_service() -> tuple[BinanceSpotMainnetExecution, SQLiteOrderStore]:
    global _mainnet_executor, _mainnet_store
    settings = get_settings()
    if not settings.MAINNET_SPOT_EXECUTION_ENABLED:
        raise HTTPException(status_code=423, detail="Mainnet Spot execution is locked by server configuration.")
    if _mainnet_executor is None or _mainnet_store is None:
        _mainnet_store = SQLiteOrderStore(settings.MAINNET_ORDER_DATABASE_URL)
        try:
            _mainnet_executor = BinanceSpotMainnetExecution(settings, _mainnet_store)
        except Exception:
            _mainnet_store = None
            raise
    return _mainnet_executor, _mainnet_store


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.MAINNET_SPOT_EXECUTION_ENABLED:
        try:
            executor, store = _get_mainnet_service()
            await executor.reconcile()
        except Exception:
            if _mainnet_store is not None:
                _mainnet_store.halt("STARTUP_RECONCILIATION_FAILED")
    try:
        yield
    finally:
        if _mainnet_executor is not None:
            await _mainnet_executor.close()
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


class MainnetLimitOrderCommand(BaseModel):
    side: Literal["BUY", "SELL"]
    symbol: str
    quantity: str
    price: str
    client_order_id: str
    confirmed: bool = False


class MainnetCancelOrderRequest(BaseModel):
    symbol: str
    confirmed: bool = False


class MainnetProtectedEntryCommand(BaseModel):
    symbol: str
    quantity: str
    working_price: str
    take_profit_price: str
    stop_price: str
    stop_limit_price: str
    list_client_order_id: str
    working_client_order_id: str
    pending_above_client_order_id: str
    pending_below_client_order_id: str
    confirmed: bool = False


def _require_dashboard_password(x_require_dashboard_password: str | None) -> str:
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        raise HTTPException(
            status_code=503,
            detail="Dashboard password is not configured. Set DASHBOARD_PASSWORD on the server.",
        )
    if len(password) < 12:
        raise HTTPException(
            status_code=503,
            detail="DASHBOARD_PASSWORD must contain at least 12 characters.",
        )
    if not x_require_dashboard_password or not hmac.compare_digest(x_require_dashboard_password, password):
        raise HTTPException(status_code=401, detail="Dashboard password is invalid.")
    return password


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
            "environment": "PAPER_ISOLATED",
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
                "environment": "PAPER_ISOLATED",
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
                "environment": "PAPER_ISOLATED",
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
            "environment": "PAPER_ISOLATED",
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


@app.get("/api/binance/account/read-only")
async def binance_readonly_account(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    api_key = os.getenv("BINANCE_ACCOUNT_READONLY_API_KEY", "")
    api_secret = os.getenv("BINANCE_ACCOUNT_READONLY_API_SECRET", "")
    if not api_key or not api_secret:
        raise HTTPException(
            status_code=409,
            detail="Binance read-only account credentials are not configured on the S21 server.",
        )
    try:
        return await read_binance_spot_account(api_key, api_secret)
    except BinanceReadOnlyAccountError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None



@app.get("/api/mainnet/status")
async def mainnet_status(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    settings = get_settings()
    if not settings.MAINNET_SPOT_EXECUTION_ENABLED:
        return {
            "environment": settings.TRADING_ENV.value,
            "mode": settings.TRADING_MODE.value,
            "status": "LOCKED",
            "execution_enabled": False,
            "host": "https://api.binance.com",
            "database": "separate_mainnet_spot_ledger",
            "available_actions": [],
        }
    _, store = _get_mainnet_service()
    halted, reason = store.is_halted()
    return {
        "environment": settings.TRADING_ENV.value,
        "mode": settings.TRADING_MODE.value,
        "status": "HALTED" if halted else "ENABLED",
        "execution_enabled": True,
        "host": "https://api.binance.com",
        "halted": halted,
        "halt_reason": reason,
        "unresolved_orders": len(store.unresolved_orders()),
        "available_actions": ["ACCOUNT", "RECONCILE", "LIMIT_ORDER", "CANCEL_ORDER", "EMERGENCY_STOP"],
    }


@app.get("/api/mainnet/account")
async def mainnet_account(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    executor, _ = _get_mainnet_service()
    try:
        account = await executor._account()
        return {
            "account_type": account.get("accountType"),
            "can_trade": account.get("canTrade"),
            "balances": account.get("balances", []),
            "update_time": account.get("updateTime"),
            "host": "https://api.binance.com",
        }
    except Exception:
        raise HTTPException(status_code=502, detail="Binance Mainnet account query failed.") from None


@app.post("/api/mainnet/reconcile")
async def mainnet_reconcile(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    executor, _ = _get_mainnet_service()
    try:
        return await executor.reconcile()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Mainnet reconciliation failed; the execution ledger has been halted pending manual review.",
        ) from None


@app.get("/api/mainnet/orders")
async def mainnet_orders(
    limit: int = 50,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    _, store = _get_mainnet_service()
    database = sqlite3.connect(store.database_path, timeout=5)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT client_order_id,symbol,side,quantity,price,state,exchange_order_id,executed_quantity,cumulative_quote_quantity,last_error,created_at,updated_at FROM spot_order_intents ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
        has_lists = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='spot_oco_order_lists'"
        ).fetchone()
        order_lists = []
        if has_lists:
            order_lists = [
                dict(row) for row in database.execute(
                    "SELECT list_client_order_id,symbol,side,quantity,state,order_list_id,working_client_order_id,created_at,updated_at,last_error FROM spot_oco_order_lists ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
            ]
        latest = database.execute(
            "SELECT open_orders_json,created_at,discrepancies_json FROM spot_reconciliation_snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        exchange_open_orders = json.loads(latest["open_orders_json"]) if latest else []
        return {
            "orders": [dict(row) for row in rows],
            "order_lists": order_lists,
            "exchange_open_orders": exchange_open_orders,
            "last_reconciliation": latest["created_at"] if latest else None,
            "discrepancies": json.loads(latest["discrepancies_json"]) if latest else [],
        }
    finally:
        database.close()


@app.get("/api/mainnet/trades")
async def mainnet_trades(
    limit: int = 100,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    executor, store = _get_mainnet_service()
    try:
        await executor.sync_trade_history()
    except Exception:
        raise HTTPException(status_code=503, detail="Mainnet trade history could not be reconciled.") from None
    database = sqlite3.connect(store.database_path, timeout=5)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT symbol,trade_id,order_id,price,quantity,quote_quantity,commission,commission_asset,commission_quote_value,is_buyer,trade_time_ms FROM spot_trade_fills ORDER BY trade_time_ms DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        ).fetchall()
        return {"trades": [dict(row) for row in rows]}
    finally:
        database.close()


@app.get("/api/mainnet/pnl")
async def mainnet_pnl(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    _, store = _get_mainnet_service()
    return store.realized_pnl_report(get_settings().QUOTE_ASSET)


@app.post("/api/mainnet/orders")
async def submit_mainnet_limit_order(
    request: MainnetLimitOrderCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit order confirmation is required.")
    if request.side == "BUY":
        raise HTTPException(
            status_code=409,
            detail="Unprotected BUY LIMIT orders are disabled. Use the atomic protected OPOCO entry workflow.",
        )
    if not idempotency_key or idempotency_key != request.client_order_id:
        raise HTTPException(status_code=400, detail="Idempotency-Key must match client_order_id.")
    executor, _ = _get_mainnet_service()
    try:
        result = await executor.submit_limit_order(
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            client_order_id=request.client_order_id,
        )
        return {"order": result, "execution": "BINANCE_SPOT_MAINNET"}
    except SpotOrderOutcomeUnknown as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except SpotExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None



@app.post("/api/mainnet/entries/protected")
async def submit_mainnet_protected_entry(
    request: MainnetProtectedEntryCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit protected-entry confirmation is required.")
    if not idempotency_key or idempotency_key != request.list_client_order_id:
        raise HTTPException(status_code=400, detail="Idempotency-Key must match list_client_order_id.")
    executor, _ = _get_mainnet_service()
    try:
        result = await executor.opoco.place_buy_opoco(
            symbol=request.symbol,
            quantity=request.quantity,
            working_price=request.working_price,
            take_profit_price=request.take_profit_price,
            stop_price=request.stop_price,
            stop_limit_price=request.stop_limit_price,
            list_client_order_id=request.list_client_order_id,
            working_client_order_id=request.working_client_order_id,
            pending_above_client_order_id=request.pending_above_client_order_id,
            pending_below_client_order_id=request.pending_below_client_order_id,
        )
        return {"order_list": result, "execution": "BINANCE_SPOT_MAINNET_PROTECTED_OPOCO"}
    except SpotOrderOutcomeUnknown as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except SpotExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.delete("/api/mainnet/orders/{client_order_id}")
async def cancel_mainnet_order(
    client_order_id: str,
    request: MainnetCancelOrderRequest,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit cancellation confirmation is required.")
    executor, _ = _get_mainnet_service()
    try:
        return await executor.cancel_order(symbol=request.symbol, client_order_id=client_order_id)
    except SpotOrderOutcomeUnknown as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except SpotExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None



@app.delete("/api/mainnet/order-lists/{list_client_order_id}")
async def cancel_mainnet_order_list(
    list_client_order_id: str,
    request: MainnetCancelOrderRequest,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit protective-order cancellation confirmation is required.")
    executor, _ = _get_mainnet_service()
    try:
        return await executor.opoco.cancel_order_list(
            symbol=request.symbol,
            list_client_order_id=list_client_order_id,
        )
    except SpotOrderOutcomeUnknown as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except SpotExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@app.post("/api/mainnet/emergency-stop")
async def mainnet_emergency_stop(
    request: EmergencyStopRequest,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    if not request.confirmed:
        raise HTTPException(status_code=428, detail="Explicit emergency-stop confirmation is required.")
    executor, _ = _get_mainnet_service()
    return await executor.emergency_stop()


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.TRADING_ENV.value,
        "mode": settings.TRADING_MODE.value,
        "execution": (
            "SPOT_MAINNET_LIMIT_ENABLED; PAPER_SEPARATE; TESTNET_SEPARATE"
            if settings.MAINNET_SPOT_EXECUTION_ENABLED
            else "PAPER_ONLY; TESTNET_DISABLED; MAINNET_BLOCKED"
        ),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "Mumin AI Trader",
        "status": "mainnet_spot_enabled" if get_settings().MAINNET_SPOT_EXECUTION_ENABLED else "paper_only",
        "market_data": "BINANCE_PUBLIC_SPOT",
        "execution": "BINANCE_SPOT_MAINNET_LIMIT" if get_settings().MAINNET_SPOT_EXECUTION_ENABLED else "SIMULATED_PAPER_ONLY",
        "account_link": "BINANCE_SPOT_MAINNET" if get_settings().MAINNET_SPOT_EXECUTION_ENABLED else "NONE",
        "dashboard": "/dashboard",
        "api": "/api/dashboard",
    }


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not DASHBOARD_FILE.is_file():
        raise HTTPException(status_code=503, detail="Dashboard asset is missing.")
    return FileResponse(DASHBOARD_FILE, media_type="text/html")


@app.get("/operations-chat.js", include_in_schema=False)
async def operations_chat_script() -> FileResponse:
    script = PROJECT_ROOT / "dashboard" / "operations-chat.js"
    if not script.is_file():
        raise HTTPException(status_code=503, detail="Operations chat asset is missing.")
    return FileResponse(script, media_type="application/javascript")


@app.get("/mainnet-operations.js", include_in_schema=False)
async def mainnet_operations_script() -> FileResponse:
    script = PROJECT_ROOT / "dashboard" / "mainnet-operations.js"
    if not script.is_file():
        raise HTTPException(status_code=503, detail="Mainnet dashboard asset is missing.")
    return FileResponse(script, media_type="application/javascript")


@app.get("/api/market")
async def market_snapshot(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    await _market_stream.start()
    return await _market_stream.snapshot()


@app.get("/api/dashboard")
async def dashboard_snapshot(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
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
    mainnet_enabled = get_settings().MAINNET_SPOT_EXECUTION_ENABLED
    mainnet_status = {"status": "LOCKED", "execution_enabled": False}
    if mainnet_enabled:
        _, mainnet_store = _get_mainnet_service()
        halted, halt_reason = mainnet_store.is_halted()
        mainnet_status = {
            "status": "HALTED" if halted else "ENABLED",
            "execution_enabled": True,
            "halted": halted,
            "halt_reason": halt_reason,
            "unresolved_orders": len(mainnet_store.unresolved_orders()),
        }
    snapshot["mainnet"] = mainnet_status
    snapshot["control_plane"] = {
        "read_only": False,
        "paper_order_submission": "CONFIRMED_PAPER_ONLY",
        "paper_engine_control": "AUTHENTICATED_PAUSE_RESUME_ONLY",
        "testnet_order_submission": "DISABLED_PENDING_EXPLICIT_OPERATIONAL_APPROVAL",
        "mainnet_order_submission": "CONFIRMED_LIMIT_ONLY" if mainnet_enabled else "BLOCKED",
        "execution_authorized": False,
        "available_actions": [
            "PAPER_BUY", "PAPER_SELL", "PAPER_PAUSE", "PAPER_RESUME"
        ] + (["MAINNET_LIMIT_ORDER", "MAINNET_CANCEL", "MAINNET_EMERGENCY_STOP"] if mainnet_enabled else []),
    }
    return snapshot


@app.post("/api/commands")
async def create_paper_command(
    request: PaperOrderCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
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
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
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
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    database = _dashboard_database()
    return {"commands": list_commands(database, limit) if database.is_file() else []}


@app.get("/api/commands/{command_id}")
async def read_paper_command(
    command_id: str,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    database = _dashboard_database()
    command = get_command(database, command_id) if database.is_file() else None
    if command is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    return command


@app.get("/api/commands/{command_id}/events")
async def read_paper_command_events(
    command_id: str,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    database = _dashboard_database()
    if not database.is_file():
        raise HTTPException(status_code=404, detail="Command not found.")
    command = get_command(database, command_id)
    if command is None:
        raise HTTPException(status_code=404, detail="Command not found.")
    return {"events": list_command_events(database, command_id)}


@app.post("/api/testnet/orders")
async def blocked_testnet_order(
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
    raise HTTPException(
        status_code=423,
        detail="Binance Testnet order submission is locked pending a separate explicit operational approval.",
    )


@app.post("/api/paper-control")
async def control_paper_worker(
    request: PaperControlRequest,
    x_require_dashboard_password: str | None = Header(default=None, alias="X-Dashboard-Password"),
) -> dict[str, Any]:
    _require_dashboard_password(x_require_dashboard_password)
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
