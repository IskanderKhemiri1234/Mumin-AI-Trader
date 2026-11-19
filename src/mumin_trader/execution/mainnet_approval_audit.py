from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.config import Settings


def _database_path(settings: Settings) -> Path:
    value = settings.MAINNET_ORDER_DATABASE_URL
    if value.startswith("sqlite:///"):
        value = value.removeprefix("sqlite:///")
    elif "://" in value or value == ":memory:":
        raise ValueError("Mainnet approvals require the durable Mainnet SQLite database.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _canonical(proposal: dict[str, Any]) -> str:
    clean = {key: value for key, value in proposal.items() if key not in {"approval_id", "confirmed"}}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _connect(settings: Settings) -> sqlite3.Connection:
    db = sqlite3.connect(_database_path(settings), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA synchronous=FULL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mainnet_chat_approvals (
            approval_id TEXT PRIMARY KEY,
            proposal_type TEXT NOT NULL,
            proposal_json TEXT NOT NULL,
            proposal_sha256 TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            confirmed_at TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mainnet_chat_approval_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(approval_id) REFERENCES mainnet_chat_approvals(approval_id)
        )
        """
    )
    return db


def _event(
    db: sqlite3.Connection,
    approval_id: str,
    event_type: str,
    detail: dict[str, Any],
    actor: str,
) -> None:
    db.execute(
        """INSERT INTO mainnet_chat_approval_events
           (approval_id,event_type,actor,detail_json,created_at)
           VALUES (?,?,?,?,?)""",
        (
            approval_id,
            event_type,
            actor,
            json.dumps(detail, sort_keys=True, default=str),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def prepare_mainnet_approval(settings: Settings, proposal: dict[str, Any]) -> str:
    """Persist a short-lived immutable proposal; preparing never sends an exchange request."""
    approval_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    encoded = _canonical(proposal)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    db = _connect(settings)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT INTO mainnet_chat_approvals
               (approval_id,proposal_type,proposal_json,proposal_sha256,state,
                created_at,expires_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                approval_id,
                str(proposal.get("type", "unknown")),
                encoded,
                digest,
                "PENDING",
                now.isoformat(),
                (now + timedelta(minutes=5)).isoformat(),
            ),
        )
        _event(db, approval_id, "PROPOSAL_PREPARED", {"proposal_sha256": digest}, "operations_chat")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return approval_id


def begin_mainnet_approval(
    settings: Settings,
    approval_id: str,
    submitted_proposal: dict[str, Any],
) -> dict[str, Any]:
    """Atomically consume a pending approval and reject replay, expiry or payload changes."""
    if len(approval_id) != 32:
        raise ValueError("A valid approval ID is required.")
    encoded = _canonical(submitted_proposal)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    db = _connect(settings)
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM mainnet_chat_approvals WHERE approval_id=?",
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Approval does not exist.")
        if row["state"] != "PENDING":
            raise ValueError("Approval was already consumed or is not pending.")
        expires = datetime.fromisoformat(str(row["expires_at"]))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            db.execute(
                "UPDATE mainnet_chat_approvals SET state='EXPIRED' WHERE approval_id=?",
                (approval_id,),
            )
            _event(db, approval_id, "PROPOSAL_EXPIRED", {}, "mainnet_api")
            db.commit()
            raise ValueError("Approval has expired; prepare a new proposal.")
        if (
            not hmac.compare_digest(str(row["proposal_sha256"]), digest)
            or str(row["proposal_json"]) != encoded
        ):
            raise ValueError("Submitted order does not exactly match the approved proposal.")
        db.execute(
            """UPDATE mainnet_chat_approvals
               SET state='SUBMISSION_STARTED',confirmed_at=?
               WHERE approval_id=? AND state='PENDING'""",
            (datetime.now(timezone.utc).isoformat(), approval_id),
        )
        _event(db, approval_id, "USER_CONFIRMED_SUBMISSION_STARTED", {"proposal_sha256": digest}, "dashboard_authenticated_user")
        db.commit()
        return json.loads(str(row["proposal_json"]))
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()


def finish_mainnet_approval(
    settings: Settings,
    approval_id: str,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    if state not in {"SUBMITTED", "REJECTED", "OUTCOME_UNKNOWN"}:
        raise ValueError("Unsupported approval terminal state.")
    db = _connect(settings)
    try:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """UPDATE mainnet_chat_approvals
               SET state=?,result_json=?,last_error=?
               WHERE approval_id=? AND state='SUBMISSION_STARTED'""",
            (
                state,
                json.dumps(result or {}, sort_keys=True, default=str),
                error[:1000],
                approval_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Approval is not awaiting a submission result.")
        _event(
            db,
            approval_id,
            state,
            {"result": result or {}, "error": error[:500]},
            "mainnet_api",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_mainnet_approval_events(settings: Settings, approval_id: str) -> list[dict[str, Any]]:
    db = _connect(settings)
    try:
        rows = db.execute(
            """SELECT event_type,actor,detail_json,created_at
               FROM mainnet_chat_approval_events
               WHERE approval_id=? ORDER BY event_id""",
            (approval_id,),
        ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "actor": row["actor"],
                "detail": json.loads(row["detail_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        db.close()
