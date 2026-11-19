# Binance Spot Mainnet operations

> **Not a release checklist.** Mainnet is disabled by default. Do not enable these settings until the Mainnet signal worker, bot supervisor, end-to-end exchange tests, independent review, and signed Android release are complete.

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

This branch is not yet a production live-trading release. The existing continuous Paper worker is deliberately not connected to Mainnet order submission. A separate validated signal worker and bot execution bridge, chat audit/approval lifecycle, restart supervisor, end-to-end OPOCO lifecycle tests, independent review, Android client integration, and original APK signing-certificate verification remain required. No real Binance order has been submitted by CI or by this branch.
