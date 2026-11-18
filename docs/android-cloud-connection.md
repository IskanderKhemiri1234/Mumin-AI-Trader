# Android cloud connection and sign-in

## Connect the Android app to the same backend

1. Install the APK built from the same repository commit as the backend.
2. Open **Connection** in the app and enter the backend base URL, without a path, query, or credentials. Use an HTTPS URL for a remote host; the app rejects remote plain HTTP.
3. Select **Connect to server**. The app opens that server's `/dashboard` page.
4. Sign in with the exact `DASHBOARD_PASSWORD` configured in that server's private environment. This is the same password used by the FastAPI service; it is not a Binance API key.
5. The dashboard stores the password only in WebView session storage for the current tab/session. It is not compiled into the APK.

For a local Termux backend, use `http://127.0.0.1:8000`. For a remote backend, use its verified HTTPS URL.

## Binance connectivity

Public Spot market data does not require account credentials. Private account balance/commission reads require Binance API credentials configured server-side. Never put those credentials in the APK, browser storage, source control, screenshots, logs, or chat. Use a dedicated API key with withdrawals, margin, and futures disabled. Account-linking endpoints must remain read-only until their encrypted, durable server-side credential storage and access controls have been reviewed.

## Hosting limitations

The Render Free web service may sleep when idle, and its local filesystem is ephemeral. It is suitable only for a stateless demonstration/API trial, not as a continuously running trading worker or durable Paper ledger/account-secret store. A persistent database/disk or an always-on compute plan is needed for durable operation; do not silently switch to paid resources. If Render requests billing information before creating a free service, stop rather than adding payment details.

## Trading safety

The Android client does not authorize exchange orders. Keep `TRADING_MODE=PAPER`, `ALLOW_MAINNET=false`, and `LIVE_TRADING_ENABLED=false`. The configured 70 USDT Paper capital is simulated and is not a real Binance balance.
