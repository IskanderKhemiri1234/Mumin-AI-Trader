# Binance Spot Testnet OPOCO Runbook

## Scope and hard boundaries

This runbook applies only to Binance Spot Testnet. The execution adapter is pinned to the Testnet REST base URL and refuses Mainnet fallback. Mainnet and live trading remain disabled.

The OPOCO runner is implemented but must not be treated as proof of exchange execution until a separately approved Testnet run has completed and its evidence has been reviewed. CI and mocked transport tests do not submit exchange orders.

## OPOCO behavior

The runner submits one `POST /api/v3/orderList/opoco` request containing:

- A working BUY LIMIT order.
- A pending SELL LIMIT_MAKER take-profit leg.
- A pending SELL STOP_LOSS_LIMIT stop leg.

The pending OCO legs are placed by Binance only after the working BUY is fully filled. Binance uses the received amount, applies commission deductions as appropriate, and evaluates pending-order filters when the working order completes. Symbol support requires `otoAllowed`, `opoAllowed`, and `ocoAllowed` in Exchange Information. See the [Binance Spot Testnet REST documentation](https://developers.binance.com/en/docs/products/spot/testnet/rest-api) and [OPO FAQ](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/opo.md).

The approved exit policy is fixed at a **0.50% stop trigger** and **1.25% take-profit** relative to the working limit price (with a 0.02 percentage-point validation tolerance). After execution, the runner recomputes both percentages from the actual average fill price. A better-than-limit fill can change the realized percentages; if the actual fill moves either exit outside policy tolerance, the cycle remains NO-GO even if the protective list is active.

**5 USDT minimum-notional constraint:** if a symbol has a 5 USDT minimum notional and the stop-limit price is 0.50% below the entry, a 5 USDT BUY can produce a stop leg below the exchange minimum. The runner refuses the list before submission. It does not silently increase the 5 USDT trade cap. A one-run Testnet-only cap override is an explicit operator action and must be justified by the symbol's actual filters; it is not a production risk-policy change.

**Partial-fill limitation:** if the working BUY is only partially filled, the pending OCO protection is not yet active. The runner cancels the remaining OPOCO list when possible, halts the local execution ledger, records the partial fill, and returns a manual-review state. It does not pretend the partial inventory is protected and does not automatically sell it. If the working BUY is fully filled but a protective leg partially fills, the runner leaves the remaining protective list active and requires review.

## Read-only inspection

Inspect the current symbol price, exchange filters, OPOCO capability flags, account readiness, and BNB fee readiness without submitting an order:

```bash
python scripts/binance_spot_testnet_lifecycle.py inspect --symbol BTCUSDT
```

A symbol that does not explicitly expose all three OPOCO capability flags is a hard NO-GO.

## Explicitly confirmed Testnet cycle

The `run-opoco` command requires the literal confirmation `TESTNET_ONLY`. It also requires operator-supplied prices and quantity that pass the live Testnet filters. Do not copy stale prices from this document or assume that BTCUSDT supports OPOCO.

The command shape is:

```bash
python scripts/binance_spot_testnet_lifecycle.py run-opoco \
  --symbol BTCUSDT \
  --quantity <base-quantity> \
  --working-price <buy-limit-price> \
  --take-profit-price <take-profit-price> \
  --stop-price <stop-trigger-price> \
  --stop-limit-price <stop-limit-price> \
  --confirm-testnet-cycle TESTNET_ONLY
```

Before any run, independently confirm:
1. Testnet account access and the exact account/symbol BNB discount flags.
2. Free BNB covers the conservative BUY plus one protective exit reserve.
3. OPOCO capability flags are all true for the selected symbol.
4. Initial account/trade reconciliation is clean and FIFO/fee ledger is complete.
5. Working and pending leg filters, notional, price ordering, and risk limits pass.

If any check fails, no OPOCO order list is submitted.

## Continue monitoring

If the working order fills and both pending protective legs are armed but the exit has not happened before timeout, the runner leaves the protective OPOCO list active. Continue monitoring the same persisted list; do not submit another list:

```bash
python scripts/binance_spot_testnet_lifecycle.py watch-opoco \
  --symbol BTCUSDT \
  --list-client-order-id <persisted-list-client-order-id> \
  --database <run-evidence-directory>/testnet-ledger.sqlite3 \
  --evidence-dir <run-evidence-directory> \
  --timeout-seconds 1800
```

This watcher queries the existing list and does not submit a new BUY or OPOCO list.

## Evidence and GO/NO-GO

A cycle is not Verified unless the evidence proves all of the following:

- The working BUY is fully filled.
- The parent order list is terminal (`ALL_DONE`).
- The three child order IDs are distinct and match their persisted client IDs.
- Exactly one protective SELL leg is FILLED and its sibling is CANCELED or EXPIRED.
- Exchange fills match the SQLite FIFO/commission ledger.
- No unmatched trade order IDs, fee-asset mismatches, fill discrepancies, or unexplained inventory remain.
- Final account reconciliation is clean and FIFO open inventory is zero.
- No unprotected dust remains.

An active protective list at timeout is a safe-to-monitor state, not a completed cycle. A partial working fill is a manual-review NO-GO. A passing test suite alone is not exchange evidence.
