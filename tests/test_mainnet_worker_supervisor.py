from __future__ import annotations

from mumin_trader.execution.mainnet_signal_worker import MainnetSignalLedger
from scripts.mainnet_worker_supervisor import MainnetWorkerSupervisor


def _audit_events(ledger: MainnetSignalLedger) -> list[str]:
    with ledger._connect() as db:
        rows = db.execute(
            "SELECT event_type FROM mainnet_worker_audit ORDER BY id"
        ).fetchall()
    return [str(row["event_type"]) for row in rows]


def test_shadow_desired_state_survives_supervisor_restart(tmp_path, monkeypatch) -> None:
    database = tmp_path / "supervisor-shadow.sqlite3"
    monkeypatch.setenv("MAINNET_SIGNAL_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("MAINNET_AUTONOMOUS_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("MAINNET_AUTONOMOUS_BOOT_RESUME_ENABLED", "false")

    first = MainnetWorkerSupervisor(ledger_path=database, run_for_seconds=0)
    first.ledger.set_desired_state("RUNNING", "test")
    first.run()

    restarted = MainnetWorkerSupervisor(ledger_path=database, run_for_seconds=0)
    assert restarted.ledger.desired_state() == "RUNNING"
    restarted.run()
    assert restarted.ledger.desired_state() == "RUNNING"


def test_autonomous_mode_fails_stopped_after_supervisor_restart_by_default(tmp_path, monkeypatch) -> None:
    database = tmp_path / "supervisor-autonomous.sqlite3"
    monkeypatch.setenv("MAINNET_SIGNAL_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("MAINNET_AUTONOMOUS_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("MAINNET_AUTONOMOUS_BOOT_RESUME_ENABLED", "false")

    supervisor = MainnetWorkerSupervisor(ledger_path=database, run_for_seconds=0)
    supervisor.ledger.set_desired_state("RUNNING", "test")
    supervisor.run()

    assert supervisor.ledger.desired_state() == "STOPPED"
    events = _audit_events(supervisor.ledger)
    assert "AUTONOMOUS_BOOT_RESUME_BLOCKED" in events
    assert "MAINNET_SUPERVISOR_STARTED" in events
    assert "MAINNET_SUPERVISOR_STOPPED" in events


def test_explicit_autonomous_boot_resume_preserves_durable_desired_state(tmp_path, monkeypatch) -> None:
    database = tmp_path / "supervisor-resume.sqlite3"
    monkeypatch.setenv("MAINNET_SIGNAL_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("MAINNET_AUTONOMOUS_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("MAINNET_AUTONOMOUS_BOOT_RESUME_ENABLED", "true")

    supervisor = MainnetWorkerSupervisor(ledger_path=database, run_for_seconds=0)
    supervisor.ledger.set_desired_state("RUNNING", "test")
    supervisor.run()

    assert supervisor.ledger.desired_state() == "RUNNING"
    events = _audit_events(supervisor.ledger)
    assert "AUTONOMOUS_BOOT_RESUME_BLOCKED" not in events
