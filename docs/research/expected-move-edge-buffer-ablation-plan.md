# Expected-Move Edge Buffer Ablation — Frozen Research Plan

## Status and decision boundary

- Branch: `research/expected-move-edge-buffer`
- Parent: `research/entry-scarcity-mtf-momentum` (entry-scarcity diagnostic baseline).
- This is a new, isolated research branch. It is not based on the rejected three-candle branch.
- Research/Paper simulation only. No live orders, exchange execution, private API keys, or production deployment.
- PR #25 remains Draft, open, and unmerged.
- No changes to `main`, production strategy configuration, production risk limits, or execution allowlists.
- The accepted production three-candle momentum rule remains `momentum_3_bps > 5`; this study does not alter it.
- Capital reference remains 70 USDT per symbol and variant in independent Paper accounts; compounding off.
- Fixed stop remains 0.50%; the strategy-selected take-profit rule remains unchanged at 1.25%-1.50%; production ATR band remains 0.08%-1.20%; fees 0.10% each side; slippage 2 bps each side; Shield enabled.

## Why Expected Move is the next diagnostic

The entry-scarcity diagnostic's exclusive primary-rejection attribution assigned approximately 5.38% of HOLD candles at 5 bps and 5.41% at 10 bps to the expected-move requirement. RSI was approximately 4.07% in the same priority-based attribution. Expected Move is therefore the next higher-ranked hypothesis to investigate.

Important limitation: exclusive primary-rejection labels use a fixed priority and are not causal attribution. A candle assigned to another primary reason may also fail Expected Move. The new study must report overlapping gate failures and paired incremental signals separately; the old percentages are prioritization evidence only, not proof that the Expected-Move gate is unnecessarily restrictive.

## Frozen hypothesis

Question: does a modest reduction in the final Expected-Move edge buffer increase useful Paper entries without degrading out-of-sample net performance or drawdown?

Only the final hard eligibility comparison is varied:

- Baseline: `expected_move_bps >= selected_target_bps + estimated_cost_bps + 10`.
- Candidate: `expected_move_bps >= selected_target_bps + estimated_cost_bps + 5`.

This is a 5 bps reduction in the final eligibility buffer only.

The following remain unchanged in both variants:
- The strategy's `minimum_net_edge_bps=10` used for scoring/quality bonuses.
- The +20 MOMENTUM and +15 RECOVERY score components tied to the existing 10 bps net-edge threshold.
- The 3R target-selection rule and its original 10 bps edge-buffer requirement.
- Three-candle momentum floor `momentum_3_bps > 5` and its +10 quality bonus.
- RSI bands, local EMA/trend checks, MTF hard veto, score threshold, spread ceiling, ATR band, Shield, sizing, fees, slippage, stop/target mechanics, and every other strategy gate.
- RECOVERY setup logic and strategy-branch selection.

Implementation must expose the candidate as a research-only final-gate override. It must not alter the default production behavior or any production configuration. The two variants must use identical candles, strategy scores, selected branches, risk/reward targets, costs, and Paper simulator; only the final Expected-Move buffer comparison may differ.

## Data universe and chronological protocol

- Symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base timeframe: 1h.
- Higher-timeframe confirmation: fully closed 4h and 1d candles only.
- Target: 9,940 closed 1h candles per symbol, matching the previous paired studies. If a source cannot supply the frozen count, fail closed rather than silently shorten or shift the split.
- Chronological split per symbol: 70% development/warm-up (6,958), 15% validation (1,491), 15% final untouched holdout (1,491).
- Validation is for pipeline/data-integrity verification only; no threshold selection or tuning.
- The final holdout is evaluated once after the implementation and all tests are frozen. No post-hoc tuning.
- Exact UTC start/end timestamps are derived from the fetched closed-candle timestamps and written into the result report.
- Higher-timeframe data must be timestamp-aligned; no 4h/1d candle may be used before its close timestamp.

## Spread scenarios and Paper ledger

- Primary evaluation spreads: 5 and 10 bps.
- Negative controls: 20 and 30 bps.
- 70 USDT reference capital per symbol/variant, independent isolated Paper ledgers.
- Compounding disabled.
- Buy fee 0.10%, sell fee 0.10%; slippage 2 bps per side.
- Shield on.
- Fixed SL 0.50%.
- Existing target rule unchanged (strategy-selected 1.25%-1.50%).
- ATR band 0.08%-1.20%.
- No live exchange orders or execution.

## Frozen acceptance criteria

At each primary spread (5 and 10 bps), the candidate must satisfy all six:

1. Candidate aggregate final-holdout net PnL is positive.
2. Candidate aggregate final-holdout net PnL beats baseline.
3. Candidate maximum symbol-series drawdown is no higher than baseline.
4. Candidate has at least 30 closed trades.
5. At least 5 of 8 candidate symbol series are profitable.
6. Both 20 and 30 bps negative controls produce zero Paper entries for both baseline and candidate.

Any failed criterion rejects the candidate. No threshold changes, alternate scoring, or acceptance-rule edits after holdout inspection. Passing these historical checks would not prove future profitability or authorize production/live deployment.

## Required paired reporting

For every symbol, spread, and variant:
- Signal counts, BUY candidates before Shield, Shield blocks, final Paper entry orders, closed trades, wins/losses, win rate, gross/net PnL, fees, return, profit factor, max drawdown, and profitable-series status.
- Exact split counts and UTC timestamp boundaries.
- Expected move, selected target, estimated costs, configured net-edge buffer, final required move, strategy branch, score, RSI, momentum_3/12, MTF, ATR, and all unchanged gate outcomes.
- Incremental candidate-only BUY assessments and whether each passed Shield and became a Paper fill.
- Overlapping Expected-Move failures, not only exclusive primary rejection counts.
- Six acceptance flags at 5 and 10 bps, negative-control counts at 20/30 bps, data checks, checksums, and limitations.

## Interpretation and governance

This is a one-factor research ablation of the final Expected-Move eligibility buffer, not a broad loosening of the strategy. If the candidate raises entries but fails any acceptance criterion, reject it and retain the 10 bps final buffer. Do not alter RSI, momentum, MTF, ATR, spread, score, risk, or target rules in response to this run. Any later RSI study must use a separate branch and a separately frozen plan.

No merge, production promotion, or live trading is authorized by this experiment.
