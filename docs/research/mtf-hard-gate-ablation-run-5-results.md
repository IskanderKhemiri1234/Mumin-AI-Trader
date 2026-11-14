# MTF Hard-Gate Ablation — Run #5 Results

## Decision

**REJECT H1. Keep the production MTF hard gate unchanged.**

The candidate produced more Paper entries, but it failed the return, baseline-superiority, drawdown, and profitable-symbol criteria at both primary spreads. The 20/30 bps negative controls passed.

## Reproducibility

- Workflow run: [#5](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43571974287)
- Branch: `research/mtf-hard-gate-ablation`
- Tested source commit: `d0ae30bd88c94a7087e9e5c17fcf4b0f675c2e26`
- Artifact: `smart-sniper-mtf-hard-gate-ablation-5` (artifact ID `14103799288`)
- Artifact SHA-256: `f4e8f09fa4224617356bc5605fe4b3ce243ed48b28c45f92564a987744240766`
- Report JSON SHA-256: `f2fdb237c421c27640fc4ddbc3891d7aa389f5aab6034d3424ddc5e0d85d03d0`
- Generated: `2026-11-14T07:38:37.376395Z`
- Source: Binance public historical closed candles; no private keys or order endpoints.

## Frozen experiment

- 8 symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- 9,940 closed 1h candles per symbol; 4h and 1d higher-timeframe context.
- Split per symbol: 6,958 development / 1,491 validation / 1,491 final holdout.
- Holdout close-time range: **2026-09-13 04:59:59.999 UTC through 2026-11-14 06:59:59.999 UTC**.
- Baseline and candidate each used an independent 70 USDT Paper account per symbol; compounding off.
- SL 0.50%; strategy-selected TP 1.25–1.50%; fees 0.10% each side; slippage 2 bps each side; Shield on; ATR 0.08–1.20%.
- Candidate changed only the final MTF hard-veto check. MTF indicators and their score bonuses remained enabled. No production strategy or risk setting was changed.

## Aggregate holdout results

| Spread | Variant | Net PnL | Aggregate net return | Closed trades | Wins / losses | Positive symbol series | Max of symbol drawdowns | Paper entry orders |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 5 bps | Baseline | +0.038828 USDT | +0.006934% | 20 | 6 / 14 | 4 / 8 | 0.044002% | 20 |
| 5 bps | Candidate | -0.136375 USDT | -0.024353% | 79 | 19 / 60 | 2 / 8 | 0.135177% | 79 |
| 10 bps | Baseline | +0.031880 USDT | +0.005693% | 20 | 6 / 14 | 4 / 8 | 0.045079% | 20 |
| 10 bps | Candidate | -0.177210 USDT | -0.031645% | 76 | 17 / 59 | 2 / 8 | 0.138163% | 76 |

Candidate max drawdown was about **3.07×** baseline at 5 bps and **3.06×** baseline at 10 bps. More entries did not translate into better net performance.

## Preregistered acceptance criteria

| Criterion | 5 bps | 10 bps |
|---|---:|---:|
| Candidate aggregate net return positive | FAIL | FAIL |
| Candidate beats baseline net return | FAIL | FAIL |
| Candidate max drawdown no greater than baseline | FAIL | FAIL |
| At least 30 closed trades | PASS (79) | PASS (76) |
| At least 5 of 8 symbol series profitable | FAIL (2/8) | FAIL (2/8) |
| 20/30 bps negative controls produce zero entries | PASS | PASS |

**Overall: FAIL.** H1 is rejected for this preregistered test. Do not weaken any other gate or promote this candidate.

## Incremental candidate signals

These are eligible candidate signals absent from baseline; they are not all filled trades because an existing Paper position can prevent a new entry.

- 5 bps: 127 incremental signals — neither HTF bullish 104, only 4h bullish 17, only 1d bullish 6. Shield blocked 39 and passed 88.
- 10 bps: 119 incremental signals — neither HTF bullish 97, only 4h bullish 16, only 1d bullish 6. Shield blocked 36 and passed 83.

## Negative controls

- 20 bps: baseline 0 Paper entries; candidate 0.
- 30 bps: baseline 0 Paper entries; candidate 0.

The hard spread ceiling remained effective.

## Governance

- Production ATR remains 0.08–1.20%; SL 0.50%; TP rule unchanged.
- No live orders, no live trading, no execution allowlist changes, no production strategy promotion.
- PR #25 remains Draft, open, and unmerged.
- Recommendation: retain the current production MTF hard gate. The 83.83% exclusive attribution from Entry Scarcity Run #5 identified a frequent rejection label, but this controlled counterfactual shows that bypassing the hard veto alone creates more entries with materially worse holdout outcomes.
