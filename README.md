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

The data API at `/api/dashboard` is protected by the server-side `DASHBOARD_PASSWORD` (minimum 12 characters). Set the same strong password (at least 12 characters) in the private server environment before using the dashboard. The password entered in the browser is held in session storage for the current tab only; it is not a Binance credential. The dashboard is a private operations console, not a public website. Do not expose it publicly without adding a proper identity provider, TLS, rate limiting, and deployment access controls.

The API reads `MUMIN_PAPER_DB_PATH`, defaulting to `data/mumin_public_paper.db`. Docker Compose's Paper worker writes to `/app/data/mumin_public_paper.db`, which matches this default. The API opens the SQLite database read-only and never creates or modifies ledger records.

## Android companion app

The Android companion in `android/` is a WebView client for the always-on Termux node on the same Samsung phone. It connects to `http://127.0.0.1:8000/dashboard`; the `mumin-api` and `mumin-paper-engine` services run under `termux-services`, with Termux:Boot configured by `deploy/termux/install.sh`. No phone Termux node or external hosting is part of this architecture. The dashboard then reads the actual SQLite Paper ledger and loads Mumin Operations Chat from the API. The user enters the exact `DASHBOARD_PASSWORD` configured on the selected server in the dashboard login; the password is not embedded in the APK. The app contains no Binance credentials and cannot enable exchange execution. The password used for dashboard login is not the Binance API key or secret.

- GitHub Actions workflow: `.github/workflows/build-apk.yml`.
- Trigger: changes to `android/**`, workflow dispatch, or pull requests that touch the Android app.
- Output artifact: `Mumin-AI-Trader-APK`, containing `Mumin-AI-Trader.apk` and its SHA-256 checksum.
- Android CI compiles the app for validation but does not publish or distribute a Debug APK. The distributable package is produced only by `.github/workflows/release-android.yml` as a signed Release APK using the existing stable signing keystore and certificate verification.
- The app permits HTTP only to loopback hosts `127.0.0.1` and `localhost`; all remote server connections must use HTTPS.
- The dashboard includes Paper-only controls. Backend execution gates remain authoritative; this Android client does not enable Testnet or Mainnet execution.
- If Termux services are stopped, the dashboard cannot load. The phone is the server; Termux:Boot and the service supervisor keep the API and Paper engine running after restart. The optional embedded demo is synthetic and must not be confused with live account or Paper ledger data.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Set DASHBOARD_PASSWORD to a long random secret in .env.
uvicorn mumin_trader.main:app --reload
```

- Dashboard: http://127.0.0.1:8000/dashboard
- Health: http://127.0.0.1:8000/health
- Read-only API: http://127.0.0.1:8000/api/dashboard (requires `X-Dashboard-Password` header)

## Run with Docker

```bash
cp .env.example .env
# Set DASHBOARD_PASSWORD in the private .env file.
docker compose up --build -d
docker compose logs -f
```

## Tests

```bash
pytest -q
```

## Current engine capabilities

- Binance Spot REST market snapshots (book ticker, closed candles, depth and server time); the continuous Paper runner skips the entire cycle when any of the eight required symbols is missing or stale.
- Liquidity and spread screening; candidates are observations, not trade signals.
- Continuous SQLite-backed Paper portfolio for BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT and LINKUSDT, with shared cash, per-symbol positions and portfolio-level exposure limits.
- Public Binance market snapshots (closed 1-minute candles plus bid/ask) with stale-data rejection; no API key is used for public market data.
- Conservative simulated fills using bid/ask plus configured slippage and estimated fees; source-tagged market ticks, fills and portfolio snapshots.
- Deterministic risk and fee modules.
- FastAPI health and authenticated read-only dashboard endpoints.

Paper fills are simulated and persisted in SQLite; they are not exchange fills. Docker Compose defaults the continuous Paper service to Binance public market data. Set `MUMIN_MARKET_DATA_SOURCE=MOCK_SYNTHETIC` in the private environment to use deterministic offline prices. Separate database files are used for synthetic and public-market sessions. BNB fee deduction is not enabled by the continuous Paper engine; fees are estimated in USDT and are not account-verified.

## Spot Testnet order preflight (non-executing)

The isolated `BinanceSpotTestnetOrderPreflight` validates Spot LIMIT parameters against Binance Spot Testnet symbol filters and signs only `POST /api/v3/order/test`. That endpoint validates the order without sending it to the matching engine. It is disabled by default and pinned to `https://testnet.binance.vision`.

## Isolated Spot Testnet execution (disabled by default)

A separate `BinanceSpotTestnetExecution` adapter supports allowlisted USDT Spot LIMIT/GTC orders only. It is pinned to the Spot Testnet host and has no configurable host or Mainnet, Market, Margin, Futures, or withdrawal order method. Both preflight and execution gates must be explicitly enabled with `TRADING_ENV=TESTNET`, `TRADING_MODE=TESTNET`, `ALLOW_MAINNET=false`, and `LIVE_TRADING_ENABLED=false`.

The adapter includes exchange filter checks, quote-reserve and exposure limits, durable SQLite order intents, unique client-order IDs, no automatic retry after ambiguous submission/cancellation outcomes, order query/cancel, paginated trade-fill ingestion, fill-to-order quantity checks, account-balance reconciliation, persistent daily-loss/drawdown state, and a persistent emergency halt. It requires a successful initial account/trade reconciliation before any order. Account responses must confirm Spot trading. Binance `canWithdraw` is an account capability, not a reliable API-key scope check; key scopes must be verified separately in Testnet key management.

This adapter is not connected to the Paper signal loop or Android app. Current automated tests use mocked Binance responses; **no real Testnet order has yet been placed or verified**. A dedicated Testnet operator run and independent review are still required. A successful Testnet run will not enable Mainnet.

### Operator CLI (Spot Testnet only)

The operator CLI is explicit and is not wired to autonomous signals. Run it only in a private environment with dedicated Spot Testnet credentials and a dedicated SQLite ledger:

```bash
python scripts/binance_spot_testnet.py reconcile
python scripts/binance_spot_testnet.py preflight --symbol BTCUSDT --side BUY --quantity 0.00010 --price 50000 --client-order-id mumin-preflight-001
```

Only after reviewing a clean reconciliation and deliberately enabling both Testnet gates, a single Testnet order requires the literal confirmation:

```bash
python scripts/binance_spot_testnet.py submit --symbol BTCUSDT --side BUY --quantity 0.00010 --price 50000 --client-order-id mumin-test-001 --confirm-testnet-order TESTNET_ONLY
```

Cancellation also requires `--confirm-testnet-cancel TESTNET_ONLY`. The CLI never selects a Mainnet URL. Do not run the submit command against a real-money account or with Mainnet credentials.

## Not production-ready

The project is **not approved for real-money trading**. Mainnet is hard-blocked by the current Settings validator. Paper stop-loss/take-profit are simulated, not exchange-hosted protective orders. Before any Mainnet consideration, the project still requires green CI, a verified dedicated Spot Testnet lifecycle, complete cost-basis/fee accounting, exchange-hosted protective exits, an operator-facing authenticated emergency stop, supervised market-data freshness/reconnect handling, independent security review, and a numeric GO/NO-GO report. Any later Mainnet implementation and first real order require a separate explicit human approval. Paper or Testnet results do not guarantee future returns.



## Verify Binance Spot Testnet credentials (read-only)

1. In GitHub repository Settings → Secrets and variables → Actions, create repository secrets named `BINANCE_API_KEY` and `BINANCE_API_SECRET` using **Binance Spot Testnet** credentials.
2. Open Actions → **Binance Spot Testnet Account Check** → Run workflow.
3. Review the log for the connection result and Testnet USDT free/locked balance.

This workflow calls Spot Testnet ping and signed account-read endpoints only. It never submits an order. Keep withdrawals, margin, futures, and Mainnet disabled. Do not paste credentials into issues, pull requests, source files, or chat.


### Supervised BUY → OCO → FIFO Testnet evidence runner

The lifecycle runner is a manual, one-cycle operator tool. It is not connected to the strategy loop and cannot select a Mainnet host.

Before using it, install the project in Termux with Python 3.11+ and configure the private environment with dedicated Spot Testnet credentials. Set these non-secret switches in the private `.env` file:

```dotenv
TRADING_ENV=TESTNET
TRADING_MODE=TESTNET
ALLOW_MAINNET=false
LIVE_TRADING_ENABLED=false
TESTNET_ORDER_PREFLIGHT_ENABLED=true
TESTNET_SPOT_EXECUTION_ENABLED=true
```

Keep `BINANCE_API_KEY` and `BINANCE_API_SECRET` in that private environment only. Never paste them into a command, source file, GitHub issue, or chat. Restrict the file with `chmod 600 .env`.

First inspect the live Spot Testnet price and symbol filters. This is read-only:

```bash
python scripts/binance_spot_testnet_lifecycle.py inspect --symbol BTCUSDT
```

Then run one explicitly confirmed cycle. Supply a quantity and prices that satisfy the returned LOT_SIZE, PRICE_FILTER and NOTIONAL filters and the configured 5 USDT per-trade ceiling:

```bash
python scripts/binance_spot_testnet_lifecycle.py run \
  --symbol BTCUSDT \
  --quantity YOUR_VALID_QUANTITY \
  --buy-price YOUR_BUY_LIMIT_PRICE \
  --take-profit-price YOUR_TAKE_PROFIT_PRICE \
  --stop-price YOUR_STOP_TRIGGER_PRICE \
  --confirm-testnet-cycle TESTNET_ONLY
```

The default Testnet per-order cap remains the configured value (currently 5 USDT). Some symbols require the protective OCO stop leg to remain above the exchange minimum notional after fees and price rounding; in that case the pre-validation safely refuses before sending the BUY. If the inspected filters prove it is necessary, an operator may explicitly use the one-run `--testnet-max-order-usdt 6.00` override. It is hard-limited to 6 USDT, applies only to this Testnet process, and does not edit `.env`, Paper strategy limits, or any Mainnet setting.

The runner performs a clean initial reconciliation, validates BUY and protective OCO parameters before the BUY, submits one LIMIT BUY, synchronizes actual `myTrades`, computes the net base quantity after base-asset commission, and places a protective SELL OCO. It polls both OCO child orders and records `orderListId`, each child `orderId`/client ID, exchange statuses, exact fills, FIFO report, and final account reconciliation.

If neither OCO leg fills before the default 15-minute watch window, the runner deliberately leaves the protective OCO active rather than canceling protection. It prints the database path, evidence directory and `listClientOrderId`. Continue monitoring that same list without placing another order:

```bash
python scripts/binance_spot_testnet_lifecycle.py watch \
  --symbol BTCUSDT \
  --list-client-order-id THE_PRINTED_LIST_CLIENT_ORDER_ID \
  --database THE_PRINTED_DATABASE_PATH \
  --evidence-dir THE_PRINTED_EVIDENCE_DIRECTORY
```

Evidence is saved locally under `artifacts/binance-spot-testnet-evidence/` with restrictive directory/file permissions. The evidence contains exchange account balances, order responses and trade fills; treat it as private account data and do not commit or upload it publicly. API key/secret/signature fields are explicitly excluded from evidence and console output.

A run is marked `TESTNET_CYCLE_VERIFIED` only when one OCO child is FILLED, its sibling is CANCELED/EXPIRED, both child fill totals match the exchange reports, FIFO is complete, and final reconciliation has no discrepancies. A timeout, partial fill, unresolved response, or accounting mismatch remains NO-GO and requires operator review. The runner never enables Mainnet or clears a persistent halt. A successful Testnet cycle is evidence for this test only, not permission to trade real funds or a guarantee of future performance.
