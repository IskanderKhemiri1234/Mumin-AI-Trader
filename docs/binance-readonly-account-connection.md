# Binance account connection — read-only preparation

This integration is intentionally separate from Paper and Spot Testnet execution.

## What it does

- The authenticated server route is `GET /api/binance/account/read-only`.
- Credentials are read only from the S21 server environment:
  - `BINANCE_ACCOUNT_READONLY_API_KEY`
  - `BINANCE_ACCOUNT_READONLY_API_SECRET`
- The client is pinned to `https://api.binance.com`.
- It performs only `GET /api/v3/time` and signed `GET /api/v3/account` requests.
- It returns the Spot account type and non-zero free/locked balances.
- It never places/cancels orders, transfers funds, withdraws, accesses Margin/Futures, or writes to the Paper ledger.
- It uses the existing dashboard password authentication. API credentials are never accepted from the Android app or chat request body.

## Required Binance key setup before connection

Create a dedicated API key in Binance API Management for account reading only. Keep trading permissions disabled and withdrawals disabled. Apply IP restrictions where practical for the S21 network. Do not reuse Testnet credentials. Do not paste credentials into ChatGPT, GitHub issues, source code, APK assets, or dashboard browser storage.

The account endpoint does not reveal the API-key permission scope. The response therefore explicitly warns that read-only scope must be verified in Binance API Management. The fact that an account reports `canTrade=true` is an account capability flag; it does not mean this application has enabled order submission.

## Server environment

Set the two `BINANCE_ACCOUNT_READONLY_*` variables in the private Termux `.env` file on the S21 only after the user chooses to connect the account. Restart the API service after setting them. Never put these variables in the Android build environment or GitHub Actions secrets.

## Safety boundary

- `TRADING_ENV=TESTNET` remains the only environment supported by the existing trading/execution configuration.
- `ALLOW_MAINNET=false` and `LIVE_TRADING_ENABLED=false` remain mandatory.
- The production Mainnet URL exists only inside this isolated account-read module for signed account reads.
- No Mainnet order endpoint or Mainnet execution adapter is imported or exposed by this module.
- Paper remains a separate virtual ledger with its own 70 USDT starting balance. Binance wallet balances are displayed as exchange account information and are never copied into Paper capital.
- Testnet order execution remains disabled by default and requires its separate explicit operational approval and preflight.
- No automatic signal path connects Paper decisions to Binance.

This is account-readiness plumbing, not authorization to trade. Any future Testnet or live-order capability requires a separate review, explicit approval, and independent verification.
