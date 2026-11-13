# Preliminary invention disclosure: Liquidity-State Anti-Loss Shield

**Working title:** Anti-Loss Quantum Shield (ALQS)  
**Status:** Engineering disclosure draft for prior-art review; not a patent filing, legal opinion, or finding of novelty.

## 1. Abstract

A computer-implemented trading risk-control method evaluates each instrument's market quality before authorizing a new simulated or executable entry. The method forms a liquidity-state vector from bid/ask spread, quote-volume deviation from a rolling baseline, trade-count deviation from a rolling baseline, short-horizon price shock, and market-data freshness. A deterministic state machine maps the vector to normal, caution, defensive, critical, data-guard, or lockdown states. Depending on the state, the system blocks new entries and may reduce an existing position in stages, but only when the observed spread remains within a configured executable threshold. If the market is too wide or the measurements are missing or invalid, the system blocks entries and avoids forcing an uneconomic exit. Risk decisions and their input measurements are persisted for audit and subsequent research.

## 2. Technical field

Electronic trading systems, pre-trade risk controls, liquidity anomaly detection, portfolio exposure management, and computer-implemented execution safeguards.

## 3. Problem addressed

Price-only stop-loss controls may react after a price threshold has already been crossed. A market can also become difficult to exit because spreads widen, quoted liquidity collapses, trade flow changes abruptly, or the data feed becomes stale. An immediate market exit during a severe spread expansion may itself produce an adverse fill. The disclosed method therefore separates (a) blocking additional exposure, (b) staged exposure reduction when execution quality permits, and (c) avoiding forced liquidation when execution conditions are materially impaired.

## 4. Proposed technical mechanism

For each symbol and completed market observation, calculate:

- spread in basis points from bid, ask, and midpoint;
- quote-volume ratio against a rolling median of prior closed observations;
- trade-count ratio against a rolling median of prior closed observations;
- one-observation price change in basis points;
- observation age in milliseconds.

The decision engine validates every input. Missing volume or trade-flow baselines produce a data-guard state that blocks entries. Invalid or non-finite inputs produce lockdown. Otherwise, configured anomaly thresholds generate a per-symbol state:

| State | Entry action | Existing-position action |
|---|---|---|
| NORMAL | Permit strategy evaluation | Normal position management |
| CAUTION | Block new entries | No forced reduction |
| DEFENSIVE | Block new entries | Reduce a configured fraction only if spread is executable |
| CRITICAL | Block new entries | Reduce only if spread permits; otherwise avoid forced bad fill |
| DATA_GUARD / LOCKDOWN | Block new entries | No assumption that missing data is safe |

A separate account-level daily-loss and drawdown controller remains authoritative. A symbol-level shield decision cannot override the account-level halt.

## 5. Example implementation in Mumin AI Trader

- Decision engine: `src/mumin_trader/risk/anti_loss_quantum_shield.py`
- Runtime integration and durable risk-event records: `scripts/run_continuous_paper.py`
- Historical evaluation and ablation: `src/mumin_trader/research/walk_forward.py`
- Tests: `tests/unit/test_anti_loss_quantum_shield.py` and `tests/test_continuous_paper.py`

The current implementation is a deterministic rule-based Paper control, not a quantum-computing system and not a guarantee that losses cannot occur.

## 6. Draft claim concepts for attorney review

1. A computer-implemented method comprising: receiving per-instrument bid/ask, closed-candle quote-volume, trade-count, price-change, and timestamp data; deriving a liquidity-state vector using rolling historical baselines; validating the vector; assigning a discrete market-quality state; and gating new position entries according to the assigned state.
2. The method of claim 1, further comprising selecting a partial position-reduction fraction based on the assigned state and permitting that reduction only when a measured execution-spread condition is satisfied.
3. The method of claim 2, wherein a critical spread condition suppresses forced market liquidation while maintaining an entry lock, thereby distinguishing an exposure-entry circuit breaker from an execution-constrained liquidation controller.
4. The method of claim 1, wherein missing or invalid liquidity measurements cause a fail-closed entry state rather than an assumed normal state.
5. The method of claim 1, further comprising persisting the state, input ratios, observation age, selected action, and reason codes in a durable per-symbol risk-event ledger.
6. The method of claim 1, wherein per-symbol entry gating operates in conjunction with a separate account-level daily-loss and portfolio-drawdown controller.

These are drafting concepts only. They are not a conclusion that the subject matter is novel, non-obvious, patentable, or free of third-party rights.

## 7. Known prior-art landscape requiring claim differentiation

A preliminary search found earlier disclosures covering trading anomaly detection, order-flow/volume analysis, automated trading halts, circuit breakers, and risk-threshold controls, including:

- US10169816B2, *Trading anomaly kill switch*: https://patents.google.com/patent/US10169816B2/en
- US20160196606A1, *Trading anomaly kill switch*: https://patents.google.com/patent/US20160196606A1/en
- US20220230247A1, *Risk mitigation in an electronic trading system*: https://patents.google.com/patent/US20220230247A1/en
- FIA, *Best Practices for Automated Trading Risk Controls and System Safeguards* (2024): https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf

Accordingly, broad claims to “detect abnormal volume and stop trading” would face substantial prior-art risk. A professional search should test the narrower combination of fail-closed data validation, per-symbol state transitions, execution-spread-constrained staged de-risking, and durable reason-coded risk events.

## 8. Experimental validation plan

Use chronologically ordered closed candles and explicit spread scenarios. Compare shield-enabled and shield-disabled runs over identical out-of-sample folds. Report entry blocks, trades, net return after fees/slippage, maximum drawdown, loss-tail outcomes, missed recoveries, and the cost of defensive exits. Include stale-data, missing-volume, spread-widening, volume-collapse, and abrupt-price-shock cases. No finite backtest can establish absolute protection or future performance.
