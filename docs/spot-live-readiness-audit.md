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
- The execution adapter requires `accountType=SPOT` and `canTrade=true`. Binance's `canWithdraw` field is an account capability in the account response, not a reliable assertion of the API key's withdrawal scope; it is not used as an order-authorization gate. The adapter exposes no withdrawal endpoint. API-key scopes must still be verified independently in Testnet key management (Spot trading and User Data only, with withdrawal/Margin/Futures permissions absent where configurable). No key or secret is stored in the app or included in source.

## Testnet execution safeguards added in PR #37

- Separate `TRADING_MODE=TESTNET` and two disabled-by-default gates: preflight and Testnet execution.
- Spot LIMIT/GTC only, allowlisted USDT pairs, exchange symbol status and Spot permission checks, PRICE_FILTER/LOT_SIZE/NOTIONAL checks, configured minimum/maximum trade notional, and quote-reserve/exposure/open-position checks.
- Durable SQLite order intents and unique client-order IDs. Reusing an ID with changed parameters is rejected.
- An ambiguous submission or cancellation outcome is not automatically retried. The order is marked unresolved and must be reconciled. Any reported partial/full fill is immediately ingested into the durable trade ledger; failure to persist it activates a halt.
- Order query, cancel, partial-fill status/quantity persistence, account/open-order reconciliation, and persistent emergency stop are implemented.
- Daily-loss and peak-drawdown state is persisted; a loss-limit halt blocks new BUYs but permits risk-reducing SELLs. Manual emergency and reconciliation halts block all new orders.
- Reconciliation failures and mismatches activate a persistent halt.

## Remaining blockers before any first real-money order

1. The current PR commit's CI must remain green for compile, full pytest, Ruff, Paper smoke, Android APK build, signature verification, and source-provenance verification; prior green runs do not certify later commits.
2. Testnet execution must be exercised with dedicated Spot Testnet credentials and verified BUY, SELL, cancel, partial fill, timeout, restart, duplicate ID, and reconciliation scenarios. No Testnet order has been sent by this work.
3. FIFO realized-PnL accounting is now derived from persisted exchange `myTrades` fills, including partial exits and open-lot cost basis. Actual `isMaker`/taker classification, raw commission totals by asset, effective maker/taker fee-rate marks, gross entry/exit notionals, executed quantities and average entry/exit prices are reported. Quote-asset fees add to entry cost or reduce exit proceeds; base-asset fees adjust delivered/disposed quantity; BNB fees require a historical BNB/USDT 1-minute candle-close valuation mark. BNB valuation is an auditable estimate, not an exchange-reported conversion; missing marks, unsupported fee assets, or sells without matched FIFO inventory make the report incomplete and block new orders. Remaining work: independently validate accounting against exchange statements and stress-test partial-fill/cancel/restart races.
4. Implement exchange-hosted protective exits (stop/target or an independently reviewed bracket/OCO design) and prove behavior through partial fills, cancel/replace, gaps, and API outages. Simulated Paper stops are not sufficient.
5. Add an operator-facing, authenticated emergency-stop control and an independently tested recovery/unhalt procedure. The current kill switch is a code-level durable control, not yet a deployed dashboard control.
6. Add a continuously supervised market-data freshness/streaming layer for automated execution; current Paper runner is REST snapshot based.
7. Complete independent security review of secret handling, deployment access, key permissions, IP restrictions, dependency and supply-chain posture, and incident response.
8. Produce a signed numeric GO/NO-GO report and require separate explicit human approval for any Mainnet implementation and first real order.

## Current release gate

- Paper engine: remains Paper only.
- Testnet adapter: isolated, opt-in, under review; has an explicit operator CLI, but is not connected to autonomous signals.
- Mainnet: hard blocked.
- Real-money trading: **not approved and not enabled**.


### OCO recovery hardening (2026-11-16; branch-only)
- OCO list queries now fetch each child order by its exchange `orderId` because the list-query response provides child IDs but not current child execution quantities/statuses. The child reports are then reconciled into the local response and actual `myTrades` fills are synchronized.
- SQLite now stores the parent `orderListId` plus the above/below `orderId` and `clientOrderId` values in dedicated columns, while retaining the full exchange response JSON.
- Any child reported as `PARTIALLY_FILLED` moves the list to `PARTIAL_FILL_REVIEW` and persists an execution halt (`OCO_PARTIAL_FILL_REVIEW`). The residual position is not declared protected and no new entries are permitted until manual/exchange reconciliation.
- A previously persisted OCO list is queried again before returning its state; a stale local `EXECUTING` snapshot is not treated as current exchange truth.
- Mocked tests added for create-response loss followed by list recovery without duplicate POST, cancel-response loss followed by exchange query before retry, partial-fill halt, and persistence of both child order identifiers.
- These remain simulated transport tests. They do not establish that Binance Spot Testnet accepted an OCO, canceled its sibling, or produced the same fills/accounting as the local ledger.


### Supervised Testnet lifecycle runner (2026-11-16; branch-only)
- Added `scripts/binance_spot_testnet_lifecycle.py` with read-only `inspect`, explicitly confirmed `run`, and read-only `watch` commands.
- The run is pinned to the Spot Testnet REST base URL, requires both Testnet execution gates and both Mainnet/live flags false, uses a per-run SQLite ledger, and requires the literal `TESTNET_ONLY` confirmation.
- The runner pre-validates the BUY and protective SELL OCO before placing the BUY; after execution it sizes OCO from actual `myTrades` net base quantity, accounting for base-asset commissions and LOT_SIZE step rounding.
- It records the BUY order ID, OCO `orderListId`, both child order IDs/client IDs, child status reports, exact `myTrades` fills, FIFO realized-PnL report, and final account reconciliation. Evidence is stored locally with restrictive directory/file permissions; credential/signature fields and known credential values are redacted.
- An OCO that has not triggered by the watch timeout is deliberately left active as protection and can be monitored using the `watch` command; it is not canceled merely to make a test appear complete. A partial fill is a review/NO-GO state. Residual unprotected dust or open FIFO inventory prevents a verified result.
- OCO child order IDs are now included in unmatched-fill and executed-quantity reconciliation, rather than being incorrectly classified as trades without local intents.
- CI on commit `60dcca3271bc020f7e019680bf3f6fed841a2425`: 126 tests passed and Ruff passed. This is software/MockTransport evidence only.
- **No real Spot Testnet BUY, SELL, or OCO has been submitted by this work.** Operator execution and exchange-generated evidence are still pending; Mainnet remains NO-GO.
