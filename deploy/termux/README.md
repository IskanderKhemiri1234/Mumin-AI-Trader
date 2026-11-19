# Mumin AI Trader — Termux Self-Hosted Node

This deployment runs the existing FastAPI dashboard and SQLite-backed Paper worker on Android. It is best-effort, not a guaranteed 24/7 server: Android can stop background processes, reboot, suspend the app, or lose network.

## Requirements
- Termux and its add-ons from the same trusted source (do not mix F-Droid and GitHub builds).
- Repository cloned to `~/Mumin-AI-Trader`.
- Reliable power and internet.
- Termux:Boot installed separately and opened once to grant startup permission.
- Optional Termux:API for `termux-wake-lock`.
- Disable battery optimization for Termux and Termux:Boot.

## Install
```sh
cd ~/Mumin-AI-Trader
bash deploy/termux/install.sh
```

The installer installs Python dependencies, creates a private `.env` only if absent, prompts you to choose a private dashboard password (minimum 12 characters), removes the legacy token setting, installs the API, isolated Paper engine, six-hour verified local backup, Paper watchdog, and a separate Mainnet signal-worker supervisor in Shadow-only mode, and registers a Termux:Boot startup script. It refuses unsafe trading configuration.

Dashboard: `http://127.0.0.1:8000/dashboard`\n\nThe dashboard accepts only `DASHBOARD_PASSWORD` from the private `.env` file. The former `DASHBOARD_ACCESS_TOKEN` setting and `X-Dashboard-Token` header are no longer accepted. Never put your password in GitHub or send it in chat. Passwords must be at least 12 characters.
Health: `http://127.0.0.1:8000/health`

## Service controls
```sh
sv status mumin-api
sv status mumin-paper-engine
sv status mumin-paper-backup
sv status mumin-paper-watchdog
sv status mumin-mainnet-signal-worker
sv restart mumin-api
sv restart mumin-paper-engine
sv down mumin-paper-engine
sv up mumin-paper-engine
tail -f ~/.mumin-ai-trader/logs/api.log
tail -f ~/.mumin-ai-trader/logs/paper-engine.log
tail -f ~/.mumin-ai-trader/logs/mainnet-signal-worker.log
```

SQLite remains at `data/mumin_public_paper.db`. A runit backup service creates an SQLite online-backup snapshot every six hours under `data/backups/`, verifies SQLite integrity and SHA-256, and retains 30 days. These snapshots are on the same phone and are not disaster-proof; periodically copy them to a separate trusted device/storage. Back up before replacing/reinstalling Termux. Never delete `data/` during updates.

To verify a backup, use the exact filename returned by the backup service:

```sh
cd ~/Mumin-AI-Trader
python scripts/backup_paper_ledger.py --db data/mumin_public_paper.db --verify data/backups/EXACT_BACKUP_FILENAME.sqlite3
```

## Read-only node health check

Run this from Termux whenever you want to verify the local Paper node:

```sh
cd ~/Mumin-AI-Trader
python scripts/monitor_paper_node.py --db data/mumin_public_paper.db
```

The command checks SQLite integrity, the fixed 70 USDT Paper capital, supervisor heartbeat, desired RUNNING state, freshness and completeness of the latest eight-symbol public market cycle, and FIFO accounting reconciliation. It is read-only and exits nonzero if a required operational check fails. It does not start or restart services.

A local watchdog service runs this read-only check every five minutes. When health changes to WARN or FAIL it attempts an Android notification through `termux-notification`; install the matching Termux:API app and grant notification permission to receive phone notifications. Without Termux:API, transitions are still recorded in `~/.mumin-ai-trader/logs/paper-watchdog.log`. The watchdog does not restart or modify the Paper engine.

## Local research archive and Walk-forward

After updating the repository, export a read-only archive from the phone's own Paper database:

```sh
cd ~/Mumin-AI-Trader
mkdir -p data/research
python scripts/export_paper_research_archive.py \
  --db data/mumin_public_paper.db \
  --archive data/research/paper-observations.jsonl.gz \
  --manifest data/research/manifest.json
python -c 'import json; m=json.load(open("data/research/manifest.json")); print(json.dumps({"cycles":m["observed_cycle_count"],"selection":m["selection"],"quality":m["quality"]}, indent=2))'
```

The exporter selects the latest complete eight-symbol snapshot for each minute and retains only the latest contiguous gap-free suffix. It never invents missing candles; earlier gaps and excluded snapshots are disclosed in the manifest. Formal Walk-forward is allowed only when `quality.replay_ready` is true and at least 120 cycles are available:

```sh
python scripts/run_paper_walk_forward.py \
  --archive data/research/paper-observations.jsonl.gz \
  --manifest data/research/manifest.json \
  --output data/research/walk-forward.json \
  --folds 3
```

If the archive is not ready or has fewer than 120 cycles, do not bypass the gate. Continue collecting and export a new manifest later. This Walk-forward is development/validation evidence, not the sealed Holdout and not a Mainnet authorization.

## Update
```sh
cd ~/Mumin-AI-Trader
git fetch origin
git pull --ff-only origin main
python -m pip install -e .
python scripts/validate_termux_runtime.py
sv restart mumin-api
sv restart mumin-paper-engine
```

## Limits and safety
- API binds to loopback only; it is not public on the internet.
- GitHub is source control, CI, PR review and audit trail. The live SQLite database is local to this phone; Actions is not a continuous host.
- Android Doze, force-stop, reboot, heat, storage and network failures can interrupt service. Termux:Boot and wake lock improve recovery but cannot guarantee uptime.
- No failover is configured.
- The existing Paper engine remains isolated and does not submit Binance orders.
- The separate Mainnet signal worker reads real Binance public Spot market data and applies the MarketScanner strategy to closed candles. It writes signals and audit events to `data/mumin_mainnet_signals.db`.
- Mainnet signal worker defaults to `SHADOW_ONLY`; it does not create a private Mainnet executor or submit orders unless `MAINNET_AUTONOMOUS_EXECUTION_ENABLED=true`, the separate exact confirmation phrase is configured, and all independent Mainnet execution gates and credentials are valid. Keep these disabled during Shadow validation.
- The Mainnet supervisor persists desired RUNNING/STOPPED state in its own SQLite ledger, uses a process lock and bounded restart backoff, and is registered with Termux:Boot. This improves restart recovery but Android cannot guarantee 24/7 uptime.
- Do not set autonomous execution until strategy shadow results, full mocked lifecycle tests, account permissions and independent review are complete.
- A ChatGPT-like LLM requires a separately selected model provider and possibly paid API usage; this installer does not invent or enable one.


## Change the dashboard password

Edit the private environment file on the phone:

```sh
cd ~/Mumin-AI-Trader
nano .env
```

Set `DASHBOARD_PASSWORD='your-own-password-at-least-12-characters'` and remove any `DASHBOARD_ACCESS_TOKEN` line. Save the file, then restart only the API so the new password takes effect:

```sh
sv restart mumin-api
```

The dashboard password is not a Binance credential. The API remains bound to localhost and Paper-only; changing this password does not restart or alter the Paper engine or its SQLite ledger.
