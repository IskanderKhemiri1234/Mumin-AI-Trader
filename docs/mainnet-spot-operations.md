# Binance Spot Mainnet operations

> **Not a release checklist.** Mainnet is disabled by default. The independent signal worker and supervisor are implemented on the feature branch but still require passing CI, sustained Shadow evaluation, independent review, verified Binance account permissions, and a signed Android release before any live activation.

## Isolation and credentials

Mainnet uses its own credential pair and SQLite ledger. Keep the keys only in the private server-side `.env` on the Samsung S21 host. Never place them in Android assets, WebView storage, chat messages, dashboard source, GitHub, logs, or support messages.

Create a dedicated Binance API key restricted to Spot trading only. Disable withdrawals, transfers, margin, futures, and any other permissions that are not required. Binance does not expose all API-key permission scopes through the account endpoint, so verify the key's permissions in Binance before use.

The server must use the account's legally permitted region and an account that is eligible for the requested Binance Spot products.

## Mainnet gates

The following are intentionally disabled in `.env.example`:

- `TRADING_ENV=MAINNET`
- `TRADING_MODE=LIVE`
- `ALLOW_MAINNET=true`
- `LIVE_TRADING_ENABLED=true`
- `MAINNET_ORDER_PREFLIGHT_ENABLED=true`
- `MAINNET_SPOT_EXECUTION_ENABLED=true`
- `MAINNET_CONFIRMATION=I_ACKNOWLEDGE_REAL_MONEY_SPOT_TRADING`
- `MAINNET_LEGAL_ELIGIBILITY_CONFIRMED=true`
- `BINANCE_MAINNET_API_KEY` and `BINANCE_MAINNET_API_SECRET`
- `MAINNET_ORDER_DATABASE_URL=sqlite:///./data/mumin_mainnet_spot.db`

The application rejects partial gate combinations. Testnet and Mainnet flags cannot be mixed. The Mainnet REST host is pinned to `https://api.binance.com`; no host override is accepted.

## Independent Mainnet signal worker and supervisor

The dedicated worker lives in `src/mumin_trader/execution/mainnet_signal_worker.py`. It consumes public Binance Mainnet Spot market data through the fixed public REST host and runs the existing MarketScanner/StrategyEngine filters over closed candles. It does not import or reuse the Continuous Paper worker, Paper execution engine, Paper account, or Paper database.

Default configuration:

- `MAINNET_AUTONOMOUS_EXECUTION_ENABLED=false`
- `MAINNET_AUTONOMOUS_CONFIRMATION=` (empty)
- `MAINNET_AUTONOMOUS_BOOT_RESUME_ENABLED=false`
- `WATCHLIST=BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,LINKUSDT` (the Mainnet worker refuses to start if any of the eight configured Spot symbols are missing)
- `MAINNET_SIGNAL_INTERVAL=15m` (entry/indicator timeframe; the 5m frame remains a fast-trend confirmation)
- `MAINNET_SIGNAL_INTERVAL_SECONDS=60` (poll cadence, not candle timeframe)
- `MAINNET_SIGNAL_DATABASE_URL=sqlite:///./data/mumin_mainnet_signals.db`

In Shadow mode, no private Mainnet executor is constructed and no orders are sent. Signals, per-candle decisions and audit events are stored in the separate signal ledger. Autonomous mode is a distinct opt-in path: it requires the separate flag, exact confirmation phrase, all Mainnet execution gates, server-only Mainnet credentials, fresh reconciliation before each cycle, and the protected OPOCO entry workflow. It scans all eight configured symbols independently, records each symbol/candle decision, and durably claims each eligible symbol/candle before any exchange request. Each order still passes the shared account-wide exposure and open-position limits; the current maximum of two open positions means the bot will monitor all eight but will not hold eight simultaneous positions. Quantity is capped by both `MAX_TRADE_ALLOCATION_USD` and the configured per-trade risk ceiling. With the current defaults, 70 USDT capital and a 0.50% risk ceiling imply 0.35 USDT maximum risk, while the separate 5 USDT trade-allocation cap makes the actual 0.50% stop risk smaller. Do not enable it until a sustained Shadow evaluation and independent review have passed.

The Termux runit service `mumin-mainnet-signal-worker` runs the dedicated supervisor. The supervisor uses a process lock, bounded restart backoff, persistent desired RUNNING/STOPPED state and supervisor/worker heartbeats. It is registered in the Termux:Boot startup script. Authenticated controls are available at:

- `GET /api/mainnet/signal-worker/status`
- `POST /api/mainnet/signal-worker/control` with `{"desired_state":"RUNNING"}` or `{"desired_state":"STOPPED"}`

Shadow mode resumes after a phone/supervisor restart. If autonomous mode is configured, the supervisor forces the desired state to STOPPED after a supervisor restart unless `MAINNET_AUTONOMOUS_BOOT_RESUME_ENABLED=true`. This requires a fresh dashboard start confirmation by default. Android battery management and operating-system process termination still mean that uninterrupted 24/7 availability cannot be guaranteed.

## Chat approvals and audit

A valid chat proposal is persisted for five minutes with a canonical proposal hash. The authenticated submission endpoint atomically consumes the approval; replay, expired approvals and any change to symbol, side, quantity, price, client IDs or protection prices are rejected. Submission success, rejection and unknown outcomes are recorded in the same durable Mainnet database and displayed in the Mainnet dashboard. Unknown outcomes are never automatically retried.

The Android Mainnet order form uses the same Operations Chat proposal/approval path; it must not submit directly without an approval ID.

## Order controls

- Only Spot LIMIT entries and exits are supported.
- Direct unprotected BUY LIMIT orders are rejected. BUY entries must use the atomic OPOCO order list with a take-profit and stop-loss leg.
- The current fixed exit policy is a 0.50% stop and 1.25% take-profit, within the validator's narrow tolerance.
- A successful account/trade reconciliation no older than 90 seconds is required before every new order.
- Symbol filters, minimum notional, quantity steps, quote reserve, exposure, open-position count, daily loss, drawdown, BNB fee readiness, and the FIFO fee/cost ledger are checked server-side.
- The configured paper capital is not the user's Binance account balance. Mainnet exposure is capped by the separate configured allocation and reserve.
- SELL exits can be submitted as LIMIT orders. Cancelling a protective OPOCO list can leave an open position without its exits; the dashboard requires an explicit confirmation.
- Emergency stop halts new entries and cancels open BUY orders while preserving SELL exit/protective orders. It does not guarantee that exposure is closed.

## Operations Chat syntax

Chat does not submit an order when a command is typed. It first creates a proposal card. The user must review the exact values and press the confirmation button.

Protected BUY proposal:

```text
BUY BTCUSDT qty=0.0001 price=50000 tp=50625 stop=49750 stop_limit=49740
```

SELL LIMIT proposal:

```text
SELL BTCUSDT qty=0.0001 price=50000
```

The numbers above are syntax examples only, not current market prices or trade recommendations. A proposal is refused unless Mainnet is enabled, the last reconciliation is fresh, the symbol is in the fixed watchlist, and the configured allocation and exit policy pass local validation. Binance filters and live account risk checks run again at confirmation time.

If an order result is unknown, do not submit it again. Reconcile the account and order list first; the durable client order ID is used to prevent duplicate submission.

## Current release boundary

This branch is not yet a production live-trading release. The independent worker, durable supervisor, approval ledger and mocked OPOCO lifecycle tests are now present, but they have not yet completed final CI/review or a sustained market Shadow evaluation. The signed Release workflow compares the certificate from the configured keystore and new APK with the published v1.4.5 APK baseline; the installed phone app must have come from that release (or its signer must be separately verified). The Release workflow has not been run with the private signing secrets. All Binance exchange tests are mocked/offline. No real Binance account has been accessed and no real Binance order has been submitted by CI or by this branch.
