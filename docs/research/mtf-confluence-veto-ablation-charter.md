# MTF Final Veto Ablation — Research Charter

## Status

Research-only branch: `research/mtf-confluence-veto-ablation`

This branch is isolated from production. It is based on the completed RSI research branch so the frozen 48–68 production Momentum RSI band and the Run 12 decision record remain available as its starting context. The RSI widening hypothesis remains rejected; its Run 12 report and artifact are retained on `research/rsi-entry-band-ablation`.

No production strategy, execution allowlist, account configuration, or `main` branch changes are authorized by this experiment.

## Next bottleneck selected

**Higher-timeframe (MTF) confluence final-entry veto.**

The prior signal-bottleneck audit ranked missing higher-timeframe confirmation as a major rejection source: approximately 39.10% on 1m and 75.58% on 5m in the audited sample. On 1h, the strategy uses fully closed 4h and 1d context. This is a more evidence-supported next candidate than loosening RSI again.

These historical rejection shares are diagnostic counts, not proof that relaxing MTF improves net returns. The experiment must establish that independently.

## Single-variable hypothesis

Compare two variants on identical candles:

- **Baseline:** production MTF final veto enabled.
- **Candidate:** disable only the final hard veto that rejects an otherwise-qualified entry solely because MTF confluence is absent.

The candidate MUST retain:
- MTF quality-score rewards unchanged (Momentum +25; Recovery +20).
- All local trend, momentum, RSI (48–68 final Momentum eligibility), Recovery setup and Recovery scoring rules.
- ATR band 0.08–1.20%, spread ceiling 12 bps, Shield, expected-move/target/cost buffer, minimum net edge and score threshold.
- Position sizing, compounding disabled, 70 USDT starting cash per independent symbol/variant, fees, slippage, stop loss 0.50%, and existing target selection.
- No changes to production defaults: MTF veto remains enabled by default.

The research implementation must expose this as an explicit research-only switch whose default is enabled. Tests must prove that the candidate changes only the final MTF veto, not MTF score rewards or any other gate. Recovery rules must be tested separately.

## Frozen data protocol

- Base timeframe: 1h.
- Higher-timeframe context: fully closed 4h and 1d candles, aligned only when their close timestamp is not later than the base candle.
- Universe: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- 9,940 closed 1h candles per symbol.
- Chronological split: 6,958 development (70%), 1,491 validation (15%), 1,491 sealed final holdout (15%).
- Development and validation may be used to validate implementation and pre-register the candidate, not to repeatedly tune it. The final holdout is opened once after all quality gates pass.
- Primary spread scenarios: 5 and 10 bps. Negative controls: 20 and 30 bps.
- Fees: 0.10% each side; slippage: 2 bps each side; half-spread applied at entry and exit.
- Each symbol/variant is a separate 70 USDT Paper simulation. No live orders, exchange credentials, or live execution calls.

## Six pre-registered acceptance criteria

Evaluate at both 5 bps and 10 bps. The candidate passes only if all six criteria pass at both spreads:

1. Positive aggregate net holdout return after fees, slippage and spread.
2. Net return strictly greater than baseline.
3. Maximum symbol-series drawdown no greater than baseline.
4. At least 30 closed trades.
5. At least 5 of 8 symbol series profitable.
6. Zero entries for both variants at 20 bps and 30 bps.

A missing metric, insufficient sample, or failed criterion is a rejection, not an invitation to retune on the sealed holdout.

## CI and execution order

1. Compile all source and scripts.
2. Run the complete pytest suite.
3. Run Ruff with E, F, I, and UP checks on every changed Python file.
4. Run focused tests proving default MTF veto is unchanged and the research switch bypasses only that final veto.
5. Only after all quality gates pass, execute the frozen paired Paper holdout and upload the full JSON ledger and report.

## Safety invariants

- Paper simulation only; never submit live orders.
- Initial cash remains 70 USDT per independent series.
- Stop loss remains 0.50%; target-selection logic is unchanged.
- No execution allowlist expansion or production threshold changes.
- PR #25 remains Draft, open, and unmerged; `main` is not modified.
- A passing research holdout is evidence for review only, not authorization to promote a candidate.
