# RSI Final Entry-Band Ablation — Run 12 Results

## Decision

**REJECT the widened 45–70 Momentum RSI final-entry band.** Keep the production Momentum RSI eligibility band at 48–68. The candidate generated five additional closed Paper trades but reduced net PnL, increased drawdown, and produced fewer profitable symbol series. No production configuration or risk setting was changed.

## Frozen comparison

- Baseline final Momentum eligibility: `48 <= RSI <= 68`.
- Candidate final Momentum eligibility: `45 <= RSI <= 70`.
- The only changed predicate is the final Momentum RSI eligibility band.
- The Momentum RSI quality bonus remains tied to the original 48–68 band in both variants.
- Recovery setup, Recovery RSI (35–62), and Recovery scoring remain unchanged.
- Momentum floor `momentum_3_bps > 5`, 10bps minimum net edge, Expected-Move final buffer, MTF hard veto, ATR 0.08–1.20%, spread ceiling, Shield, sizing, SL/TP selection, fees, slippage, and simulator remain unchanged.

## Data and Paper protocol

- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- 1h base candles, with fully closed 4h and 1d higher-timeframe context.
- 9,940 closed 1h candles per symbol.
- Chronological split per symbol: 6,958 development, 1,491 validation, 1,491 untouched final holdout.
- Development: 2025-09-26 09:59:59.999 UTC to 2026-07-13 06:59:59.999 UTC.
- Validation: 2026-07-13 07:59:59.999 UTC to 2026-09-13 09:59:59.999 UTC.
- Holdout: 2026-09-13 10:59:59.999 UTC to 2026-11-14 12:59:59.999 UTC.
- Each symbol/variant uses an independent 70 USDT Paper account; compounding disabled.
- Fees 0.10% per side; slippage 2bps per side; Shield enabled; SL 0.50%; strategy-selected TP unchanged (1.25–1.50%); production ATR 0.08–1.20%.
- No live orders; production strategy, production risk limits, and execution allowlist unchanged.

## CI quality gates

All gates passed on workflow run 43601386188:
- `python -m compileall -q src scripts`: PASS.
- Full `pytest -q`: **67 passed** (one unrelated Starlette/httpx deprecation warning).
- Ruff: **All checks passed**.
- Frozen paired Paper holdout: completed successfully.
- Artifact upload: completed successfully.

The earlier initial CI attempt failed three tests and correctly stopped before the holdout. Those test issues were corrected; the successful run above is the final verified result for this report.

## Final holdout results

### 5bps spread

| Metric | Baseline 48–68 | Candidate 45–70 |
|---|---:|---:|
| Closed trades | 20 | 25 |
| Net PnL | +0.03882825 USDT | +0.00313099 USDT |
| Net return on 560 USDT aggregate initial capital | +0.006934% | +0.000559% |
| Profitable symbol series | 4/8 | 2/8 |
| Max symbol-series drawdown | 0.044002% | 0.067999% |

Candidate delta: **−0.03569726 USDT** versus baseline. Drawdown rose by approximately 54.5%.

### 10bps spread

| Metric | Baseline 48–68 | Candidate 45–70 |
|---|---:|---:|
| Closed trades | 20 | 25 |
| Net PnL | +0.03188040 USDT | −0.01118993 USDT |
| Net return on 560 USDT aggregate initial capital | +0.005693% | −0.001998% |
| Profitable symbol series | 4/8 | 2/8 |
| Max symbol-series drawdown | 0.045079% | 0.069792% |

Candidate delta: **−0.04307034 USDT** versus baseline. Drawdown rose by approximately 54.8%.

### Six strict acceptance criteria

| Criterion | 5bps | 10bps |
|---|---|---|
| Candidate aggregate holdout net return positive | PASS | FAIL |
| Candidate beats baseline net return | FAIL | FAIL |
| Candidate max drawdown no higher than baseline | FAIL | FAIL |
| At least 30 closed trades | FAIL (25) | FAIL (25) |
| At least 5/8 profitable symbol series | FAIL (2/8) | FAIL (2/8) |
| Zero entries at 20/30bps for both variants | PASS | PASS |
| **Overall** | **REJECT** | **REJECT** |

Both 20bps and 30bps negative controls produced zero entries for every symbol in both variants.

## Interpretation

The wider band increased closed trades from 20 to 25 at both primary spreads, but those five extra trades did not improve the portfolio outcome. At 5bps the candidate remained marginally positive but earned substantially less than baseline; at 10bps it turned negative. Profitable symbol series fell from four to two, and maximum drawdown increased by roughly 55%.

The diagnostic ledger also shows substantial overlap between RSI-band failures and other gates (notably MTF, score, and Expected Move). These are overlapping counts, not mutually exclusive causal attribution. Candidate-only assessments must not be conflated with filled Paper orders or closed trades.

**Research conclusion:** reject the RSI widening hypothesis. Retain 48–68 as the production Momentum RSI final eligibility band. Do not promote this candidate or alter `main`.

## Reproducibility

- Workflow: https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43601386188
- Artifact: `rsi-entry-band-oos-12` (artifact ID `14116800692`)
- Artifact ZIP SHA-256: `ee7b9fa00cd7cb6de5702ac79de3244e5f737e09bd56640efd50bd43ae1af6eb`
- JSON SHA-256: `bd18bcb13689965da9d3ebc65299b76daf35b2df56d49c52b6a20565f0237a5c`
- Generated: 2026-11-14T13:48:05.809873+00:00
