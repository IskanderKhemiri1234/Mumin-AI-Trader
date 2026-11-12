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
- Binance Spot Testnet combined WebSocket streams for trade ticks, best bid/ask, top-20 partial depth and 1-minute klines, with reconnect/backoff and REST bootstrap; no API key is used for public market data.
- Optional unauthenticated Binance public-market REST snapshots remain available as a separate source; Testnet and public-market data use separate SQLite ledgers.
- Conservative simulated fills using bid/ask plus configured slippage and estimated fees; source-tagged market ticks, fills and portfolio snapshots.
- Deterministic risk and fee modules.
- FastAPI health endpoint and Docker configuration.

Paper fills are simulated and persisted in SQLite; they are not exchange fills. Docker Compose defaults the continuous Paper service to `BINANCE_TESTNET_WEBSOCKET`, using the Spot Testnet REST bootstrap at `https://testnet.binance.vision/api` and combined WebSocket streams at `wss://stream.testnet.binance.vision/stream`. This is a Testnet market environment and its quotes/order book can differ from production Spot markets. Set `MUMIN_MARKET_DATA_SOURCE=BINANCE_PUBLIC_MARKET_DATA` for the unauthenticated public REST snapshot mode, or `MUMIN_MARKET_DATA_SOURCE=MOCK_SYNTHETIC` for deterministic offline prices. Each source uses a separate database ledger. BNB fee deduction is not enabled by this Paper engine; fees are estimated in USDT and are not account-verified.

## Not production-ready

No live orders are sent to Testnet or Mainnet; no private account endpoints, withdrawals, margin, or futures are used by the Paper stream. Before any live deployment, add exchange filter validation, persistent order state, reconciliation, robust fee-asset conversion, high-water-mark drawdown controls, monitoring, secrets management, disaster recovery, integration tests, and jurisdiction/platform eligibility review. Backtests and paper results never guarantee future returns.

## GitHub helper

`scripts/init_and_push.py` uses an existing authenticated GitHub CLI session and is intended to create a **new** repository. Do not run it against an existing repository; this project is being managed directly in the dedicated repository.


## Run the Binance Spot Testnet WebSocket Paper stream

```bash
docker compose up --build -d mumin-paper-bot
docker compose logs -f mumin-paper-bot
```

Or run directly after installing the project:

```bash
python scripts/run_continuous_paper.py --data-source BINANCE_TESTNET_WEBSOCKET --db data/mumin_binance_testnet_paper.db
```

The process consumes public Testnet streams for `BTCUSDT`, `ETHUSDT`, and `BNBUSDT`: `@trade`, `@bookTicker`, `@depth20@100ms`, and `@kline_1m`. It reconnects with exponential backoff and re-seeds its market state from Testnet REST endpoints after reconnects. It prints Paper signals, simulated actions, equity, realized net PnL, daily net PnL, and cumulative estimated fees. No API key or secret is required. Stop it with `docker compose stop mumin-paper-bot`.
