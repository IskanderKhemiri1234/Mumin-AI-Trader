# Binance Spot readiness audit

Audit snapshot: 2026-11-15  
Scope: current `main` Paper engine plus the isolated Testnet implementation proposed in PR #37. This document is an engineering status report, not authorization to trade real funds.

## Executive decision

**NO-GO for Mainnet.** Mainnet is explicitly rejected by the current Settings validator. The Paper engine remains separate and the Testnet adapter is not wired into the signal loop or Android app. A successful Testnet order must never enable Mainnet.

## Signal engine — strategy preserved

- The public-market-data Paper path uses `PaperMicroAlphaSwarm`: wick-sweep proxy, order-book depth imbalance, and Micro-Alpha risk filter.
- Entry requires strict three-way BUY consensus. The filter rejects spread at or above 8 bps, ATR outside 0.05%–0.80%, and missing/invalid hourly macro data. BUY additionally checks 21 closed hourly closes and the SMA20/falling-trend guard.
- The wick-sweep is explicitly a price-action proxy; it is not proof of hidden stops or executed flow.
- The older `PaperSwarm` momentum/liquidity path is used for synthetic Paper data. The public Binance Paper path uses Micro-Alpha.
- No agent thresholds or signal logic are changed by this readiness work.

## Market data and stale-feed behavior

- The continuous Paper runner currently requests REST snapshots: book ticker, 1-minute klines, 1-hour klines, depth, and server time. It does not use a WebSocket stream in this runner.
- It rejects a 1-minute candle older than 180 seconds, hourly macro data older than 7,200 seconds, invalid quotes, invalid candles, and insufficient depth.
- The continuous runner requires all eight configured symbols in one snapshot. If any are missing, it skips the entire cycle without synthetic fallback. A finite CI/smoke run fails rather than silently continuing with partial data.
- There is no verified live WebSocket order-execution feed or WebSocket-disconnect recovery path in the current Paper runner. That must be designed and tested before any automated strategy-to-exchange connection.

## Paper risk and persistence

- Paper defaults and scheduled workflow policy: 70 USDT configured capital, 5 USDT maximum trade allocation, 20% cash reserve, two open positions, 2% daily loss limit, 5% maximum drawdown, 0.50% simulated stop cap, 1.25% simulated target cap, and strict 8 bps spread ceiling.
- The Paper engine persists account, positions, fills, equity, portfolio snapshots, market ticks, and per-agent decisions in SQLite WAL mode. Batch integrity and WAL checkpoint checks are present.
- Important boundary: Paper stop-loss and take-profit are simulated exits. They are not exchange-hosted protective orders.
- Some Paper limits remain environment-configurable in the runner; the scheduled workflow supplies the agreed values. This needs a separate immutable live-risk policy before Mainnet consideration.

## API credentials and environment

- Public Paper market data is unauthenticated.
- Testnet credentials are represented as Pydantic `SecretStr` and are sent only in the API-key header; secrets are not part of the Android app.
- The isolated adapter pins its REST base URL to `https://testnet.binance.vision`; no caller-supplied host is accepted.
- Mainnet is unconditionally blocked by the current Settings validator. Margin, Futures, withdrawals, and Mainnet order paths are not implemented in the Testnet adapter.
- The account response must explicitly report `accountType=SPOT`, `canTrade=true`, and `canWithdraw=false`; missing permission fields, any non-Spot account type, or enabled withdrawals are rejected. Binance's account payload does not prove every API-key scope: an operator must still manually verify Spot-only trading, withdrawals disabled, Margin/Futures disabled, and IP restrictions where supported. No real API key has been supplied or tested.

## Testnet execution safeguards added in PR #37

- Separate `TRADING_MODE=TESTNET` and two disabled-by-default gates: preflight and Testnet execution.
- Spot LIMIT/GTC only, allowlisted USDT pairs, exchange symbol status and Spot permission checks, PRICE_FILTER/LOT_SIZE/NOTIONAL checks, configured minimum/maximum trade notional, and quote-reserve/exposure/open-position checks.
- Durable SQLite order intents and unique client-order IDs. Reusing an ID with changed parameters is rejected.
- An ambiguous submission or cancellation outcome is not automatically retried. The order is marked unresolved and must be reconciled. Any reported partial/full fill is immediately ingested into the durable trade ledger; failure to persist it activates a halt.
- Order query, cancel, partial-fill status/quantity persistence, account/open-order reconciliation, and persistent emergency stop are implemented.
- Daily-loss and peak-drawdown state is persisted; a loss-limit halt blocks new BUYs but permits risk-reducing SELLs. Manual emergency and reconciliation halts block all new orders.
- Reconciliation failures and mismatches activate a persistent halt.

## Remaining blockers before any first real-money order

1. CI must complete successfully for compile, full pytest, Ruff, and Paper smoke. No green CI result is claimed in this audit snapshot.
2. Testnet execution must be exercised with dedicated Spot Testnet credentials and verified BUY, SELL, cancel, partial fill, timeout, restart, duplicate ID, and reconciliation scenarios. No Testnet order has been sent by this work.
3. Complete weighted-average/FIFO cost-basis and realized-PnL accounting, normalize commissions paid in quote/base/BNB assets, and stress-test balance/fill reconciliation across restart and partial-fill/cancel races. The current ledger persists raw fills and reconciles net asset deltas, but does not yet provide a fully audited cost-basis and fee-accounting ledger.
4. Implement exchange-hosted protective exits (stop/target or an independently reviewed bracket/OCO design) and prove behavior through partial fills, cancel/replace, gaps, and API outages. Simulated Paper stops are not sufficient.
5. Add an operator-facing, authenticated emergency-stop control and an independently tested recovery/unhalt procedure. The current kill switch is a code-level durable control, not yet a deployed dashboard control.
6. Add a continuously supervised market-data freshness/streaming layer for automated execution; current Paper runner is REST snapshot based.
7. Complete independent security review of secret handling, deployment access, key permissions, IP restrictions, dependency and supply-chain posture, and incident response.
8. Produce a signed numeric GO/NO-GO report and require separate explicit human approval for any Mainnet implementation and first real order.

## Current release gate

- Paper engine: remains Paper only.
- Testnet adapter: isolated, opt-in, under review; not connected to autonomous signals.
- Mainnet: hard blocked.
- Real-money trading: **not approved and not enabled**.
