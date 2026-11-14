# Three-Candle Momentum Floor Ablation — Preregistered Paper Research Plan

## Status

Design-only at branch creation. No code has been changed to production strategy, no test has been run, and no result exists yet. This branch is independent of the rejected MTF hard-gate ablation and is based on the Entry Scarcity diagnostic branch.

## Research question

Run a single-factor, paired counterfactual to determine whether the strict three-candle momentum floor in the MOMENTUM branch rejects otherwise acceptable entries:

- Baseline: existing MOMENTUM eligibility requires `momentum_3_bps > 5`.
- Candidate: only the final MOMENTUM-branch eligibility floor changes to `momentum_3_bps > 0`.

This is **not** a general loosening of strategy quality. The existing +10 momentum-score bonus remains awarded only when momentum_3 is strictly greater than 5 bps. RECOVERY branch logic is unchanged. No score threshold, RSI range, expected-move test, MTF rule, ATR band, spread ceiling, Shield rule, sizing, exit, stop, target, or execution behavior may change.

## Why this is the next experiment

Entry Scarcity Run #5's fixed-priority exclusive attribution assigned about 6.0% of HOLD observations to the 3-candle momentum floor (3,663/60,994 at 5 bps and 3,657/61,007 at 10 bps). This is a priority label, not causal attribution: failures overlap, and MTF/score/expected-move gates often fail on the same candle. Therefore the experiment changes only the MOMENTUM branch's final hard floor, preserves the +10 score bonus at >5 bps, and reports every unchanged gate for incremental signals.

The rejected MTF hard-gate experiment is not reopened. Its conclusion remains: preserve simultaneous 4h+1d MTF hard veto.

## Paired experiment and data

- Source: public Binance historical closed candles only; no authenticated account endpoints.
- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base timeframe: 1h; higher-timeframe inputs: 4h and 1d.
- Request 9,940 closed 1h candles per symbol and matching closed 4h/1d histories; timestamp-align higher-timeframe candles and use only those fully closed by the base candle close.
- Identical candles, timestamps, symbol order, spread assumptions, fees, slippage, independent starting balances, Shield inputs, and Paper engine for baseline and candidate.
- Primary spread scenarios: 5 and 10 bps. Negative controls: 20 and 30 bps.
- Each symbol/variant has an independent 70 USDT Paper account; compounding off.
- Fees: 0.10% per side; slippage: 2 bps per side; Shield on.
- Fixed SL: 0.50%; strategy-selected TP remains 1.25%-1.50%; production ATR band remains 0.08%-1.20%.
- No live orders, exchange execution, private keys, or production Paper allowlist changes.

## Frozen chronological split

For each symbol's sorted 9,940 closed 1h candles, no shuffling:
- Development/warmup: first 6,958 bars (70%).
- Validation/pipeline verification: next 1,491 bars (15%); no threshold tuning.
- Final untouched holdout: last 1,491 bars (15%).
- Derive exact per-symbol inclusive/exclusive timestamp boundaries from downloaded candles and include them in the report. The final holdout must end at the last closed candle available to this run, no later than 2026-11-14 UTC.
- Indicator warmup may use only historical candles available before each evaluated candle. No future HTF candle may be used.

The threshold values (baseline >5 bps, candidate >0 bps) and all acceptance criteria are frozen before holdout inspection. No additional candidate values or threshold grid search.

## Preregistered acceptance criteria

Evaluate 5 and 10 bps independently. Candidate is accepted only if **all** criteria pass at both spreads:
1. Aggregate candidate holdout net return is positive.
2. Candidate aggregate net return exceeds paired baseline.
3. Candidate max drawdown is no greater than baseline; report both maximum and mean of per-symbol drawdowns.
4. At least 30 closed trades across the eight independent symbol series.
5. At least 5 of 8 symbol series have positive net PnL.
6. Both 20 and 30 bps negative controls produce zero Paper entry orders for baseline and candidate.

Any failed criterion rejects the candidate. More entries or a higher trade count alone are not evidence of improvement. Do not alter thresholds or criteria after viewing holdout results.

## Required reporting

For each spread, symbol, and variant:
- closed trades, wins/losses, win rate, net PnL, net return, fees, max drawdown, profit factor, Shield blocks, BUY candidates, and Paper entry orders;
- incremental candidate signals and whether each passes all unchanged non-momentum gates;
- selected strategy branch and momentum_3/12, score, RSI, MTF state, ATR, expected move and required move for every incremental signal;
- number of incremental signals with momentum_3 in (0, 5] bps, Shield blocks, and entries actually placed by Paper engine;
- exact candle counts and timestamp boundaries for each split;
- six acceptance flags per spread, negative-control results, final pass/fail, limitations, source commit and artifact checksum.

## Governance and stop conditions

- Do not edit `main`, production strategy behavior, PR #25, live execution, or execution allowlists.
- Keep PR #25 open, Draft, and unmerged.
- Keep 70 USDT per independent Paper symbol/variant, SL 0.50%, TP rule unchanged, ATR 0.08%-1.20%, fees/slippage and Shield unchanged.
- Preserve the MTF hard gate exactly.
- If max drawdown rises, any risk limit is breached, or any acceptance criterion fails, reject the candidate. Do not compensate by weakening another gate.
- A passing historical result is not proof of future profitability and does not authorize live trading.
