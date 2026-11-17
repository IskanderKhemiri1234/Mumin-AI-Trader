from __future__ import annotations

import hmac
import os
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "mumin_public_paper.db"
router = APIRouter(tags=["local-operations-chat"])
ZERO = Decimal("0")
HEARTBEAT_LIMIT_SECONDS = 90


class OperationsChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


def normalize_message(message: str) -> str:
    value = message.strip().lower()
    value = value.translate(str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه",
        "ؤ": "و", "ئ": "ي", "ـ": "",
    }))
    return re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE).strip()


def classify_intent(message: str) -> str:
    value = normalize_message(message)
    groups = {
        "alerts": (
            "تنبيه", "التنبيهات", "انذار", "تحذير", "alert", "alerts", "warning",
            "warnings", "issue", "issues", "مشكله", "مشاكل",
        ),
        "events": (
            "سجل الاحداث", "الاحداث", "السجل", "التاريخ", "event", "events",
            "audit", "history", "log", "logs", "activity",
        ),
        "engine": (
            "المحرك", "حاله التشغيل", "حاله المحرك", "نبض", "heartbeat",
            "engine", "worker", "supervisor", "runtime", "running", "online",
            "offline", "status", "حاله",
        ),
        "positions": (
            "المراكز", "مركز مفتوح", "المركز المفتوح", "الصفقات المفتوحه",
            "position", "positions", "open trade", "open trades", "holdings",
        ),
        "pnl": (
            "الربح", "الارباح", "الخساره", "الخسائر", "العائم", "المحقق",
            "pnl", "profit", "profits", "loss", "losses", "realized",
            "unrealized", "performance", "اداء",
        ),
        "portfolio": (
            "المحفظه", "الرصيد", "النقد", "قيمه الحساب", "قيمه المحفظه",
            "portfolio", "balance", "cash", "equity", "account", "funds",
        ),
    }
    for intent in ("alerts", "events", "engine", "positions", "pnl", "portfolio"):
        if any(term in value for term in groups[intent]):
            return intent
    return "overview"


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else ZERO
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _money(value: Decimal, language: str) -> str:
    return f"{value:,.4f} USDT" if language == "en" else f"{value:,.4f} USDT"


def _age_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None


def _table_names(db: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _rows(db: sqlite3.Connection, sql: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in db.execute(sql, (limit,)).fetchall()]
    except sqlite3.Error:
        return []


def read_operations_snapshot(database: Path) -> dict[str, Any]:
    if not database.is_file():
        return {
            "database_status": "missing", "engine": {"status": "offline"},
            "account": None, "positions": [], "alerts": [{
                "code": "LEDGER_MISSING", "severity": "warning",
                "message": "Paper SQLite ledger does not exist yet.",
            }], "events": [],
        }

    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        return {
            "database_status": "unavailable", "engine": {"status": "unknown"},
            "account": None, "positions": [], "alerts": [{
                "code": "LEDGER_UNAVAILABLE", "severity": "warning",
                "message": "Paper SQLite ledger could not be opened read-only.",
            }], "events": [],
        }

    tables = _table_names(db)
    required = {"bot_account", "bot_positions", "bot_fills", "bot_market_ticks"}
    if not required.issubset(tables):
        db.close()
        return {
            "database_status": "schema_unavailable", "engine": {"status": "unknown"},
            "account": None, "positions": [], "alerts": [{
                "code": "LEDGER_SCHEMA", "severity": "warning",
                "message": "The SQLite file does not contain the required Paper ledger tables.",
            }], "events": [],
        }

    account_row = db.execute("SELECT * FROM bot_account WHERE id=1").fetchone()
    if account_row is None:
        db.close()
        return {
            "database_status": "empty", "engine": {"status": "unknown"},
            "account": None, "positions": [], "alerts": [{
                "code": "ACCOUNT_MISSING", "severity": "warning",
                "message": "The Paper ledger has no account record yet.",
            }], "events": [],
        }

    account_raw = dict(account_row)
    marks = {
        str(row["symbol"]): _decimal(row["price"])
        for row in db.execute(
            """SELECT m.symbol,m.price FROM bot_market_ticks m
               JOIN (SELECT symbol,MAX(id) AS max_id FROM bot_market_ticks GROUP BY symbol) x
               ON x.max_id=m.id"""
        ).fetchall()
    }
    positions: list[dict[str, Any]] = []
    market_value = ZERO
    open_cost = ZERO
    marks_complete = True
    for row in db.execute(
        """SELECT symbol,quantity,entry_price,entry_gross,entry_fee,opened_at
           FROM bot_positions ORDER BY symbol"""
    ).fetchall():
        quantity = _decimal(row["quantity"])
        entry_gross = _decimal(row["entry_gross"])
        entry_fee = _decimal(row["entry_fee"])
        mark = marks.get(str(row["symbol"]))
        value = quantity * mark if mark is not None else None
        if value is None:
            marks_complete = False
        else:
            market_value += value
        open_cost += entry_gross + entry_fee
        positions.append({
            "symbol": str(row["symbol"]), "quantity": str(quantity),
            "entry_price": str(_decimal(row["entry_price"])),
            "mark_price": str(mark) if mark is not None else None,
            "market_value": str(value) if value is not None else None,
            "unrealized_pnl": str(value - entry_gross - entry_fee) if value is not None else None,
            "opened_at": row["opened_at"],
        })

    cash = _decimal(account_raw.get("cash"))
    realized = _decimal(account_raw.get("realized_pnl"))
    fees = _decimal(account_raw.get("fees_paid"))
    equity = cash + market_value if marks_complete else None
    unrealized = market_value - open_cost if marks_complete else None

    engine: dict[str, Any] = {
        "status": "offline", "supervisor_status": "offline",
        "worker_status": "offline", "desired_state": "UNKNOWN",
        "heartbeat_age_seconds": None, "supervisor_heartbeat_age_seconds": None,
    }
    if "bot_runtime_control" in tables:
        runtime = db.execute("SELECT * FROM bot_runtime_control WHERE id=1").fetchone()
        if runtime is not None:
            runtime_data = dict(runtime)
            worker_age = _age_seconds(runtime_data.get("heartbeat_at"))
            supervisor_age = _age_seconds(runtime_data.get("supervisor_heartbeat_at"))
            worker_online = worker_age is not None and -5 <= worker_age <= HEARTBEAT_LIMIT_SECONDS
            supervisor_online = supervisor_age is not None and -5 <= supervisor_age <= HEARTBEAT_LIMIT_SECONDS
            desired = str(runtime_data.get("desired_state", "UNKNOWN"))
            worker_status = (
                "paused" if desired == "PAUSED" and supervisor_online
                else "running" if worker_online
                else "starting" if supervisor_online
                else "offline"
            )
            engine = {
                "status": "online" if supervisor_online or worker_online else "offline",
                "supervisor_status": "online" if supervisor_online else "offline",
                "worker_status": worker_status, "desired_state": desired,
                "heartbeat_age_seconds": worker_age,
                "supervisor_heartbeat_age_seconds": supervisor_age,
                "worker_pid": runtime_data.get("worker_pid"),
            }

    alerts: list[dict[str, str]] = []
    if engine["status"] == "offline":
        alerts.append({
            "code": "ENGINE_OFFLINE", "severity": "warning",
            "message": "Paper engine or supervisor heartbeat is missing or stale.",
        })
    if bool(account_raw.get("halted", False)):
        alerts.append({
            "code": "RISK_HALTED", "severity": "critical",
            "message": str(account_raw.get("halt_reason") or "Paper risk halt is active."),
        })
    if not marks_complete:
        alerts.append({
            "code": "MARKS_INCOMPLETE", "severity": "warning",
            "message": "At least one open position has no market mark; total equity is incomplete.",
        })

    audit = _rows(
        db,
        """SELECT event_type,actor,desired_state,detail,created_at
           FROM bot_runtime_audit ORDER BY audit_id DESC LIMIT ?"""
    ) if "bot_runtime_audit" in tables else []
    command_events = _rows(
        db,
        """SELECT e.event_type,e.status,e.detail,e.created_at,c.command_type,c.symbol
           FROM bot_command_events e
           LEFT JOIN bot_commands c ON c.command_id=e.command_id
           ORDER BY e.event_id DESC LIMIT ?"""
    ) if {"bot_command_events", "bot_commands"}.issubset(tables) else []
    events = (audit + command_events)
    events.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    events = events[:12]

    reconciliation: dict[str, Any] = {"status": "UNAVAILABLE", "issues": []}
    try:
        from mumin_trader.execution.paper_reconciliation import reconcile_paper_ledger
        reconciliation = reconcile_paper_ledger(db)
    except (sqlite3.Error, KeyError, ValueError, InvalidOperation):
        reconciliation = {"status": "UNAVAILABLE", "issues": ["Ledger reconciliation could not be completed."]}
    if reconciliation.get("status") not in {"PASS", "INCOMPLETE"}:
        alerts.append({
            "code": "LEDGER_RECONCILIATION", "severity": "critical",
            "message": "Paper ledger reconciliation reported a mismatch.",
        })

    closed = db.execute("SELECT COUNT(*) FROM bot_fills WHERE side='SELL'").fetchone()[0]
    db.close()
    return {
        "database_status": "ready",
        "engine": engine,
        "account": {
            "initial_cash": str(_decimal(account_raw.get("initial_cash"))),
            "cash": str(cash), "equity": str(equity) if equity is not None else None,
            "realized_pnl": str(realized),
            "unrealized_pnl": str(unrealized) if unrealized is not None else None,
            "fees_paid": str(fees), "closed_trades": int(closed),
            "open_positions": len(positions), "tick_count": int(account_raw.get("tick_count") or 0),
            "halted": bool(account_raw.get("halted", False)),
        },
        "positions": positions,
        "reconciliation": {
            "status": reconciliation.get("status", "UNAVAILABLE"),
            "issues": list(reconciliation.get("issues", []))[:5],
        },
        "alerts": alerts,
        "events": events,
    }


def _reply(intent: str, language: str, snapshot: dict[str, Any]) -> str:
    ar = language == "ar"
    account = snapshot.get("account")
    engine = snapshot.get("engine") or {}
    if snapshot.get("database_status") != "ready" or account is None:
        return (
            "لم أتمكن من قراءة دفتر Paper بعد. تحقق من مسار SQLite وتشغيل المحرك."
            if ar else
            "I cannot read the Paper ledger yet. Check the SQLite path and engine startup."
        )

    cash = _money(_decimal(account["cash"]), language)
    equity = _money(_decimal(account["equity"]), language) if account["equity"] is not None else ("غير مكتملة" if ar else "incomplete")
    realized = _money(_decimal(account["realized_pnl"]), language)
    unrealized = _money(_decimal(account["unrealized_pnl"]), language) if account["unrealized_pnl"] is not None else ("غير متاح" if ar else "unavailable")
    status_ar = {"running": "يعمل", "paused": "متوقف مؤقتاً", "starting": "قيد البدء", "offline": "غير متصل"}
    status_en = {"running": "running", "paused": "paused", "starting": "starting", "offline": "offline"}
    engine_status = status_ar.get(engine.get("worker_status"), "غير معروف") if ar else status_en.get(engine.get("worker_status"), "unknown")

    if intent == "engine":
        if ar:
            return f"حالة محرك Paper: {engine_status}. المشرف: {engine.get('supervisor_status', 'unknown')}. الحالة المطلوبة: {engine.get('desired_state', 'UNKNOWN')}."
        return f"Paper engine: {engine_status}. Supervisor: {engine.get('supervisor_status', 'unknown')}. Desired state: {engine.get('desired_state', 'UNKNOWN')}."
    if intent == "positions":
        if not snapshot["positions"]:
            return "لا توجد مراكز مفتوحة حالياً." if ar else "There are no open positions."
        lines = [f"{p['symbol']}: {p['quantity']} @ {p['entry_price']}; mark {p['mark_price'] or 'N/A'}; unrealized {p['unrealized_pnl'] or 'N/A'} USDT" for p in snapshot["positions"]]
        return ("المراكز المفتوحة:\n" if ar else "Open positions:\n") + "\n".join(lines)
    if intent == "pnl":
        return (
            f"الربح المحقق: {realized}. الربح/الخسارة العائمة: {unrealized}. الرسوم المسجلة: {_money(_decimal(account['fees_paid']), language)}."
            if ar else
            f"Realized PnL: {realized}. Unrealized PnL: {unrealized}. Recorded fees: {_money(_decimal(account['fees_paid']), language)}."
        )
    if intent == "portfolio":
        return (
            f"ملخص دفتر Paper: قيمة المحفظة {equity}، النقد {cash}، عدد المراكز {account['open_positions']}، والصفقات المغلقة {account['closed_trades']}."
            if ar else
            f"Paper portfolio: equity {equity}, cash {cash}, open positions {account['open_positions']}, closed trades {account['closed_trades']}."
        )
    if intent == "alerts":
        alerts = snapshot.get("alerts", [])
        if not alerts:
            return "لا توجد تنبيهات حالياً وفق البيانات المقروءة." if ar else "No alerts are currently detected in the available data."
        return ("التنبيهات الحالية:\n" if ar else "Current alerts:\n") + "\n".join(
            f"[{item['severity']}] {item['message']}" for item in alerts
        )
    if intent == "events":
        events = snapshot.get("events", [])
        if not events:
            return "لا توجد أحداث مسجلة في الجداول المتاحة." if ar else "No events are recorded in the available audit tables."
        return ("أحدث الأحداث:\n" if ar else "Latest events:\n") + "\n".join(
            f"{item.get('created_at', '')} · {item.get('event_type', 'EVENT')} · {item.get('detail', '')}"
            for item in events[:8]
        )
    if ar:
        return (
            f"أهلاً، أنا Mumin Operations Chat المحلي. أقرأ دفتر SQLite بوضع القراءة فقط. "
            f"المحرك {engine_status}، قيمة المحفظة {equity}، النقد {cash}، "
            f"المحقق {realized}، العائم {unrealized}، والتنبيهات {len(snapshot.get('alerts', []))}. "
            "اسألني عن المحرك أو المحفظة أو المراكز أو الأرباح أو التنبيهات أو سجل الأحداث."
        )
    return (
        f"Hello, I am the local Mumin Operations Chat. I read the SQLite ledger in read-only mode. "
        f"Engine {engine_status}; equity {equity}; cash {cash}; realized PnL {realized}; "
        f"unrealized PnL {unrealized}; alerts {len(snapshot.get('alerts', []))}. "
        "Ask about engine, portfolio, positions, PnL, alerts, or event history."
    )


@router.post("/api/operations-chat")
async def operations_chat(
    request: OperationsChatRequest,
    x_dashboard_token: str | None = Header(default=None, alias="X-Dashboard-Token"),
) -> dict[str, Any]:
    access_token = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
    if not access_token:
        raise HTTPException(status_code=503, detail="Dashboard access is not configured.")
    if not x_dashboard_token or not hmac.compare_digest(x_dashboard_token, access_token):
        raise HTTPException(status_code=401, detail="Dashboard access token is invalid.")

    database_value = os.getenv("MUMIN_PAPER_DB_PATH", str(DEFAULT_DATABASE))
    database = Path(database_value)
    if not database.is_absolute():
        database = PROJECT_ROOT / database
    message = request.message.strip()
    language = "ar" if re.search(r"[\u0600-\u06ff]", message) else "en"
    intent = classify_intent(message)
    snapshot = read_operations_snapshot(database)
    return {
        "language": language,
        "intent": intent,
        "answer": _reply(intent, language, snapshot),
        "read_only": True,
        "external_services_used": False,
        "snapshot": snapshot,
    }
