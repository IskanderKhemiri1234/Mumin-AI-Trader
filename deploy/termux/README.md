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

The installer installs Python dependencies, creates a private `.env` only if absent, generates a random dashboard token, installs runit services, and registers a Termux:Boot startup script. It refuses unsafe trading configuration.

Dashboard: `http://127.0.0.1:8000/dashboard`
Health: `http://127.0.0.1:8000/health`

## Service controls
```sh
sv status mumin-api
sv status mumin-paper-engine
sv restart mumin-api
sv restart mumin-paper-engine
sv down mumin-paper-engine
sv up mumin-paper-engine
tail -f ~/.mumin-ai-trader/logs/api.log
tail -f ~/.mumin-ai-trader/logs/paper-engine.log
```

SQLite remains at `data/mumin_public_paper.db`. Back it up before replacing/reinstalling Termux. Never delete `data/` during updates.

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
