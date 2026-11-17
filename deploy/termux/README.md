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

The installer installs Python dependencies, creates a private `.env` only if absent, generates a random dashboard token, installs the API, Paper engine, and six-hour verified local backup runit services, and registers a Termux:Boot startup script. It refuses unsafe trading configuration.

Dashboard: `http://127.0.0.1:8000/dashboard`
Health: `http://127.0.0.1:8000/health`

## Service controls
```sh
sv status mumin-api
sv status mumin-paper-engine
sv status mumin-paper-backup
sv status mumin-paper-watchdog
sv restart mumin-api
sv restart mumin-paper-engine
sv down mumin-paper-engine
sv up mumin-paper-engine
tail -f ~/.mumin-ai-trader/logs/api.log
tail -f ~/.mumin-ai-trader/logs/paper-engine.log
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
- This deployment is PAPER only. It does not submit Binance orders. Testnet order gates and Mainnet stay disabled.
- A ChatGPT-like LLM requires a separately selected model provider and possibly paid API usage; this installer does not invent or enable one.
