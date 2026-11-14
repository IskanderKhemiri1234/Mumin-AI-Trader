# MTF Final-Confluence Veto Ablation — Run 7 Final Decision

**Decision: REJECTED. Research branch frozen. Do not promote or retune this hypothesis.**

- Workflow: [Run #7](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43635205217)
- Workflow run ID: `43635205217`
- Evaluated source SHA: `8d1b0ab69903b2de48cbf95dfa7660c3f2e95b18`
- Artifact: `mtf-confluence-veto-oos-7` (artifact ID `14129696729`)
- Artifact SHA-256: `f5aafe84db3df047ddc253727f60a357728334aaf4ae7fe2ce53fe73b9a6e202`
- Generated at: `2026-11-14T20:02:26.569456+00:00`
- Research-only, Paper-only; live orders disabled.

## Frozen experiment

- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base timeframe 1h; higher timeframes 4h and 1d.
- 9,940 closed 1h candles per asset; chronological split: 6,958 development / 1,491 validation / 1,491 final holdout.
- Baseline: final MTF confluence veto required.
- Candidate: final MTF veto disabled only.
- MTF quality rewards unchanged: Momentum +25, Recovery +20.
- Recovery setup and scoring unchanged.
- RSI final eligibility unchanged at 48–68.
- 70 USDT per symbol/variant; Shield enabled; compounding disabled.
- Fees 0.10% each side; slippage 2 bps each side.
- Spread scenarios: 5, 10, 20, 30 bps.
- No production strategy/risk-limit/allowlist changes; production MTF veto default remains `True`.

## Aggregate final-holdout results

All PnL values are USDT aggregated across the eight independent 70-USDT symbol series. Drawdown is the maximum symbol-series drawdown reported by the runner.

| Spread | Variant | Net PnL | Aggregate return | Max symbol drawdown | Closed trades | Profitable symbols |
|---:|---|---:|---:|---:|---:|---:|
| 5 bps | Baseline | +0.0388282493 | +0.0069336159% | 0.0440022105% | 20 | 4/8 |
| 5 bps | Candidate | -0.1363746949 | -0.0243526241% | 0.1351771051% | 79 | 2/8 |
| 10 bps | Baseline | +0.0318804019 | +0.0056929289% | 0.0450789716% | 20 | 4/8 |
| 10 bps | Candidate | -0.1772096614 | -0.0316445824% | 0.1381633072% | 76 | 2/8 |

The candidate generated more trades, but the extra entries destroyed net performance and materially worsened drawdown. It did not double profits; it lost money at both primary spreads.

## Six mandatory acceptance criteria

| # | Criterion | 5 bps | 10 bps | Decision |
|---:|---|---|---|---|
| 1 | Candidate aggregate holdout net PnL positive | FAIL (-0.1363746949) | FAIL (-0.1772096614) | **FAIL** |
| 2 | Candidate net PnL beats baseline | FAIL | FAIL | **FAIL** |
| 3 | Candidate max drawdown no greater than baseline | FAIL (0.135177% vs 0.044002%) | FAIL (0.138163% vs 0.045079%) | **FAIL** |
| 4 | At least 30 closed trades | PASS (79) | PASS (76) | PASS |
| 5 | At least 5/8 profitable symbol series | FAIL (2/8) | FAIL (2/8) | **FAIL** |
| 6 | Zero entries for both variants at 20 and 30 bps | PASS | PASS | PASS |

**Final score: 2/6 passed; 4/6 failed.** Since every criterion was mandatory, this is an unconditional rejection at both 5 and 10 bps.

## Negative controls

At 20 bps and 30 bps, both baseline and candidate recorded exactly zero entry orders for each of the eight symbols: 16 symbol/spread checks per variant, all zero. The negative controls passed.

## Final disposition

1. Reject the hypothesis of disabling the final MTF confluence veto.
2. Retain the MTF final veto as a mandatory protection gate. The candidate's increased trade count is not evidence of alpha.
3. Keep the production default `require_mtf_confluence=True`; do not expose the candidate setting to production configuration.
4. Freeze this research branch as rejected evidence. No further tuning or holdout reruns for this hypothesis.
5. Keep PR #25 Draft, open, and unmerged. Do not merge into `main`.
6. Keep all execution Paper-only with the 70-USDT reference. No live orders and no real-capital test are authorized.

## Reproducibility limitations

- Spread scenarios are fixed assumptions, not archived historical order-book spreads.
- Higher-timeframe alignment uses only fully closed candles at or before each 1h close.
- Fills, fees, and slippage are simulated.
- The backtester evaluates exits on candle-close returns and does not reconstruct intrabar order paths.
- This report records the one final sealed holdout evaluation; do not use it for parameter tuning.
