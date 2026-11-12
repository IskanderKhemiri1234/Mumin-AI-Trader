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
- Paper execution with quantity-step rounding, slippage and quote-denominated fee estimates.
- Deterministic risk and fee modules.
- FastAPI health endpoint and Docker configuration.

Paper fills are simulated and in-memory; they are not exchange fills. BNB fee deduction is not yet verified against actual asset balances and trade commissions.

## Not production-ready

No live orders, withdrawals, margin, or futures. Before any live deployment, add exchange filter validation, persistent order state, reconciliation, robust fee-asset conversion, high-water-mark drawdown controls, monitoring, secrets management, disaster recovery, integration tests, and jurisdiction/platform eligibility review. Backtests and paper results never guarantee future returns.

## GitHub helper

`scripts/init_and_push.py` uses an existing authenticated GitHub CLI session and is intended to create a **new** repository. Do not run it against an existing repository; this project is being managed directly in the dedicated repository.
