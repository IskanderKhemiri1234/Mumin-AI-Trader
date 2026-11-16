from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from run_continuous_paper import ContinuousPaperBot, PUBLIC_SOURCE, SYNTHETIC_SOURCE

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLL_SECONDS = 2.0
TERMINATE_GRACE_SECONDS = 12.0


class PaperWorkerSupervisor:
    """Persistent process supervisor for the isolated Paper worker."""

    def __init__(
        self,
        database: str,
        interval_seconds: float,
        data_source: str,
        restart_initial_seconds: float = 2.0,
        restart_max_seconds: float = 60.0,
    ) -> None:
        self.database = database
        self.interval_seconds = interval_seconds
        self.data_source = data_source
        self.restart_initial_seconds = restart_initial_seconds
        self.restart_max_seconds = restart_max_seconds
        self.bot = ContinuousPaperBot(database)
        self.instance_id = f"paper-supervisor-{os.getpid()}"
        self.child: subprocess.Popen[bytes] | None = None
        self.stop_requested = False
        self.restart_at = 0.0
        self.restart_delay = restart_initial_seconds

    def request_stop(self, _signum: int, _frame: object) -> None:
        self.stop_requested = True

    def _desired_state(self) -> str:
        with self.bot._connect() as db:
            row = db.execute(
                "SELECT desired_state FROM bot_runtime_control WHERE id=1"
            ).fetchone()
        if row is None:
            raise RuntimeError("Paper worker desired-state row is missing.")
        return str(row["desired_state"])

    def _record(self, event: str, state: str, detail: str = "") -> None:
        worker_id = f"paper-{self.child.pid}" if self.child is not None else None
        self.bot.record_runtime_audit(
            event_type=event,
            actor=self.instance_id,
            desired_state=state,
            worker_instance_id=worker_id,
            detail=detail[:500],
        )

    def _start_child(self, desired_state: str) -> None:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_continuous_paper.py"),
            "--db",
            self.database,
            "--interval-seconds",
            str(self.interval_seconds),
            "--data-source",
            self.data_source,
        ]
        self.child = subprocess.Popen(command, cwd=PROJECT_ROOT)
        self.bot.supervisor_heartbeat(self.instance_id, self.child.pid)
        self._record("WORKER_STARTED", desired_state, f"pid={self.child.pid}")
        print(
            f"Paper worker started under supervisor; pid={self.child.pid}, "
            f"source={self.data_source}.",
            flush=True,
        )

    def _stop_child(self, desired_state: str, reason: str) -> None:
        child = self.child
        if child is None:
            return
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        return_code = child.returncode
        self.child = None
        self.bot.supervisor_heartbeat(self.instance_id, None)
        self._record(
            "WORKER_STOPPED",
            desired_state,
            f"reason={reason}; exit_code={return_code}",
        )
        print(
            f"Paper worker stopped by supervisor; reason={reason}, exit={return_code}.",
            flush=True,
        )

    def run(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.request_stop)
        self.bot.record_runtime_audit(
            "SUPERVISOR_STARTED",
            self.instance_id,
            desired_state=self._desired_state(),
            detail=f"pid={os.getpid()}",
        )
        print(
            f"Paper supervisor online; database={self.database}; "
            f"poll={POLL_SECONDS}s; exchange orders are not enabled.",
            flush=True,
        )
        try:
            while not self.stop_requested:
                desired = self._desired_state()
                if self.child is not None and self.child.poll() is not None:
                    exit_code = self.child.returncode
                    self._record("WORKER_EXITED", desired, f"exit_code={exit_code}")
                    self.child = None
                    self.restart_at = time.monotonic() + self.restart_delay
                    self.restart_delay = min(
                        self.restart_delay * 2, self.restart_max_seconds
                    )
                    self.bot.supervisor_heartbeat(self.instance_id, None)
                    print(
                        f"Paper worker exited unexpectedly ({exit_code}); "
                        f"restart delay={self.restart_delay}s.",
                        flush=True,
                    )

                if desired == "PAUSED":
                    self._stop_child(desired, "dashboard_pause_request")
                elif desired == "RUNNING" and self.child is None:
                    if time.monotonic() >= self.restart_at:
                        self._start_child(desired)
                        self.restart_delay = self.restart_initial_seconds

                self.bot.supervisor_heartbeat(
                    self.instance_id,
                    self.child.pid if self.child is not None and self.child.poll() is None else None,
                )
                time.sleep(POLL_SECONDS)
        finally:
            self._stop_child("PAUSED", "supervisor_shutdown")
            self.bot.record_runtime_audit(
                "SUPERVISOR_STOPPED",
                self.instance_id,
                desired_state=self._desired_state(),
                detail="graceful shutdown",
            )
            self.bot.supervisor_heartbeat(self.instance_id, None)
            print("Paper supervisor stopped cleanly.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Persistent process supervisor for the isolated Paper worker."
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument(
        "--data-source",
        choices=(SYNTHETIC_SOURCE, PUBLIC_SOURCE),
        default=PUBLIC_SOURCE,
    )
    parser.add_argument("--restart-initial-seconds", type=float, default=2.0)
    parser.add_argument("--restart-max-seconds", type=float, default=60.0)
    args = parser.parse_args()
    if (
        args.interval_seconds <= 0
        or args.restart_initial_seconds <= 0
        or args.restart_max_seconds < args.restart_initial_seconds
    ):
        parser.error("Intervals and restart backoff values must be positive and ordered.")
    PaperWorkerSupervisor(
        database=args.db,
        interval_seconds=args.interval_seconds,
        data_source=args.data_source,
        restart_initial_seconds=args.restart_initial_seconds,
        restart_max_seconds=args.restart_max_seconds,
    ).run()


if __name__ == "__main__":
    main()
