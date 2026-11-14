# Expected-Move Edge Buffer Ablation — Run 13 Results

## Decision

**REJECT the 5 bps candidate. Retain the original 10 bps final Expected-Move buffer.**

The candidate's net PnL was slightly higher and maximum symbol-series drawdown was no higher than baseline, but it failed two frozen acceptance criteria at both primary spreads: only 20 closed trades (minimum 30) and only 4/8 profitable symbol series (minimum 5/8). No production promotion is justified.

## Reproducibility

- Branch: `research/expected-move-edge-buffer`
- Evaluated commit: `072f17773b71397d0f51109d5f3db37b6b8992ee`
- [GitHub Actions Run 13](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43600205799): Compile PASS; Pytest PASS (65); Ruff PASS; holdout PASS; artifact upload PASS.
- Artifact ID: `14116141644`; ZIP SHA-256: `a66e3ed5c3ef357f4893d84fdfae80a60f348f88860efd0b033a5f1cc02811ed`
- JSON SHA-256: `a80489a7c746aef0b7b75dadbef3f0e8e2746a70fd07fcbd3769f3c9e8dd37e3`
- Generated: 2026-11-14T13:32:56.980312+00:00

## Single-variable definition

- Baseline: `expected_move_bps >= selected_target_bps + estimated_cost_bps + 10`
- Candidate: `expected_move_bps >= selected_target_bps + estimated_cost_bps + 5`
- Only the final eligibility buffer changed.
- The `momentum_3_bps > 5` condition and its +10 quality bonus remained unchanged. The 10 bps net-edge threshold for score bonuses and 3R target selection remained unchanged. RSI, MTF, ATR, spread, Shield, sizing, costs, stop/target mechanics, and all other gates remained unchanged.

## Data and Paper protocol

- Eight symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- 9,940 closed 1h candles per symbol; fully closed 4h/1d confirmations only.
- Chronological split: 6,958 development (70%), 1,491 validation (15%), 1,491 untouched holdout (15%).
- UTC boundaries (same across symbols):
  - Development: 2025-09-26T09:59:59.999+00:00 to 2026-07-13T06:59:59.999+00:00
  - Validation: 2026-07-13T07:59:59.999+00:00 to 2026-09-13T09:59:59.999+00:00
  - Holdout: 2026-09-13T10:59:59.999+00:00 to 2026-11-14T12:59:59.999+00:00
- Independent Paper accounts: 70 USDT per symbol and variant; compounding off; Shield on; fees 0.10% each side; slippage 2 bps each side; SL 0.50%; strategy-selected target unchanged; ATR 0.08%-1.20%.
- No live orders, private exchange keys, or real execution.

## Aggregate final-holdout results

| Spread | Baseline PnL | Candidate PnL | Candidate delta | Candidate trades | Profitable symbols |
|---:|---:|---:|---:|---:|---:|
| 5 bps | +0.03882825 USDT | +0.04390528 USDT | +0.00507703 USDT | 20 | 4/8 |
| 10 bps | +0.03188040 USDT | +0.03317627 USDT | +0.00129587 USDT | 20 | 4/8 |

- At 5 bps, candidate maximum symbol-series drawdown: 0.04400221%, equal to baseline.
- At 10 bps, candidate maximum symbol-series drawdown: 0.04507897%, equal to baseline.
- At both spreads, both variants closed 20 trades: 6 wins and 14 losses.
- Candidate PnL was positive and beat baseline, but the absolute improvements were small and the trade-count and profitable-symbol requirements both failed.

## Six frozen acceptance criteria

| Criterion | 5 bps | 10 bps |
|---|---|---|
| Candidate net PnL positive | PASS | PASS |
| Candidate beats baseline | PASS | PASS |
| Maximum drawdown no higher than baseline | PASS | PASS |
| At least 30 closed trades | FAIL (20) | FAIL (20) |
| At least 5/8 profitable symbol series | FAIL (4/8) | FAIL (4/8) |
| 20 and 30 bps negative controls have zero entries | PASS | PASS |
| **Overall decision** | **REJECT** | **REJECT** |

At 20 bps and 30 bps, both variants generated zero Paper entry orders for all eight symbols (32 symbol/variant/spread negative-control observations).

## Candidate-only signals and overlaps

- 5 bps: four candidate-only BUY assessments (three SOLUSDT, one LINKUSDT); all four passed Shield.
- 10 bps: four candidate-only BUY assessments (BNBUSDT, SOLUSDT, XRPUSDT, LINKUSDT); Shield blocked three and passed one.
- These did not increase the aggregate closed-trade count beyond 20.
- The 5 bps candidate-only assessments had 168 bps baseline vs 163 bps candidate final required move. At 10 bps, the respective required moves were 173 vs 168 bps.
- Overlapping Expected-Move/MTF/ATR/score failure counts are included in the JSON artifact. They are overlap counts, not causal attribution.
- Limitation: the incremental-signal ledger records each candidate-only assessment and Shield result, but does not persist an exact per-signal mapping to a simulator fill. Aggregate Paper entry orders and closed trades are reported separately; both variants recorded 20 entry orders and 20 closed trades at each primary spread.

## Per-symbol net PnL (USDT)

| Symbol | 5 bps baseline | 5 bps candidate | 10 bps baseline | 10 bps candidate |
|---|---:|---:|---:|---:|
| BTCUSDT | 0.00000000 | 0.00000000 | 0.00000000 | 0.00000000 |
| ETHUSDT | 0.00685150 | 0.00685150 | 0.00531819 | 0.00531819 |
| BNBUSDT | -0.00660278 | -0.00660278 | -0.00710935 | -0.00710935 |
| SOLUSDT | 0.00575466 | 0.01282067 | 0.00545233 | 0.00545233 |
| XRPUSDT | 0.03437188 | 0.03437188 | 0.03333495 | 0.03333495 |
| ADAUSDT | 0.00251960 | 0.00251960 | 0.00098872 | 0.00098872 |
| DOGEUSDT | -0.00059504 | -0.00059504 | -0.00212436 | -0.00212436 |
| LINKUSDT | -0.00347158 | -0.00546057 | -0.00398009 | -0.00268422 |

## Governance decision

- Keep the production final Expected-Move buffer at **10 bps**.
- Reject the 5 bps candidate; do not tune or rerun the same holdout.
- Keep this branch research-only; no merge and no changes to `main`.
- PR #25 remains Draft, open, and unmerged.
- A positive historical PnL delta is not a profitability guarantee and does not override failed acceptance criteria.
