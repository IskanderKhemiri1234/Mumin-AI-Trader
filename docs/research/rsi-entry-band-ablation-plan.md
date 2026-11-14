# RSI Final Entry-Band Ablation — Frozen Research Plan

## Status
Plan only. No implementation, workflow, tests, or holdout execution has been performed yet.

## Research question
Does a modest widening of the final Momentum RSI eligibility band from 48–68 to 45–70 produce useful additional Paper entries without degrading final-holdout net performance, drawdown, or cross-symbol consistency?

## Why RSI is next
The prior Entry Scarcity Diagnostic Run 5 attributed approximately 4.07% of HOLD observations at 5 bps to RSI under a fixed-priority exclusive-reason ledger. Expected-Move was approximately 5.38%. This is prioritization evidence only, not causal attribution; overlapping gate failures will be measured in this study.

## Frozen single-variable hypothesis
- Baseline final Momentum RSI eligibility: 48 <= RSI <= 68.
- Candidate final Momentum RSI eligibility: 45 <= RSI <= 70.
- The only variant difference is the final Momentum eligibility band.
- Apply the variant band only to the final Momentum eligibility predicate. Do not alter RSI calculation, score contributions, Recovery setup, Recovery RSI condition, or any other gate.
- The existing +10 Momentum RSI quality bonus remains tied to 48 <= RSI <= 68 for both variants.
- Existing Recovery RSI quality bonus remains 35 <= RSI <= 62 for both variants.
- Existing 10 bps minimum net-edge threshold, Expected-Move final buffer of 10 bps, 3R target selection, momentum_3_bps > 5 floor and +10 momentum bonus, MTF hard veto, ATR band, spread ceiling, Shield, fees/slippage, sizing, stops/targets, and simulator behavior remain unchanged.
- Implement the candidate as a research-only final-gate override. Default production behavior remains 48–68 and must be byte-for-byte behaviorally equivalent in the default path.
- Both variants use identical candles, indicators, score calculations, target selection, Paper accounts, and costs. Only final Momentum RSI eligibility differs.

## Dataset and frozen split
- Symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base timeframe: 1h; higher timeframe confirmations: fully closed 4h and 1d only.
- Exactly 9,940 closed 1h candles per symbol, same source/window as the preceding paired ablations. Fail closed if any symbol lacks the required count; do not shift dates or replace symbols.
- Chronological split per symbol: 6,958 development/warmup (70%), 1,491 validation (15%), 1,491 final untouched holdout (15%).
- Validation is for data/pipeline checks only, not threshold tuning.
- Freeze exact UTC start/end boundaries and dataset checksums in the run artifact before final evaluation. Evaluate the final holdout once after code/tests are frozen.

## Spread scenarios
- Primary: 5 bps and 10 bps.
- Negative controls: 20 bps and 30 bps; both variants must generate zero Paper entry orders.

## Paper-only risk and cost controls
- Independent 70 USDT Paper account for every symbol/variant combination; no shared balances and no compounding.
- Fees: 0.10% per side.
- Slippage: 2 bps per side.
- Shield enabled.
- Stop loss: 0.50%.
- Strategy-selected take-profit remains unchanged (2.5R/3R as currently selected).
- Production ATR band remains 0.08%–1.20%.
- No live exchange orders, no private exchange keys, no real execution.

## Frozen acceptance criteria (all six required at each primary spread)
1. Candidate aggregate final-holdout net PnL is positive.
2. Candidate aggregate final-holdout net PnL beats paired baseline.
3. Candidate maximum symbol-series drawdown is no higher than baseline.
4. Candidate closes at least 30 trades.
5. At least 5 of 8 candidate symbol series are profitable.
6. Both 20 bps and 30 bps negative controls have zero Paper entry orders for both variants.

Any failed criterion rejects the candidate. No post-hoc threshold tuning, no repeated holdout selection, no production promotion on partial success.

## Required reporting
Report per symbol, spread, and variant: candidate/baseline signal counts, pre-Shield BUY assessments, Shield blocks/passes, Paper entry orders, closed trades, wins/losses, win rate, gross/net PnL, fees, return, profit factor, maximum drawdown, profitable-series status, RSI distributions, baseline/candidate final RSI eligibility decisions, momentum and strategy branch, score, Expected Move, target, estimated costs, MTF state, ATR, all unchanged gates, candidate-only assessments, overlapping RSI and other gate failures, exact split boundaries, dataset/code checksums, and all six acceptance flags.

## Governance
- Research branch only: `research/rsi-entry-band-ablation`.
- Parent snapshot: `research/entry-scarcity-mtf-momentum` at `0be0fac2b3bc82ce7cdf3f4b5b34c7542e2b1da5`.
- Do not change `main`, production config, risk limits, allowlists, or PR #25.
- PR #25 remains Draft, open, unmerged, base `main`.
- Research output is not a profitability guarantee or authorization for live trading.
