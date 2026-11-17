from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(path: Path) -> dict[str, Any]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=20) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required = {"bot_account", "bot_market_ticks", "bot_fills"}
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"Paper database schema is incomplete: {missing}")
        account = db.execute(
            "SELECT initial_cash, tick_count, halted FROM bot_account WHERE id=1"
        ).fetchone()
        if account is None:
            raise RuntimeError("Paper account row is missing.")
        if str(account[0]) != "70.00":
            raise RuntimeError(f"Unexpected Paper initial capital: {account[0]}")
        return {
            "sqlite_integrity": "ok",
            "initial_cash_usdt": str(account[0]),
            "tick_count": int(account[1]),
            "risk_halted": bool(account[2]),
            "market_rows": int(db.execute("SELECT COUNT(*) FROM bot_market_ticks").fetchone()[0]),
            "fill_rows": int(db.execute("SELECT COUNT(*) FROM bot_fills").fetchone()[0]),
        }


def create_backup(
    source_db: str | Path,
    backup_dir: str | Path,
    *,
    retention_days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = Path(source_db).resolve()
    destination = Path(backup_dir).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if retention_days < 1:
        raise ValueError("retention_days must be at least one.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    current = current.astimezone(timezone.utc)
    destination.mkdir(parents=True, exist_ok=True)

    stamp = current.strftime("%Y%m%dT%H%M%SZ")
    fd, temporary_name = tempfile.mkstemp(
        prefix=".paper-backup-", suffix=".sqlite3", dir=destination
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as origin:
            with sqlite3.connect(temporary, timeout=30) as target:
                origin.backup(target, pages=256, sleep=0.05)
                target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                target.commit()
        snapshot = _inspect(temporary)
        digest = _sha256(temporary)
        filename = f"mumin-paper-{stamp}-{digest[:12]}.sqlite3"
        final_db = destination / filename
        os.replace(temporary, final_db)
        manifest = {
            "schema_version": 1,
            "created_at_utc": current.isoformat(),
            "source_database": str(source),
            "backup_file": filename,
            "backup_sha256": digest,
            "backup_bytes": final_db.stat().st_size,
            "retention_days": retention_days,
            "execution_mode": "PAPER_ONLY",
            "mainnet_authorized": False,
            "snapshot": snapshot,
        }
        manifest_path = destination / f"{filename}.manifest.json"
        manifest_tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        manifest_tmp.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(manifest_tmp, manifest_path)
        cutoff = current - timedelta(days=retention_days)
        removed: list[str] = []
        for candidate in destination.glob("mumin-paper-*.sqlite3"):
            if candidate == final_db:
                continue
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
            except OSError:
                continue
            if modified < cutoff:
                candidate_manifest = destination / f"{candidate.name}.manifest.json"
                candidate.unlink(missing_ok=True)
                candidate_manifest.unlink(missing_ok=True)
                removed.append(candidate.name)
        return {**manifest, "manifest_file": manifest_path.name, "pruned_backups": removed}
    finally:
        temporary.unlink(missing_ok=True)


def verify_backup(backup_file: str | Path) -> dict[str, Any]:
    path = Path(backup_file).resolve()
    manifest_path = path.with_name(path.name + ".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("Backup or its manifest is missing.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = _sha256(path)
    if digest != manifest.get("backup_sha256"):
        raise RuntimeError("Backup SHA-256 does not match its manifest.")
    snapshot = _inspect(path)
    if snapshot != manifest.get("snapshot"):
        raise RuntimeError("Backup database checks do not match its manifest.")
    return {
        "status": "PASS",
        "backup_file": path.name,
        "backup_sha256": digest,
        "snapshot": snapshot,
        "restore_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and verify durable, read-only Paper SQLite backups."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--backup-dir", default="data/backups")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--verify")
    args = parser.parse_args()
    if args.verify:
        result = verify_backup(args.verify)
    else:
        result = create_backup(
            args.db, args.backup_dir, retention_days=args.retention_days
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
