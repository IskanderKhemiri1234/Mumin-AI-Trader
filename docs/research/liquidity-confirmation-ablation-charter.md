# Relative-Volume Liquidity Confirmation Ablation — Research Charter

**Status: CHARTERED / RESEARCH ONLY. No production promotion is authorized.**

## Purpose and hypothesis

Test one narrowly defined, additive entry-quality filter: whether requiring ordinary, non-anomalous closed-candle quote-volume and trade-count activity improves the quality of Smart Sniper entries after the existing strategy gates and Anti-Loss Quantum Shield have passed.

The candidate is deliberately an additional veto only. It must not weaken, bypass, retune, or replace any existing risk or execution protection.

## Frozen baseline and isolated candidate

- **Baseline:** current validated Smart Sniper entry logic, mandatory MTF confluence, existing Shield behavior, and all current risk/execution rules.
- **Candidate:** baseline plus one additional entry confirmation:
  - Calculate quote-volume ratio as the current fully closed candle's quote volume divided by the median quote volume of the immediately preceding 20 fully closed candles.
  - Calculate trade-count ratio identically from trade count.
  - Permit a new entry only when **both** ratios are within the inclusive range 0.80–2.50.
  - Missing, zero, invalid, or non-finite baselines/ratios fail closed for the candidate.
  - Use only information available at the signal candle close; entry remains at the next candle open.
- The 20-bar lookback and 0.80–2.50 bounds are pre-registered constants. No threshold search or parameter fitting is allowed in this experiment.
- Keep the Anti-Loss Quantum Shield enabled and byte-for-byte behaviorally unchanged in both variants. This experiment must not modify Shield thresholds.

## Invariants — unchanged in both variants

- Mandatory MTF final confluence veto; no bypass or configuration exposure.
- RSI eligibility 48–68.
- ATR eligibility 0.08–1.20%.
- Three-candle momentum floor strictly greater than 5 bps.
- Existing score threshold, expected-move/cost buffer, target selection, stop, sizing, Shield, fees, slippage, and execution timing.
- Paper-only simulation; no live orders, testnet orders, or real capital.
- Independent starting capital reference: **70 USDT per symbol per variant**; no cross-series capital sharing and no compounding.
- No execution allowlist changes, production configuration changes, or edits to PR #25.

## Dataset and split

- Research universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base timeframe: 1h; higher-timeframe confirmation: 4h and 1d.
- Target: 9,940 chronologically ordered, fully closed 1h candles per symbol, subject to a strict completeness check.
- Chronological split per symbol: 70% development / 15% validation / 15% sealed final holdout.
- No random shuffling. All rolling medians and indicators must use only past and current closed candles. Higher-timeframe observations must be fully closed and timestamp-aligned.
- Evaluate the sealed holdout once after implementation, tests, and validation are frozen. Do not tune against holdout results.

## Execution-cost scenarios

- Primary: 5 bps and 10 bps spread assumptions.
- Negative controls: 20 bps and 30 bps.
- Fees: 0.10% per side.
- Slippage: 2 bps per side.
- Spreads remain explicit scenario assumptions, not claims of historical order-book observations.

## Mandatory acceptance criteria

The candidate must satisfy **all six** criteria at both primary spread scenarios:

1. Positive aggregate sealed-holdout net PnL.
2. Aggregate sealed-holdout net PnL greater than the paired baseline.
3. Maximum symbol-series drawdown no greater than the paired baseline.
4. At least 30 closed trades in aggregate.
5. At least 5 of 8 symbol series profitable.
6. Exactly zero entries for both variants at 20 bps and 30 bps, for every symbol.

Any failed criterion rejects the candidate. A higher win rate or fewer trades alone is not evidence of improvement. Report per-symbol and aggregate metrics, rejection attribution, trade counts, costs, drawdown, and all six pass/fail results.

## Required engineering and audit controls

- Add an explicit research-only switch/variant path; preserve baseline behavior as the default and do not expose the candidate in production configuration.
- Add tests for inclusive boundaries (0.80 and 2.50), values just outside each boundary, unavailable/zero baselines, closed-candle causality, no look-ahead, and unchanged Shield/MTF behavior.
- Run compile, full pytest, Ruff, deterministic development/validation checks, then the single sealed holdout.
- Store machine-readable ledgers and a final decision report with evaluated source SHA and artifact SHA-256.
- Keep this branch independent from `main`, PR #25, and the frozen MTF ablation branch.

## Interpretation limits

Quote volume and trade count are candle-level activity proxies, not order-book depth, executable liquidity, queue position, or guaranteed fill quality. Fixed spread scenarios do not reconstruct historical bid/ask books. Candle-close simulation cannot infer intrabar stop/target ordering. Results are historical Paper simulations and do not guarantee future outcomes.

## Safety disposition

No live execution, no real-money test, no production threshold changes, no risk-limit relaxation, no allowlist expansion, and no merge to `main` are authorized by this charter. PR #25 remains Draft, open, and unmerged.
