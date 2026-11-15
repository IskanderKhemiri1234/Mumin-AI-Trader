# Mumin AI Trader — safety-first Spot research platform

Mumin AI Trader combines a Python Paper-trading engine with a responsive Arabic/English operations dashboard. It is **not an autonomous live-trading product**: Binance connectivity currently supports public market data and signed read-only account/commission checks. Exchange order submission, withdrawals, margin, futures, and Mainnet trading remain disabled.

## Safety defaults

- `TRADING_ENV=TESTNET`, `TRADING_MODE=PAPER`.
- Mainnet requires several independent explicit flags and legal-eligibility acknowledgement.
- `ExecutionEngine.submit_order` is hard-disabled.
- Never grant withdrawal permission to an API key.
- Never put exchange secrets in Git, issue trackers, logs, browser storage, or chat.
- The 70 USDT configured strategy ceiling is not a deposit, wallet balance, or promise of returns.
- Stop-loss and take-profit rules in the continuous Paper engine are simulated exits, not exchange-hosted protective orders.

## Platform dashboard

The responsive platform is served by FastAPI at `/dashboard`. It includes:

- Arabic/English interface with responsive mobile navigation.
- Portfolio equity, cash, realized and daily Paper PnL, estimated fees, closed trades and open positions.
- Equity history chart, recent fills, position details, and risk-limit status.
- Automatic read-only refresh from the SQLite Paper ledger.
- No browser-side Binance API credentials and no order-placement controls.

The data API at `/api/dashboard` is protected by the server-side `DASHBOARD_ACCESS_TOKEN`. Set a long random token in the private server environment before using the dashboard. The token entered in the browser is held in session storage for the current tab only; it is not a Binance credential. The dashboard is a private operations console, not a public website. Do not expose it publicly without adding a proper identity provider, TLS, rate limiting, and deployment access controls.

The API reads `MUMIN_PAPER_DB_PATH`, defaulting to `data/mumin_public_paper.db`. Docker Compose's Paper worker writes to `/app/data/mumin_public_paper.db`, which matches this default. The API opens the SQLite database read-only and never creates or modifies ledger records.

## Android companion app

A lightweight Android WebView companion is built in `android/`. It loads the latest static Paper report from the `mumin-paper-ledger` branch on every launch and supports pull-to-refresh. It does not contain Binance credentials and cannot place orders.

- GitHub Actions workflow: `.github/workflows/build-apk.yml`.
- Trigger: changes to `android/**`, workflow dispatch, or pull requests that touch the Android app.
- Output artifact: `Mumin-AI-Trader-APK`, containing `Mumin-AI-Trader.apk` and its SHA-256 checksum.
- The CI artifact is a debug-signed installable APK for direct testing; it is not a Play Store release or a privately release-signed production package.
- The app reads the report from a public GitHub repository. Anyone who can access that public branch can read the same report; installing the app does not make the underlying report private.
- The report is a snapshot from the latest successful Paper export, not a live streaming screen. The app fetches the newest snapshot on launch or manual refresh.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Set DASHBOARD_ACCESS_TOKEN to a long random secret in .env.
uvicorn mumin_trader.main:app --reload
```

- Dashboard: http://127.0.0.1:8000/dashboard
- Health: http://127.0.0.1:8000/health
- Read-only API: http://127.0.0.1:8000/api/dashboard (requires `X-Dashboard-Token` header)

## Run with Docker

```bash
cp .env.example .env
# Set DASHBOARD_ACCESS_TOKEN in the private .env file.
docker compose up --build -d
docker compose logs -f
```

## Tests

```bash
pytest -q
```

## Current engine capabilities

- Binance Spot public WebSocket market streams with reconnect and heartbeat.
- Liquidity and spread screening; candidates are observations, not trade signals.
- Continuous SQLite-backed Paper portfolio for BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT and LINKUSDT, with shared cash, per-symbol positions and portfolio-level exposure limits.
- Public Binance market snapshots (closed 1-minute candles plus bid/ask) with stale-data rejection; no API key is used for public market data.
- Conservative simulated fills using bid/ask plus configured slippage and estimated fees; source-tagged market ticks, fills and portfolio snapshots.
- Deterministic risk and fee modules.
- FastAPI health and authenticated read-only dashboard endpoints.

Paper fills are simulated and persisted in SQLite; they are not exchange fills. Docker Compose defaults the continuous Paper service to Binance public market data. Set `MUMIN_MARKET_DATA_SOURCE=MOCK_SYNTHETIC` in the private environment to use deterministic offline prices. Separate database files are used for synthetic and public-market sessions. BNB fee deduction is not enabled by the continuous Paper engine; fees are estimated in USDT and are not account-verified.

## Spot Testnet order preflight (non-executing)

The isolated `BinanceSpotTestnetOrderPreflight` adapter validates a Spot LIMIT order against live Spot Testnet `exchangeInfo` filters and signs only Binance's `POST /api/v3/order/test` endpoint. Binance documents this endpoint as validating an order without sending it to the matching engine. The adapter pins its REST host to `https://testnet.binance.vision`, supports only the configured Spot watchlist, enforces the configured per-trade quote ceiling, and is disabled by default.

To opt into this validation-only check, set `TESTNET_ORDER_PREFLIGHT_ENABLED=true` together with Spot Testnet API credentials. The process must remain `TRADING_ENV=TESTNET`, `TRADING_MODE=PAPER`, `ALLOW_MAINNET=false`, and `LIVE_TRADING_ENABLED=false`. This does **not** place a Testnet order, create a persistent order record, or authorize Mainnet trading. A separate reviewed implementation is still required for actual Testnet order submission, durable order lifecycle state, idempotent retries, partial-fill reconciliation, exchange-side protective orders, and emergency halt behavior.

## Isolated Spot Testnet execution (disabled by default)

A separate `BinanceSpotTestnetExecution` adapter is being added for controlled Spot Testnet lifecycle tests. It is pinned to `https://testnet.binance.vision`, supports allowlisted USDT Spot LIMIT orders only, and has no configurable Mainnet host or Market/Margin/Futures/withdrawal method. It persists client-order intents in a dedicated SQLite table, refuses changed-parameter reuse of a client order ID, and never automatically resubmits after an ambiguous network outcome. It supports order query, cancellation, account/open-order reconciliation, and a persistent emergency stop.

Both `TESTNET_ORDER_PREFLIGHT_ENABLED` and `TESTNET_SPOT_EXECUTION_ENABLED` must be explicitly enabled for this isolated Testnet adapter. Keep `TRADING_ENV=TESTNET`, `TRADING_MODE=PAPER`, `ALLOW_MAINNET=false`, and `LIVE_TRADING_ENABLED=false`. The adapter also refuses an account unless Binance explicitly reports Spot trading allowed and withdrawals disabled. API keys must be Spot Testnet credentials stored only in private environment secrets.

This adapter is not connected to the Paper signal loop or Android app. Testnet orders are not real-money trades. The first Testnet run still requires successful CI, independent code review, dedicated Testnet credentials, and operator verification. Mainnet remains out of scope and is not authorized by a successful Testnet run.

## Not production-ready

No exchange order submission, withdrawals, margin, or futures. Before even a first Spot Testnet order, the project still needs a separately reviewed Testnet-only execution adapter, exchange filter validation, persistent order state, idempotency, reconciliation, explicit emergency halt, and tests proving Mainnet is rejected. Paper backtests and simulated results never guarantee future returns.

## Verify Binance Spot Testnet credentials (read-only)

1. In GitHub repository Settings → Secrets and variables → Actions, create repository secrets named `BINANCE_API_KEY` and `BINANCE_API_SECRET` using **Binance Spot Testnet** credentials.
2. Open Actions → **Binance Spot Testnet Account Check** → Run workflow.
3. Review the log for the connection result and Testnet USDT free/locked balance.

This workflow calls Spot Testnet ping and signed account-read endpoints only. It never submits an order. Keep withdrawals, margin, futures, and Mainnet disabled. Do not paste credentials into issues, pull requests, source files, or chat.
