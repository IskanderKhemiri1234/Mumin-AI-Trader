# Relative-Volume Liquidity Confirmation — Final Holdout Decision

**Decision: REJECTED / FROZEN. Do not promote this candidate.**

## Execution provenance

- GitHub Actions run: [#43637375279](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43637375279)
- Evaluated source commit: `53c124aa3a0018066a28798e4b27fe512091458b`
- Branch: `research/liquidity-confirmation-ablation`
- Quality gates: Compile passed; complete Pytest passed; Ruff passed.
- Holdout runner completed and uploaded its ledger. The workflow's final non-zero status is the deliberate rejection signal because the frozen acceptance criteria were not all met; it is not a compile/test/lint failure.
- Artifact ID: `14131285383`
- Artifact ZIP SHA-256: `4092314e3c3bfd8431629cb2f2a993fc97a3a091b3c0a75eb1bae701b08b5bb4`
- JSON report SHA-256: `cfda80b5c9877e0fc630b6a0bd3e7e99a764f33e731211f58bcc0108d89a2a67`
- Input 1h candle data SHA-256: `c3113d4cda20cdd9afec23239d9e34ca407f4000aea23ef0ff00004ff886b9ee`
- [Download the GitHub Actions artifact](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43637375279/artifacts/14131285383)

## Frozen experiment

- Eight symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Exactly 9,940 fully closed 1h candles per symbol.
- Chronological split: 6,958 development bars (70%), 1,491 validation bars (15%), 1,491 sealed holdout bars (15%).
- 240 prior bars supplied as indicator warm-up at each segment boundary; the warm-up is not scored as segment performance.
- Higher timeframes: 4h and 1d, timestamp aligned to closed bars.
- Independent 70 USDT starting capital for each symbol/variant; compounding disabled.
- Spread assumptions: 5, 10, 20, and 30 bps; fees 0.10% per side; slippage 2 bps per side.
- Shield remained enabled and unchanged. MTF remained mandatory. RSI, ATR, momentum, target, stop, score, sizing, and execution logic remained unchanged.
- Candidate required both quote-volume ratio and trade-count ratio, each measured against the median of the prior 20 closed bars, to be within inclusive 0.80–2.50.

## Sealed holdout aggregate results

| Spread | Variant | Net PnL | Closed trades | Profitable symbols | Max symbol drawdown | Fees |
|---|---|---:|---:|---:|---:|---:|
| 5 bps | Baseline | +0.038828 USDT | 20 | 4/8 | 0.04400% | 0.040839 USDT |
| 5 bps | Candidate | +0.057695 USDT | 14 | 4/8 | 0.02768% | 0.028618 USDT |
| 10 bps | Baseline | +0.031880 USDT | 20 | 4/8 | 0.04508% | 0.040832 USDT |
| 10 bps | Candidate | +0.052057 USDT | 14 | 4/8 | 0.02913% | 0.028612 USDT |

The candidate blocked 7 otherwise eligible BUY candidates at the additional liquidity gate in each primary spread scenario (14 combined across the 5 and 10 bps scenarios). The candidate reduced fees and drawdown and recorded higher aggregate holdout net PnL, but this does not override the mandatory breadth and sample-size requirements.

## Six mandatory acceptance criteria

| Criterion | 5 bps | 10 bps | Decision |
|---|---|---|---|
| 1. Positive candidate net PnL | PASS | PASS | PASS |
| 2. Candidate beats paired baseline | PASS | PASS | PASS |
| 3. Drawdown no worse than baseline | PASS | PASS | PASS |
| 4. At least 30 closed trades | FAIL — 14 | FAIL — 14 | FAIL |
| 5. At least 5 of 8 profitable symbols | FAIL — 4/8 | FAIL — 4/8 | FAIL |
| 6. Zero entries at 20 and 30 bps | PASS — zero | PASS — zero | PASS |

There are 7 passing and 4 failing individual checks across the 11 recorded comparisons. At the six-criterion policy level, 4 criteria pass and 2 fail. **Both failed criteria are mandatory; therefore the candidate is REJECTED.**

## Development and validation context

| Segment | Spread | Baseline net PnL | Candidate net PnL | Baseline trades | Candidate trades |
|---|---:|---:|---:|---:|---:|
| Development | 5 bps | -0.150840 | -0.045786 | 94 | 72 |
| Development | 10 bps | -0.230330 | -0.119463 | 92 | 70 |
| Validation | 5 bps | -0.052433 | -0.062674 | 36 | 19 |
| Validation | 10 bps | -0.078932 | -0.058666 | 37 | 17 |

Development and validation are descriptive and were not used to tune the pre-registered thresholds. Validation did not show a consistent PnL improvement at both primary spread settings. This reinforces the decision not to relax or adjust the candidate after seeing the sealed holdout.

## Negative controls

At both 20 bps and 30 bps, the baseline and candidate produced exactly zero entry orders across all eight symbols (16 series per spread). Negative controls passed.

## Final disposition and safety

- Freeze this candidate as rejected; do not alter the 0.80–2.50 thresholds and rerun against the same sealed holdout.
- Keep the baseline unchanged. The new research gate remains opt-in and disabled by default.
- Do not modify production strategy or risk limits, MTF, Shield, RSI, ATR, momentum, or execution allowlists.
- No live orders, no testnet orders, no real capital, and no production promotion.
- Keep PR #25 Draft, open, and unmerged. Do not merge to `main`.
- Preserve this branch, source, ledger, and report as the historical audit trail.

## Interpretation limits

Candle quote volume and trade count are activity proxies, not order-book depth or executable liquidity. Spread values are fixed assumptions, not archived historical order-book measurements. Fills and costs are simulated. Stop/target checks use candle closes and cannot reconstruct intrabar price paths. The small number of holdout trades makes the sample-size criterion especially important. Historical Paper simulation does not guarantee future performance.
