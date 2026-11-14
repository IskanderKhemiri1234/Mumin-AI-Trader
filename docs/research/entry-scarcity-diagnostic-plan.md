# Smart Sniper entry-scarcity diagnostic plan

## Research boundary

- Branch: `research/entry-scarcity-mtf-momentum`
- Research only; no production strategy, Paper execution, credentials, allowlist, or risk-limit changes.
- Paper-only assumptions: 70 USDT reference capital, 0.50% fixed stop, unchanged strategy-selected 1.25%-1.50% target.
- Production ATR band remains 0.08%-1.20%.
- Shield remains enabled for attribution; compounding is not part of this diagnostic.
- PR #25 remains Draft, open, and unmerged.

## Question

Determine whether low entry frequency is primarily associated with:
1. Missing conjunction of bullish 4h and 1d confirmations (MTF gate).
2. Local setup/momentum requirements (3-candle momentum, RSI, local trend, or recovery setup).
3. Expected-move shortfall versus selected target, estimated execution costs, and the 10 bps edge buffer.
4. Other independent gates: ATR band, hard spread ceiling, quality score, and Shield blocks after a BUY candidate exists.

## Dataset and execution

- Public Binance closed historical candles only.
- Eight research symbols: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT.
- Base interval: 1h; higher-timeframe confirmation inputs: 4h and 1d.
- Up to 10,000 closed candles per interval.
- Fixed spread scenarios: 5, 10, 20, and 30 bps.
- Every candle is timestamp-aligned; higher-timeframe inputs include only bars closed by the base-candle close timestamp.

## Measurement

For every evaluated candle and spread scenario, save:
- Signal and selected strategy branch (MOMENTUM or RECOVERY).
- Momentum 3/12, RSI, local regime, pullback/rebound measures, ATR, score, MTF boolean.
- Expected move and required move after target, estimated costs, and edge buffer.
- Every failed gate (overlapping attribution) and one exclusive primary rejection reason using a fixed priority.
- Shield state/reasons and whether a BUY candidate was blocked by Shield.

The summary reports overlapping gate counts separately from exclusive primary-rejection counts. Shield market-condition counts are not conflated with Shield rejections of BUY candidates.

## Interpretation guardrails

This is observational gate attribution, not a counterfactual proof that removing a gate would improve performance. MTF contributes to the score and may affect strategy-branch selection; therefore, disabling it in a follow-up experiment would require a separately specified, paired, pre-registered research design. No gate is bypassed, no threshold is tuned, and no strategy change is authorized by this report.

A subsequent controlled ablation, if justified by the ledger, must compare identical candles and costs, preserve the 70 USDT / 0.50% stop / unchanged target assumptions, retain Paper-only execution, and evaluate net results on a separate untouched holdout before any production discussion.
