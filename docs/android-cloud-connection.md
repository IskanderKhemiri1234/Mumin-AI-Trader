# Android APK ↔ Samsung S21 Termux node

## Architecture

The Samsung S21 is the platform server. The repository lives at `~/Mumin-AI-Trader`. The API and Paper engine are supervised by `termux-services` as `mumin-api` and `mumin-paper-engine`; backup and watchdog services are installed alongside them. The boot script is `~/.termux/boot/00-mumin-ai-trader`.

No Render service or other cloud host is required by this design.

## APK connection and sign-in

1. Install the APK built from the same repository revision as the Termux backend.
2. Keep the server address at `http://127.0.0.1:8000`. The app already defaults to this loopback address.
3. Ensure the Termux services are running: `sv status mumin-api mumin-paper-engine`.
4. Open the app. It loads `/dashboard` from the S21 API.
5. Sign in using the exact `DASHBOARD_PASSWORD` already stored in `~/Mumin-AI-Trader/.env` on the phone. The same password protects the dashboard, live market endpoint, and operations chat.
6. The password is sent only as `X-Dashboard-Password` to the local API and is held in WebView session storage. It is never compiled into the APK.

The operations chat and dashboard must share this single password contract. Legacy `DASHBOARD_ACCESS_TOKEN` and `X-Dashboard-Token` are not accepted.

## Start and verify the phone node

From Termux:

```sh
cd ~/Mumin-AI-Trader
sv status mumin-api mumin-paper-engine mumin-paper-backup mumin-paper-watchdog
curl -fsS http://127.0.0.1:8000/health
```

If services need to be installed or refreshed, use the repository's `deploy/termux/install.sh`. Keep Termux:Boot installed and disable Android battery optimization for Termux. Android may still restrict background work under extreme power or memory conditions; monitor service heartbeats.

## Binance and trading safety

Public Binance Spot market prices are read from the server-side market stream. Private account reads require a dedicated Binance API key configured only on the phone's server environment, with withdrawals, futures, and margin permissions disabled. Never place Binance credentials in the APK, browser storage, source control, logs, or chat.

Keep Paper mode and all live/Mainnet execution gates disabled. The Paper ledger and its 70 USDT starting capital are simulated; they are not the Binance account balance. No claim of profit or guaranteed returns is made.
