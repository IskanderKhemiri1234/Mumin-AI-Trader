# The Clean Profit Core — Research Charter

**Status:** DESIGN / DATA-GATED — no strategy implementation or Holdout execution is authorized by this charter alone.  
**Branch:** `research/clean-profit-core`  
**Immutable starting baseline:** `c3b1eb308375bb7160d17ab96db85c73db10957d` (`main` at branch creation).  
**Production isolation:** `main` is out of scope. No merge, release, deployment, or production configuration change.

## Objective

Research whether a narrowly scoped entry-timing overlay can improve net execution outcomes without changing the existing signal-generation or risk stack. SOLUSDT and XRPUSDT are candidate assets only; they are **not** presumed profitable. Eligibility must be supported by timestamped, reproducible historical evidence and then tested on data not used to select assets or parameters.

## Non-negotiable invariants

- Preserve MTF alignment, Shield gates, RSI, ATR, signal generation, position sizing, exits, fees, slippage, and all risk controls byte-for-byte / behavior-for-behavior unless a separately reviewed change is explicitly approved. The experiment may consume their outputs but may not weaken, bypass, reorder, or recalculate them.
- Preserve the production evaluation floor of 75 where that gate exists. Never lower it to increase trade count.
- Paper-only. No exchange order submission, Mainnet, credentials, deposits, or live execution.
- Initial Paper capital and maximum allocation remain 70 USDT; no change to the ledger or scheduled Paper worker.
- No writes to `main`; no merge without separate explicit approval.
- No synthetic data may be represented as historical market evidence.

## Research protocol

1. **Baseline audit:** identify the actual strategy implementation, all mandatory MTF/Shield/RSI/ATR gates, the eight-asset universe, fee/slippage assumptions, and the baseline evaluation artifact. Record exact commit and artifact hashes.
2. **Data provenance:** identify the closed Holdout dataset, date range, candle interval, source, completeness checks, and eight exact symbols. Freeze its hash before analysis. If these cannot be established, stop.
3. **Discovery vs confirmation:** prior Holdout may be used only for a clearly labelled discovery/eligibility analysis. No parameter selection may use the sealed confirmation Holdout. Any tuning requires a separate development/walk-forward partition and a newly sealed confirmation partition.
4. **Single intervention:** entry timing only. Freeze the exact timing rule and parameters before confirmation. Do not add new indicators or alter the existing filters.
5. **Reproducibility:** deterministic runs, chronological splits, no look-ahead, no overlapping-label leakage, realistic executable-side fills, fees, spread and slippage included, and per-symbol plus aggregate reports.

## Six mandatory acceptance gates

All six must pass on the sealed confirmation set; otherwise the result is **NO-GO**.

1. Aggregate net PnL is strictly positive after fees, spread and slippage.
2. Aggregate net PnL / risk-adjusted result strictly outperforms the frozen baseline on the same symbols, dates, data and cost model. The comparison metric and minimum margin must be frozen before unsealing.
3. Maximum portfolio drawdown is no greater than the existing 5% Paper drawdown ceiling; do not loosen that ceiling.
4. More than 30 fully closed, non-overlapping trades are observed (at least 31); open positions do not count.
5. Profit factor is strictly greater than 1.0, calculated from net wins and net losses; undefined/infinite PF is not an automatic pass.
6. Stability: positive net result in each predeclared chronological test segment, and no single trade contributes 25% or more of total net profit. Segment boundaries are frozen before confirmation.

## Current blockers / evidence required

The inspected repository baseline currently exposes a liquidity scanner with EMA/RSI candidate logic and a separate Paper engine. The inspected source tree does not establish a complete MTF + Shield + ATR strategy stack, a frozen evaluation-score-75 gate, an eight-symbol research universe, or a historical Holdout/backtest runner. These are **not assumed to exist**.

Before implementing the timing overlay or running the sealed Holdout, supply/locate:
- exact eight symbols and the authoritative strategy path that applies MTF, Shield, RSI and ATR;
- the baseline-75 artifact and its exact evaluation definition;
- immutable historical dataset(s), partition boundaries and hashes;
- frozen baseline results and the predeclared comparison margin;
- evidence that the dataset is genuinely sealed and has not been used for tuning.

Until then, this branch may contain only this charter and isolated research CI. No performance claim, asset-profitability claim, or Holdout pass may be made.
