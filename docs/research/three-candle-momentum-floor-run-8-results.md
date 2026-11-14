# Three-Candle Momentum Floor Ablation — Run 8 Results

## Decision

**REJECT the candidate for research acceptance. Retain the production momentum floor at `momentum_3_bps > 5`.**

The candidate improved aggregate Paper PnL slightly at both primary spreads and did not increase measured maximum symbol drawdown, but it failed two frozen acceptance criteria at both spreads: at least 30 closed trades and at least 5 of 8 profitable symbol series. No threshold changes or follow-up tuning are authorized by this result.

## Reproducibility

- Workflow run: [#43573047577](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43573047577)
- Branch: `research/three-candle-momentum-floor`
- Commit tested: `c9505a05255b751cac05aabd284f7f24493d7e6a`
- Artifact: [three-candle-momentum-floor-oos-8.zip](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43573047577/artifacts/14104444962)
- Artifact ID: `14104444962`
- Artifact ZIP SHA-256: `e57527ed97d6f0034638e111992043616f67157e933542583af7be69f59f651b`
- JSON SHA-256: `8394eed5bdf3a3e6a9943eac4cdaf231dcb0f5b9781a9ccf4a69952d6897d5d2`
- CI: compile passed; `65 passed`; Ruff passed.

## Frozen method

- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base interval: 1h; higher-timeframe confirmation: fully closed 4h and 1d candles.
- 9,940 closed 1h candles per symbol.
- Chronological split: 6,958 development, 1,491 validation, 1,491 untouched final holdout.
- Holdout: 2026-09-13 04:59:59.999 UTC through 2026-11-14 06:59:59.999 UTC.
- Paired comparison: baseline `momentum_3_bps > 5`; candidate `momentum_3_bps > 0`.
- The +10 quality score bonus remains strictly gated by `momentum_3_bps > 5`; RECOVERY logic and every other entry gate remain unchanged.
- Independent Paper account: 70 USDT per symbol and variant; compounding off; fees 0.10% each side; slippage 2 bps each side; Shield on; SL 0.50%; strategy-selected TP 1.25%-1.50%; ATR 0.08%-1.20%.
- Spreads: 5, 10, 20 and 30 bps.

## Aggregate holdout results

| Spread | Variant | Net PnL (USDT) | Aggregate return | Closed trades | Wins / losses | Profitable symbols | Max symbol DD |
|---:|---|---:|---:|---:|---:|---:|---:|
| 5 bps | Baseline | +0.038828 | +0.006934% | 20 | 6 / 14 | 4 / 8 | 0.044002% |
| 5 bps | Candidate | +0.042743 | +0.007633% | 21 | 6 / 15 | 4 / 8 | 0.044002% |
| 10 bps | Baseline | +0.031880 | +0.005693% | 20 | 6 / 14 | 4 / 8 | 0.045079% |
| 10 bps | Candidate | +0.035283 | +0.006301% | 21 | 6 / 15 | 4 / 8 | 0.045079% |

Candidate PnL improvement was only +0.003915 USDT at 5 bps and +0.003403 USDT at 10 bps across the eight independent accounts. The candidate added one closed trade overall at each primary spread; total wins stayed at six and losses increased by one.

## Six frozen acceptance criteria

| Criterion | 5 bps | 10 bps |
|---|---|---|
| Candidate aggregate holdout net return positive | PASS | PASS |
| Candidate beats baseline net return | PASS | PASS |
| Candidate max drawdown no higher than baseline | PASS | PASS |
| At least 30 closed trades | FAIL — 21 | FAIL — 21 |
| At least 5 of 8 profitable symbol series | FAIL — 4 / 8 | FAIL — 4 / 8 |
| 20/30 bps negative controls produce zero entries in both variants | PASS | PASS |

**Overall: FAIL at both 5 and 10 bps. Candidate rejected under the pre-registered rule.**

## Incremental signal analysis

At both 5 and 10 bps, only two incremental candidate BUY assessments appeared across the eight symbols: one BNBUSDT signal at 2.4869 bps three-candle momentum and one SOLUSDT signal at 4.4976 bps. Both passed the Shield. The BNBUSDT additional trade worsened that symbol's PnL; the SOLUSDT additional trade improved that symbol's PnL. No other symbol produced an incremental BUY. This explains the small aggregate improvement without a broader improvement in profitable symbol coverage.

At 20 and 30 bps, both variants produced zero Paper entry orders for all eight symbols.

## Governance

- Candidate rejected; keep `momentum_3_bps > 5` as the production rule.
- No production strategy, risk limits, execution allowlist, or `main` changes.
- Paper-only historical simulation; no live orders or exchange execution.
- PR #25 remains Draft, open, and unmerged.
- Positive historical PnL in this short holdout is not evidence of future profitability.
