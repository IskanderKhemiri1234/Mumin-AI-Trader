# Mumin AI Trader — Self-Hosted Android Operations Node

## Components
- GitHub: source of truth, reviewed changes, CI and release history.
- Mumin API: existing FastAPI dashboard, loopback-only.
- Paper Engine: existing `paper_worker_supervisor.py` supervising `run_continuous_paper.py`.
- Heartbeat: supervisor/worker heartbeats and audit events persisted in SQLite.
- Durable state: `data/mumin_public_paper.db` on the Android Termux filesystem.
- Local backups: a runit service makes SQLite online backups every six hours, verifies each backup with SQLite integrity and SHA-256, and retains 30 days under `data/backups/`. This is same-device redundancy, not disaster recovery; a second trusted storage destination is still required for device-loss protection.
- Free remote mirror: the existing GitHub `mumin-paper-ledger` branch stores scheduled Paper snapshots. A 15-minute GitHub Actions watchdog checks mirror freshness, SQLite integrity, the fixed 70 USDT capital and FIFO accounting, and opens/closes a GitHub issue on incidents. GitHub notifications depend on user notification settings. This monitors the mirror, not a live phone heartbeat.
- Recovery: runit restarts service processes; the supervisor restarts a crashed Paper child; Termux:Boot attempts service startup after device reboot.
- Mumin AI Chat: provider-backed assistant integration is a separate phase requiring an explicitly selected model provider and budget.

## Uptime contract
This is best-effort 24/7-style operation, not an SLA. Android power management, force-stop, reboot, network loss, heat, storage failure and add-on permissions can interrupt it. GitHub cannot keep a powered-off phone or stopped Android process alive.

GitHub Actions remains for CI and periodic checks only. It is not the live execution host or the owner of the live SQLite database.

## Safety contract
Service startup enforces `TRADING_ENV=TESTNET`, `TRADING_MODE=PAPER`, `ALLOW_MAINNET=false`, `LIVE_TRADING_ENABLED=false`, `TESTNET_ORDER_PREFLIGHT_ENABLED=false`, and `TESTNET_SPOT_EXECUTION_ENABLED=false`. The worker uses public market data and does not submit exchange orders.
