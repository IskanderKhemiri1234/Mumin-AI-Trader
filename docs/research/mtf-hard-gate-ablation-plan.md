# MTF Hard-Gate Ablation — Preregistered Paper Research Plan

## Status

Design only. No production strategy change, no live execution, and no gate has been bypassed in the running system. This experiment is isolated from `main` and PR #25.

## Evidence motivating the hypothesis

Entry Scarcity Run #5 evaluated 8 symbols, 9,940 closed 1h candles per symbol, and spread scenarios of 5/10/20/30 bps. At 5 bps, among 60,994 HOLD observations, the fixed-priority exclusive attribution assigned 51,134 (83.83%) to missing simultaneous 4h+1d bullish confluence. At 10 bps the count was 51,134 of 61,007 HOLD observations (83.82%).

The HOLD-candle MTF state distribution at 5 bps was:
- both 4h and 1d bullish: 9,860 (16.17%)
- only 4h bullish: 11,546 (18.93%)
- only 1d bullish: 5,476 (8.98%)
- neither bullish: 34,112 (55.93%)

The exclusive attribution is priority-based, not causal. MTF failures overlap materially with other failures: among 5 bps HOLDs, 45,453 MTF-failing observations also failed the score threshold and 42,654 also failed expected-move/cost coverage. Therefore 83.83% must not be interpreted as the number of trades recoverable by removing MTF.

## Single preregistered hypothesis

**H1:** The simultaneous 4h+1d bullish *hard veto* is overly restrictive and may suppress otherwise valid entries. Removing only that hard-veto check may improve out-of-sample opportunity count without unacceptable deterioration in net performance or drawdown.

This is deliberately narrower than removing MTF from the strategy. The 4h/1d indicators, confluence calculation, score bonuses, strategy-branch selection, and all other MTF-related logic remain identical. Only the final hard eligibility veto `if not mtf_confluence: eligible = False` is disabled in the research-only candidate. This isolates the hard-veto contribution; it does not test removing MTF information from scoring.

## Paired experiment

- Baseline: byte-for-byte current strategy behavior.
- Candidate: same strategy with only the hard MTF eligibility veto disabled in a research-only adapter/copy. Do not edit the production strategy module.
- Identical closed candles, timestamps, symbol order, spreads, fees, slippage, account seed, and Shield inputs for baseline and candidate.
- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base interval: 1h; higher-timeframe inputs: 4h and 1d, using only fully closed candles available at the base-candle close.
- Spread scenarios: 5 and 10 bps for the primary acceptance test. 20/30 bps remain negative-control scenarios; the existing hard spread ceiling must continue to block entries.
- Capital: 70 USDT per independent symbol/variant series; compounding disabled.
- Risk and costs remain fixed: SL 0.50%; TP unchanged at 1.25–1.50%; fees 0.10% per side; slippage 2 bps per side; Shield enabled; production ATR band 0.08–1.20%.
- Paper/historical simulation only. No exchange order endpoints, live credentials, or live execution.

## Chronological split (no randomness)

Use the 9,940-bar per-symbol history from Run #5, preserving time order:
- First 70% (6,958 bars): warm-up/development segment.
- Next 15% (1,491 bars): validation and pipeline verification only; no parameter tuning.
- Final 15% (1,491 bars): untouched out-of-sample holdout.

For the current Run #5 date range, the approximate split boundaries are 2026-07-13 01:00 UTC and 2026-09-13 04:00 UTC; the implementation must derive exact indices from each symbol's sorted unique closed-candle timestamps and report exact inclusive/exclusive boundaries. All indicators receive only historical warm-up data available before each evaluated candle. No random shuffling, resampling, threshold search, or holdout-driven revisions.

## Preregistered acceptance criteria

Evaluate each spread scenario independently. The candidate passes only if, at both 5 and 10 bps:
1. Aggregate candidate net return is positive.
2. Candidate aggregate net return exceeds its paired baseline.
3. Candidate mean/max drawdown does not exceed baseline (report both; acceptance uses max drawdown).
4. Candidate has at least 30 closed trades across the 8 independent symbol series.
5. At least 5 of 8 candidate symbol series have positive net PnL.
6. The 20/30 bps negative controls still produce zero entries under the unchanged spread ceiling.

A failure of any criterion rejects H1 for this fixed test. Do not relax acceptance criteria after seeing holdout results. More trades alone are not evidence of improvement.

## Required report

Report baseline and candidate side by side by spread and symbol:
- closed trades, wins/losses, win rate, net PnL, net return, fees, max drawdown, profit factor, and Shield blocks;
- incremental candidate entries and whether each passes every unchanged non-MTF gate;
- MTF state (both/only 4h/only 1d/neither) for each incremental candidate;
- exact holdout boundaries and candle counts;
- all preregistered acceptance flags and final pass/fail;
- explicit limitations and a checksum/commit SHA for reproducibility.

## Stop conditions and governance

- No production changes, no edits to `main`, and no merge.
- Keep PR #25 open, Draft, and unmerged.
- Keep the 70 USDT Paper reference, SL 0.50%, TP 1.25–1.50%, and ATR 0.08–1.20% unchanged.
- If the candidate breaches a risk limit, increases max drawdown, or fails the acceptance criteria, reject it; do not compensate by weakening other gates.
- This plan is a hypothesis, not evidence that the candidate is profitable. Implementation and execution must remain on this isolated research branch.
