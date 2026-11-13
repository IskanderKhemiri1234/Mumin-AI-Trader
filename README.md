# Mumin AI Autotrader — engineering foundation

Safety-first Python foundation for quantitative Spot-market research. This repository is **not yet an autonomous live-trading product**. The current Binance adapter supports public market data and signed account/commission reads. It intentionally does not expose order placement, withdrawals, margin, or futures endpoints.

## Safety defaults

- `TRADING_ENV=TESTNET`, `TRADING_MODE=PAPER`
- Mainnet requires several independent explicit flags and legal-eligibility acknowledgement.
- Live order submission is hard-disabled in this foundation.
- Never grant withdrawal permission to an API key.
- Never put API secrets in Git, issue trackers, logs, or chat.
- A 50 USDT capital budget is a configuration ceiling, not a promise of returns.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn mumin_trader.main:app --reload
```

Health endpoint: http://127.0.0.1:8000/health

## Run with Docker

```bash
cp .env.example .env
docker compose up --build -d
docker compose logs -f
```

## Tests

```bash
pytest -q
```

## Current capabilities

- Binance Spot public WebSocket market streams with reconnect and heartbeat.
- Liquidity and spread screening; candidates are observations, not trade signals.
- Continuous SQLite-backed Paper portfolio for BTCUSDT, ETHUSDT and BNBUSDT, with shared cash, per-symbol positions and portfolio-level exposure limits.
- Unauthenticated Binance Public Market Data snapshots (closed 1-minute candles plus bid/ask) with stale-data rejection; no API key is used for market data.
- Conservative simulated fills using bid/ask plus configured slippage and estimated fees; source-tagged market ticks, fills and portfolio snapshots.
- Deterministic risk and fee modules.
- FastAPI health endpoint and Docker configuration.

Paper fills are simulated and persisted in SQLite; they are not exchange fills. Docker Compose defaults the continuous Paper service to Binance public market data. Set `MUMIN_MARKET_DATA_SOURCE=MOCK_SYNTHETIC` in `.env` to use deterministic offline prices. Separate database files are used for synthetic and public-market sessions. BNB fee deduction is not enabled by this Paper engine; fees are estimated in USDT and are not account-verified.

## Not production-ready

No live orders, withdrawals, margin, or futures. Before any live deployment, add exchange filter validation, persistent order state, reconciliation, robust fee-asset conversion, high-water-mark drawdown controls, monitoring, secrets management, disaster recovery, integration tests, and jurisdiction/platform eligibility review. Backtests and paper results never guarantee future returns.

## GitHub helper

`scripts/init_and_push.py` uses an existing authenticated GitHub CLI session and is intended to create a **new** repository. Do not run it against an existing repository; this project is being managed directly in the dedicated repository.


## Mobile dashboard

A mobile-first, dependency-free dashboard is available at `/dashboard` when the FastAPI app is running. It reads the existing report exports in the browser: choose `summary.json`, `equity.csv`, and `trades.csv` from a downloaded GitHub Actions artifact. It displays portfolio metrics, an equity curve, open positions, and recent fills. The files are processed locally in the browser and are not uploaded to this service. The dashboard does not request or store API credentials and cannot submit orders.

The dashboard is a report viewer, not a hosted always-on trading service. GitHub Actions artifacts remain the source of truth for each run; the user must load the latest report files into the dashboard. Do not publish private account reports to a public repository or public Pages site.


## Verify Binance Spot Testnet credentials (read-only)

1. In GitHub repository Settings → Secrets and variables → Actions, create repository secrets named `BINANCE_API_KEY` and `BINANCE_API_SECRET` using **Binance Spot Testnet** credentials.
2. Open Actions → **Binance Spot Testnet Account Check** → Run workflow.
3. Review the log for the connection result and the Testnet USDT free/locked balance.

This workflow is read-only: it calls Spot Testnet ping and account endpoints only. It never submits an order. The configured strategy capital ceiling is 70 USDT, but the actual available Testnet balance is determined by the exchange account. Keep withdrawals, margin, futures, and Mainnet disabled. Do not paste credentials into issues, pull requests, source files, or chat.
