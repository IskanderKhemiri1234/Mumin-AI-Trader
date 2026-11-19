from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

from mumin_trader.execution.mainnet_signal_worker import MainnetSignalLedger, _sqlite_path  # noqa: E402


class MainnetWorkerSupervisor:
    """Runit-friendly supervisor with durable desired state and bounded crash backoff."""

    def __init__(
        self,
        *,
        interval_seconds: float = 60.0,
        restart_initial_seconds: float = 2.0,
        restart_max_seconds: float = 60.0,
        ledger_path: Path | None = None,
        run_for_seconds: float | None = None,
    ) -> None:
        if interval_seconds < 15:
            raise ValueError("Worker interval cannot be less than 15 seconds.")
        if restart_initial_seconds <= 0 or restart_max_seconds < restart_initial_seconds:
            raise ValueError("Restart backoff bounds are invalid.")
        configured = os.getenv("MAINNET_SIGNAL_DATABASE_URL", "sqlite:///./data/mumin_mainnet_signals.db")
        self.ledger = MainnetSignalLedger(ledger_path or _sqlite_path(configured))
        self.interval_seconds = interval_seconds
        self.restart_initial_seconds = restart_initial_seconds
        self.restart_max_seconds = restart_max_seconds
        self.run_for_seconds = run_for_seconds
        self.instance_id = f"mainnet-supervisor-{os.getpid()}"
        self.child: subprocess.Popen[bytes] | None = None
        self.child_started_at: float | None = None
        self.stop_requested = False
        self.restart_at = 0.0
        self.restart_delay = restart_initial_seconds

    def request_stop(self, _signum: int, _frame: object) -> None:
        self.stop_requested = True

    def _record(self, event: str, detail: dict[str, object]) -> None:
        self.ledger.audit(event, self.instance_id, detail)

    def _start_child(self) -> None:
        environment = os.environ.copy()
        existing_path = environment.get("PYTHONPATH", "")
        source_path = str(PROJECT_ROOT / "src")
        environment["PYTHONPATH"] = source_path + (os.pathsep + existing_path if existing_path else "")
        # The worker reads its interval from the inherited environment.
        environment["MAINNET_SIGNAL_INTERVAL_SECONDS"] = str(self.interval_seconds)
        command = [sys.executable, "-m", "mumin_trader.execution.mainnet_signal_worker"]
        self.child = subprocess.Popen(command, cwd=PROJECT_ROOT, env=environment)
        self.child_started_at = time.monotonic()
        self._record("MAINNET_SIGNAL_PROCESS_STARTED", {"pid": self.child.pid})
        print(f"Mainnet signal worker started; pid={self.child.pid}.", flush=True)

    def _stop_child(self, reason: str) -> None:
        child = self.child
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=12)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        code = child.returncode
        self.child = None
        self.child_started_at = None
        self.ledger.supervisor_heartbeat(os.getpid(), None)
        self._record("MAINNET_SIGNAL_PROCESS_STOPPED", {"reason": reason, "exit_code": code})
        print(f"Mainnet signal worker stopped; reason={reason}, exit={code}.", flush=True)

    def run(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.request_stop)
        lock_path = self.ledger.path.with_suffix(self.ledger.path.suffix + ".supervisor.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another Mainnet worker supervisor already holds the process lock.") from exc
            autonomous = _enabled(os.getenv("MAINNET_AUTONOMOUS_EXECUTION_ENABLED"))
            boot_resume = _enabled(os.getenv("MAINNET_AUTONOMOUS_BOOT_RESUME_ENABLED"))
            if autonomous and not boot_resume and self.ledger.desired_state() == "RUNNING":
                self.ledger.set_desired_state("STOPPED", self.instance_id)
                self._record(
                    "AUTONOMOUS_BOOT_RESUME_BLOCKED",
                    {"reason": "Fresh dashboard confirmation is required after supervisor restart."},
                )
            self._record(
                "MAINNET_SUPERVISOR_STARTED",
                {"pid": os.getpid(), "desired_state": self.ledger.desired_state(),
                 "autonomous": autonomous, "boot_resume_enabled": boot_resume},
            )
            started_at = time.monotonic()
            last_heartbeat = 0.0
            try:
                while not self.stop_requested:
                    if self.run_for_seconds is not None and time.monotonic() - started_at >= self.run_for_seconds:
                        break
                    desired = self.ledger.desired_state()
                    if self.child is not None and self.child.poll() is not None:
                        code = self.child.returncode
                        runtime = time.monotonic() - (self.child_started_at or time.monotonic())
                        self.child = None
                        self.child_started_at = None
                        self._record("MAINNET_SIGNAL_PROCESS_EXITED", {"exit_code": code, "runtime_seconds": round(runtime, 2)})
                        if runtime >= 60:
                            self.restart_delay = self.restart_initial_seconds
                        self.restart_at = time.monotonic() + self.restart_delay
                        self.restart_delay = min(self.restart_delay * 2, self.restart_max_seconds)
                    if desired != "RUNNING":
                        self._stop_child("durable_desired_state_" + desired.lower())
                        self.restart_delay = self.restart_initial_seconds
                        self.restart_at = 0
                    elif self.child is None and time.monotonic() >= self.restart_at:
                        self._start_child()
                    now = time.monotonic()
                    if now - last_heartbeat >= 10.0:
                        live_pid = self.child.pid if self.child is not None and self.child.poll() is None else None
                        self.ledger.supervisor_heartbeat(os.getpid(), live_pid)
                        last_heartbeat = now
                    time.sleep(2)
            finally:
                self._stop_child("supervisor_shutdown")
                self._record("MAINNET_SUPERVISOR_STOPPED", {"pid": os.getpid()})
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description="Persistent isolated Mainnet signal-worker supervisor.")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--restart-initial-seconds", type=float, default=2.0)
    parser.add_argument("--restart-max-seconds", type=float, default=60.0)
    parser.add_argument("--run-for-seconds", type=float)
    args = parser.parse_args()
    MainnetWorkerSupervisor(
        interval_seconds=args.interval_seconds,
        restart_initial_seconds=args.restart_initial_seconds,
        restart_max_seconds=args.restart_max_seconds,
        run_for_seconds=args.run_for_seconds,
    ).run()


if __name__ == "__main__":
    main()
