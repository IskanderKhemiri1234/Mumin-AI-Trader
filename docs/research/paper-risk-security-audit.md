# Mumin AI Trader — Paper Risk and Security Audit

**Audit date:** 2026-11-17 UTC  
**Audited application baseline:** main after the Paper accounting, decision-provenance, macro timestamp, and replay-harness changes  
**Decision:** PAPER-ONLY / MAINNET NO-GO  
**Scope:** static repository/configuration review and automated test evidence; not a penetration test or legal/compliance opinion

## 1. Execution isolation

The checked-in safe configuration defaults are:

| Setting | Required value |
|---|---|
| `TRADING_ENV` | `TESTNET` |
| `TRADING_MODE` | `PAPER` |
| `ALLOW_MAINNET` | `false` |
| `LIVE_TRADING_ENABLED` | `false` |
| `TESTNET_ORDER_PREFLIGHT_ENABLED` | `false` |
| `TESTNET_SPOT_EXECUTION_ENABLED` | `false` |
| `MAINNET_LEGAL_ELIGIBILITY_CONFIRMED` | `false` |

The Settings validator rejects MAINNET configuration in this release. The REST and WebSocket base URL properties are hard-coded to Binance Spot Testnet and reject a non-TESTNET environment. The Testnet order endpoint in the dashboard API returns HTTP 423 while the execution gate is closed. The foundation execution engine has an integration test asserting that order submission raises `ExecutionDisabledError`.

The authenticated dashboard control plane reports `execution_authorized=false`; available order commands are Paper-only. Paper order submission also rejects a non-PAPER mode, a non-TESTNET environment, and any live-enabled configuration. These controls are separate from the public market-data reader, which uses unauthenticated public market endpoints.

**Finding:** the inspected configuration and code path support Paper-only operation. This is not evidence that a live adapter is safe or approved; Mainnet remains blocked.

## 2. Frozen Paper risk envelope

| Control | Current value |
|---|---:|
| Initial and maximum Paper capital | 70.00 USDT |
| Maximum trade allocation | 5.00 USDT |
| Cash reserve | 20% |
| Maximum open positions | 2 |
| Daily loss halt | 2.00% |
| Maximum drawdown halt | 5.00% |
| Spread entry ceiling | strictly below 8 bps |
| Simulated fee | 0.10% per side |
| Simulated slippage | 2 bps per side |
| ATR-derived stop | 0.20%–0.50% |
| ATR-derived target | 0.40%–1.25% |

No risk setting is changed by this audit.

**Important limitation:** stop-loss and take-profit conditions are simulated inside the Paper engine. They are not exchange-side protective orders. A live price gap, API outage, process interruption, or network delay can exceed these modeled thresholds. No live trading may rely on the Paper stop/target logic as proof of actual exchange protection.

## 3. API and credential controls observed

- Dashboard API requests require `X-Dashboard-Token`; comparison uses `hmac.compare_digest`.
- Dashboard token configuration is read from the server environment; `.env.example` contains an empty placeholder, not a real token.
- Binance key and secret settings are represented as Pydantic `SecretStr`; the public market-data path does not require private credentials.
- Paper control endpoints are authenticated and accept only pause/resume.
- Emergency stop is Paper-only and requires explicit confirmation.
- SQLite dashboard snapshots are opened read-only by the API reporting path.
- Paper order commands use an idempotency key and explicit confirmation.

**Residual risks / work required:**

1. The browser dashboard stores its access token in session storage. This increases exposure if an XSS vulnerability is introduced. Before any real-money service, complete a CSP, DOM-sanitization, dependency, and XSS review; prefer an HttpOnly secure cookie or short-lived scoped token if the architecture permits.
2. This review did not inspect private GitHub Actions secret values and cannot certify that no secret was ever committed in the full repository history. Run a full-history secret scan and rotate any exposed credential.
3. No independent penetration test, dependency threat assessment, or external security review has been completed.
4. API authentication, rate limiting, audit retention, backup/restore, and service-level alerting require review under the eventual always-on deployment.
5. The current Paper scheduler is GitHub Actions on a five-minute best-effort schedule. It is not a continuous 24/7 execution host.

## 4. Operational reliability

The first immutable observation archive, SHA-256 `b32277cb9e5abd18ffcb43eee7c3c8577d4052079ff0f2442520ed25cb055ec8`, contains 2,462 public market snapshots, 1,992 agent decisions and 92 Paper fills. Only 271 of 369 observed cycles contain all eight symbols; 98 cycles are incomplete. The archive estimates 35,386 missing one-minute slots, 12 duplicate candle snapshots, and a largest per-symbol gap of 9,780,000 ms (about 2 hours 43 minutes). Only 48 rows contain the full raw hourly macro values and 24 rows pass macro timestamp alignment.

**Finding:** the current scheduled capture is not sufficiently continuous for a formal replay or a 24/7 operational claim. A persistent, monitored worker with durable storage, heartbeat, restart policy, alerting and tested recovery is required before collecting a qualifying forward dataset.

## 5. Current profitability evidence

The latest reconciled Paper audit reports 46 closed round trips, 5 wins, 41 losses, net realized PnL of -1.2856629601 USDT, simulated fees of 0.45717992 USDT and aggregate net profit factor of approximately 0.0784. All six traded symbols in that sample have negative net PnL.

Accounting reconciliation passed, but the performance result is negative. Reconciliation PASS means the ledger arithmetic agrees; it does not mean the strategy passes profitability.

## 6. Risk/security GO-NO-GO

| Gate | Result |
|---|---|
| Paper-only configuration and execution locks | PASS for inspected baseline |
| Paper accounting reconciliation | PASS |
| Strategy positive expectancy | FAIL / not established |
| Historical replay data quality | FAIL / not ready |
| Continuous 24/7 worker and recovery | NOT IMPLEMENTED / not verified |
| Sealed independent Holdout | NOT AVAILABLE |
| Independent security review | NOT COMPLETED |
| Real exchange protective-order proof | NOT AVAILABLE |

**Decision: NO-GO for Testnet order execution and Mainnet trading.** Continue public-data Paper research only. No real funds, API trading permissions, or live execution should be enabled based on this audit.
