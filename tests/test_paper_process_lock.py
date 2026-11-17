from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mumin_trader.execution.process_lock import paper_process_lock


def _child_code(database: Path, role: str) -> str:
    return f"""
from mumin_trader.execution.process_lock import paper_process_lock
try:
    with paper_process_lock({str(database)!r}, {role!r}):
        print("ACQUIRED", flush=True)
except RuntimeError:
    print("BLOCKED", flush=True)
"""


def test_second_process_cannot_acquire_same_role_lock(tmp_path: Path) -> None:
    database = tmp_path / "paper.db"
    with paper_process_lock(database, "worker"):
        result = subprocess.run(
            [sys.executable, "-c", _child_code(database, "worker")],
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "BLOCKED"


def test_supervisor_and_worker_roles_have_independent_locks(tmp_path: Path) -> None:
    database = tmp_path / "paper.db"
    with paper_process_lock(database, "supervisor"):
        with paper_process_lock(database, "worker"):
            pass


def test_kernel_releases_lock_after_process_crash(tmp_path: Path) -> None:
    database = tmp_path / "paper.db"
    code = f"""
import time
from mumin_trader.execution.process_lock import paper_process_lock
lock = paper_process_lock({str(database)!r}, "worker").acquire()
print("READY", flush=True)
time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        child.kill()
        child.wait(timeout=5)
        with paper_process_lock(database, "worker"):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
