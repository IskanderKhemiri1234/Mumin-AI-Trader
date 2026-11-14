# Score-Floor Throughput Ablation — Research Charter

**Status: PROPOSED / DESIGN-FROZEN. No code candidate or holdout execution is authorized by this charter alone.**

## 1. Research question

The preceding Relative-Volume Liquidity experiment was rejected. Its sealed holdout showed only 20 closed baseline trades and 14 candidate trades at each primary spread, with 4/8 profitable symbols for both variants. The added liquidity gate blocked seven otherwise eligible BUY candidates per primary spread. This establishes that the extra gate reduced throughput, but does not establish that it was the only or primary cause of the baseline's low trade count.

The next isolated question is whether the heuristic minimum score floor is rejecting otherwise fully qualified setups that already pass every hard strategy, market-quality, MTF, Shield, and cost/risk condition.

**Hypothesis:** a research-only score floor of 70, compared with the unchanged baseline floor of 75, may recover enough otherwise-valid opportunities to improve sample size and asset breadth without changing any hard eligibility or risk control.

This is a falsifiable research hypothesis, not a claim that lower scores are profitable. Strategy scores are heuristic rankings, not calibrated probabilities.

## 2. Baseline and isolated candidate

- **Baseline:** exact current Smart Sniper behavior with `minimum_score=75`.
- **Candidate:** identical code and rules, except a research-only override `minimum_score=70`.
- The candidate may recover only signals that pass every other existing condition and have a final chosen score in [70, 75).
- No second candidate threshold, grid search, dynamic threshold, score reweighting, or post-result retuning is permitted.
- The candidate setting must be inaccessible to production configuration and must default to the baseline value.
- If the code cannot apply the score override without changing production behavior, stop and redesign before data evaluation.

## 3. Iron invariants

These must remain identical between baseline and candidate:

- Mandatory MTF confluence veto; missing or non-confluent higher-timeframe data remains fail-closed.
- Anti-Loss Quantum Shield enabled and unchanged.
- RSI eligibility 48–68 for momentum entries; recovery rules unchanged.
- ATR eligibility 0.08–1.20%.
- Three-candle momentum strictly greater than 5 bps for momentum entries.
- Spread hard ceiling 12 bps and existing cost-aware expected-move / target / net-edge checks.
- Existing EMA, recovery setup, scoring weights, target selection (2.5R/3R), stop, fees, slippage, position sizing, cash reserve, order timing, and execution behavior.
- No changes to liquidity ratios, Shield thresholds, MTF, execution allowlists, account risk limits, or production strategy configuration.
- Paper-only historical simulation; no live orders, Testnet orders, or real capital.
- Fixed reference capital 70 USDT per symbol and per variant; compounding disabled for paired comparison.

## 4. Mandatory diagnostic before candidate implementation

Before any sealed holdout run, instrument the research evaluator to report baseline opportunity attribution, per symbol and per segment:

1. Count of raw strategy assessments and BUY candidates before Shield.
2. Count blocked by Shield, with reason categories.
3. Count failing mandatory MTF, momentum, RSI, ATR, spread, expected-move/cost, and score-floor conditions. Where conditions overlap, report both independent condition counts and a mutually exclusive first-failure waterfall.
4. Distribution of final chosen scores, especially the 70–<75 and 75+ bands.
5. Count of otherwise-qualified score-only near misses in [70,75).
6. Counts by symbol, regime, and strategy path (MOMENTUM / RECOVERY).
7. Confirm no look-ahead, no duplicate signals, and that only fully closed, timestamp-aligned bars are consumed.

The score-floor hypothesis is not worth a sealed run if the frozen development/validation diagnostics show no meaningful score-only near misses. This stop rule must be applied before unsealing holdout results.

## 5. Data, universe, and split

- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base timeframe: 1h; higher-timeframe confirmation: 4h and 1d.
- Require exactly 9,940 chronologically ordered, fully closed 1h candles per symbol, with strict completeness validation.
- Frozen chronological split per symbol: 6,958 development (70%), 1,491 validation (15%), 1,491 sealed holdout (15%).
- Provide 240 prior closed candles as warm-up at each segment boundary; do not score warm-up bars.
- No random shuffling. Indicators and features may use only data available at the decision candle close. Higher-timeframe bars must be closed and timestamp-aligned.
- Evaluate the sealed holdout once, after source, tests, diagnostic gates, and validation are frozen. Do not use holdout to tune or choose parameters.

## 6. Execution assumptions

- Primary spread scenarios: 5 bps and 10 bps.
- Negative controls: 20 bps and 30 bps.
- Fees: 0.10% per side.
- Slippage: 2 bps per side.
- Independent 70 USDT capital reference per symbol/variant; no cross-series sharing and no compounding.
- All results are simulated Paper results, not exchange fills or executable liquidity evidence.

## 7. Frozen acceptance policy

At both 5 bps and 10 bps, the candidate must satisfy every criterion:

1. Positive aggregate sealed-holdout net PnL.
2. Aggregate sealed-holdout net PnL strictly greater than paired baseline.
3. Maximum symbol-series drawdown no greater than paired baseline.
4. At least 30 closed trades in aggregate.
5. At least 5 of 8 symbol series profitable.
6. Per-symbol results and paired comparisons fully reported; no symbol may be omitted.

Negative controls: exactly zero entry orders for both variants across all eight symbols at both 20 bps and 30 bps.

Any failed mandatory criterion rejects and freezes the candidate. Higher aggregate PnL, fewer losses, or higher win rate cannot override failed sample-size, breadth, or risk criteria. Do not retune against the sealed holdout.

## 8. Engineering and audit gates

1. Implement the score override only in the research evaluator, disabled by default and not exposed to production configuration.
2. Add tests proving the baseline default remains 75, candidate only admits scores in [70,75), and every hard gate still vetoes candidate entries.
3. Add causality, closed-candle, MTF, Shield, and deterministic paired-run tests.
4. Run compile, full Pytest, and Ruff on changed files; all must pass before any holdout job can start.
5. Run frozen development and validation diagnostics; apply the pre-registered stop rule before any sealed evaluation.
6. Execute one paired sealed holdout only if every preceding gate passes.
7. Preserve source SHA, data SHA-256, report SHA-256, immutable artifact, per-symbol ledger, and final decision report.

## 9. Branch and production isolation

- Research branch: `research/score-floor-throughput-ablation`.
- Parent baseline: `feature/smart-sniper-validation-final` at branch creation. This preserves the same validated Smart Sniper research code lineage without modifying `main`.
- Do not merge this research branch.
- Do not modify production settings, risk limits, allowlists, or the active baseline.
- Keep PR #25 Draft, open, and unmerged.
- Preserve `research/liquidity-confirmation-ablation` and its rejected report/artifact as a frozen historical audit branch; do not delete or rewrite its evidence.
- Paper-only: no live orders, no Testnet orders, no real capital. The 70 USDT figure is a simulation reference, not a deposit or balance.

## 10. Interpretation limits

A 70-point score is not a 70% probability. A higher trade count is not proof of better expectancy. Small samples, overlapping market regimes, fixed spread assumptions, candle-close fill approximations, and incomplete intrabar path information limit inference. Historical Paper simulation does not guarantee future performance.

## 11. Decision status

This charter defines the next hypothesis only. No score threshold has been changed in production, no candidate has been implemented, and no sealed holdout has been run. The next authorized engineering stage is diagnostic instrumentation and preflight verification on this isolated branch.
