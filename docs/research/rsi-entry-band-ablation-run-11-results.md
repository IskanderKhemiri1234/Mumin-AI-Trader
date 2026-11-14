# RSI Final Entry-Band Ablation — Run 11 Results

## Decision

**REJECT the candidate 45–70 band. Retain the production/default Momentum RSI eligibility band of 48–68.**

The paired final holdout produced five additional closed trades (20 to 25), but the candidate lost net performance, increased maximum symbol-series drawdown by about 1.55x, and finished profitable on only 2 of 8 symbol series. It failed five of the six required acceptance criteria at both primary spreads. Only the 20/30 bps negative controls passed.

This is a research-only result. It is not a profitability guarantee and does not authorize live execution.

## Run provenance

- Workflow run: [43601373763](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43601373763)
- Tested code commit: `a3a8b3c9446d534931b87eb889b908409b403cdc`
- Branch: `research/rsi-entry-band-ablation`
- Artifact: `rsi-entry-band-oos-11` (ID `14116003300`)
- Artifact ZIP SHA-256: `25befdb9b41539c2a79fa70086e84bebdb7c55f52a52468ea1537330baa43db7`
- JSON SHA-256: `d79f30b7781a6874ffb03883ea4c8401110c291eae92cefd3b5c387d6e303775`
- Generated: 2026-11-14T13:47:00.769354+00:00

## CI quality gates

All gates passed before the historical simulation:

- `python -m compileall -q src scripts`: PASS
- `pytest -q`: **67 passed**, 1 non-failing dependency deprecation warning
- `ruff check --select E,F,I,UP ...`: PASS
- Paired OOS simulation: PASS
- Artifact upload: PASS

## Frozen comparison

- Baseline: final Momentum eligibility `48 <= RSI <= 68`
- Candidate: final Momentum eligibility `45 <= RSI <= 70`
- The only strategy decision changed was the final Momentum RSI eligibility predicate.
- RSI computation unchanged.
- Momentum RSI quality bonus remained tied to `48 <= RSI <= 68` for both variants.
- Recovery RSI condition remained `35 <= RSI <= 62`; Recovery selection and eligibility unchanged.
- Momentum floor `momentum_3_bps > 5`, +10 momentum bonus, MTF hard veto, expected-move/target/cost gates, ATR, spread, Shield, sizing, fees, slippage, stops, targets and simulator behavior unchanged.
- 8 symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- 1h base candles with closed 4h/1d higher-timeframe confirmations.
- 9,940 closed 1h candles per symbol.
- Chronological split: 6,958 development (70%), 1,491 validation (15%), 1,491 untouched final holdout (15%).
- Holdout window: 2026-09-13T10:59:59.999Z through 2026-11-14T12:59:59.999Z.
- Independent Paper capital: 70 USDT per symbol and variant; no compounding.
- Fees 0.10% each side; slippage 2 bps each side; Shield enabled; stop 0.50%; strategy-selected 2.5R/3R target; ATR 0.08%–1.20%.
- No live orders or real execution.

## Aggregate final-holdout results

Each variant has eight independent 70 USDT symbol accounts (560 USDT aggregate starting reference per spread).

| Spread | Variant | Net PnL | Closed trades | W–L | Profitable symbols | Max symbol DD |
|---|---|---:|---:|---:|---:|---:|
| 5 bps | Baseline 48–68 | +0.038828 USDT | 20 | 6–14 | 4/8 | 0.044002% |
| 5 bps | Candidate 45–70 | +0.003131 USDT | 25 | 7–18 | 2/8 | 0.067999% |
| 10 bps | Baseline 48–68 | +0.031880 USDT | 20 | 6–14 | 4/8 | 0.045079% |
| 10 bps | Candidate 45–70 | -0.011190 USDT | 25 | 6–19 | 2/8 | 0.069792% |

Candidate net-PnL delta versus baseline:
- 5 bps: **-0.035697 USDT**; maximum drawdown increased approximately **1.55x**.
- 10 bps: **-0.043070 USDT**; maximum drawdown increased approximately **1.55x**.

The candidate added five closed trades, but the extra activity did not create a better risk-adjusted or aggregate outcome.

## Six acceptance criteria

| Required criterion | 5 bps | 10 bps |
|---|---|---|
| Candidate aggregate net PnL positive | PASS | FAIL |
| Candidate beats paired baseline | FAIL | FAIL |
| Candidate max drawdown no higher than baseline | FAIL | FAIL |
| At least 30 candidate closed trades | FAIL (25) | FAIL (25) |
| At least 5/8 profitable candidate symbols | FAIL (2/8) | FAIL (2/8) |
| Both 20/30 bps controls have zero entries | PASS | PASS |
| **Overall** | **REJECT** | **REJECT** |

Both negative controls returned zero Paper entry orders for every symbol and both variants.

## RSI gate and overlap diagnostics

Across the 8-symbol holdout:
- Baseline RSI-band rejection observations: 2,255.
- Candidate RSI-band rejection observations: 1,904.
- The wider band reduced RSI-band rejections by 351 observations, but did not translate into comparable incremental eligible entries.
- Paired candidate-only BUY assessments before Shield: 11 at each primary spread.
- Shield blocked 5; 6 passed Shield.
- Final Paper entry orders and closed trades increased by only 5 versus baseline.

The overlapping failure counts show that RSI was often not the only obstacle. Among baseline RSI-band rejection observations, overlaps included:
- MTF non-confluence: 1,688.
- Expected-move gate failure: 1,317 at 5 bps; 1,339 at 10 bps.
- Score below threshold: 1,766 at 5 bps; 1,772 at 10 bps.
- Three-candle momentum at or below 5 bps: 611.
- ATR outside the accepted band: 382.

These are overlapping diagnostics, not mutually exclusive or causal attributions.

## Per-symbol net PnL (USDT)

| Symbol | 5 bps baseline | 5 bps candidate | 10 bps baseline | 10 bps candidate |
|---|---:|---:|---:|---:|
| BTCUSDT | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| ETHUSDT | +0.006852 | -0.009956 | +0.005318 | -0.011990 |
| BNBUSDT | -0.006603 | -0.011655 | -0.007109 | -0.012241 |
| SOLUSDT | +0.005755 | -0.000631 | +0.005452 | -0.001440 |
| XRPUSDT | +0.034372 | +0.034959 | +0.033335 | +0.028143 |
| ADAUSDT | +0.002520 | +0.002520 | +0.000989 | +0.000989 |
| DOGEUSDT | -0.000595 | -0.000595 | -0.002124 | -0.002124 |
| LINKUSDT | -0.003472 | -0.011511 | -0.003980 | -0.012526 |

## Limitations and audit notes

- The JSON records exact 1h split counts and UTC boundaries, but does **not** include raw dataset SHA-256 fingerprints or a full RSI histogram. The artifact and output JSON themselves are checksummed above; these are not substitutes for input-data checksums. Do not describe those missing input fingerprints or histograms as verified.
- The incremental ledger is a per-candle paired strategy assessment. It is not a one-to-one order/fill map; Paper orders and closed-trade deltas are reported separately.
- Spread scenarios are fixed assumptions, not archived historical order-book spreads.
- Paper fills are simulated at candle close and do not reconstruct intrabar stop/target paths.
- Earlier development and validation periods were used as history/warm-up only; no thresholds were tuned on validation or holdout.

## Governance

- Candidate rejected; keep default/production Momentum RSI band at 48–68.
- No changes to `main`, production risk limits, allowlists, or live execution settings.
- PR #25 remains Draft/open/unmerged and targets `main`.
- Research branch retained as an auditable record; no production promotion.
