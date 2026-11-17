# Mumin Public Paper Engine — Frozen Baseline Audit

**Status:** FROZEN OBSERVATIONAL BASELINE / NOT A PROFITABILITY CLAIM  
**Baseline commit:** `7a18432606b7ce22c48e6c520884a0da7d2eb853`  
**Audit date:** 2026-11-17 UTC  
**Scope:** the active public-market-data Paper execution path on `main`  
**Execution:** Paper only; no exchange order submission

## 1. Authoritative runtime path

The scheduled public-data runner is `scripts/run_continuous_paper.py` (`16a743eafce302a2ada6a6a64c0f6330af433aaf`). For `BINANCE_PUBLIC_MARKET_DATA`, it calls `PaperMicroAlphaSwarm` from `src/mumin_trader/strategy_micro_alpha.py` (`5a1fec59b4524e672fb4cb2929ccd29e61f7a18b`). Public snapshots are fetched by `src/mumin_trader/exchanges/binance/public_market_data.py` (`57376e5b0f5d77d230782f0f896a00ed5d7290d5`).

The separate `StrategyEngine` in `src/mumin_trader/ai_engine/strategy_engine.py` (`b6104059290528832332cdc296cc5b01e1631bf1`) defines an evaluation score floor (default 75), RSI, momentum and MTF gates, but the continuous public Paper runner does **not** import or call that class. Therefore, the score-75 rule is not part of the active public Paper decision path at this baseline. Do not claim that the current Paper engine enforces a 75-point score gate.

The older `PaperSwarm` path is used for synthetic Paper fixtures; it is not the live public-data Paper strategy. Synthetic fixtures must remain excluded from market-evidence datasets.

## 2. Frozen public Paper universe

Exactly eight symbols are configured in the runner:

- BTCUSDT
- ETHUSDT
- BNBUSDT
- SOLUSDT
- XRPUSDT
- ADAUSDT
- AVAXUSDT
- LINKUSDT

This differs from some older Smart Sniper research branches that used DOGEUSDT instead of AVAXUSDT. Results from those branches are not interchangeable with this active Paper universe.

## 3. Active signal rules

The public-data path uses three deterministic agents:

1. **Liquidity sweep proxy:** requires at least nine observations; the latest volume must be at least 1.20× the mean of the prior eight; a BUY is a low sweep/reclaim with close location at least 0.65; a SELL is a high sweep/rejection with close location at most 0.35. The source explicitly describes this as a price-action proxy, not proof of hidden stops or executed flow.
2. **Order-book depth imbalance:** BUY at bid-depth imbalance >= +12%; SELL at <= -12%; otherwise HOLD.
3. **Micro-alpha filter:** requires valid bid/ask, spread strictly below 8 bps, ATR in 0.05%–0.80%, and agreement between the sweep and depth votes. The final signal is strict three-agent consensus. For BUY, at least 21 fully closed hourly closes are required; the current price is blocked only when it is below a falling SMA20. SELL does not require the hourly BUY macro check.

The runner evaluates closed one-minute candle history and current public order-book depth. The historical wick/depth features are not reconstructible from ordinary OHLCV alone.

## 4. Data acquisition and execution model

The public reader calls Binance public REST endpoints for:

- best bid/ask;
- 1-minute klines (up to 30 rows, selecting a fully closed candle);
- 1-hour klines (up to 22 rows, selecting 21 fully closed closes);
- order-book depth (top 10 levels on each side are summed);
- exchange server time.

It rejects stale 1-minute candles older than 180 seconds, stale hourly data older than 7,200 seconds, invalid quotes/candles, insufficient hourly warm-up, and missing depth. The scheduled GitHub workflow runs three 60-second Paper cycles per invocation and is scheduled every five minutes. GitHub schedule is best-effort and is **not** continuous 24/7 service.

Paper fills are simulated at ask plus 2 bps slippage for BUY and bid minus 2 bps for SELL. Fee rate is 0.10% per side. These are model assumptions, not actual exchange fills or maker/taker commission records.

## 5. Position exits and risk limits

ATR-derived simulated exit thresholds:

- stop: `min(0.50%, max(0.20%, ATR% × 0.80))`;
- target: `min(1.25%, max(0.40%, ATR% × 1.80))`.

A position closes when its projected net return after both-side fees and simulated slippage reaches the stored stop or target threshold, or when the signal is SELL. Legacy fills store the CLOSE action but do not store which of these three conditions triggered it; retrospective attribution must remain unknown.

Fixed Paper account constraints:

| Control | Frozen value |
|---|---:|
| Initial and maximum capital | 70.00 USDT |
| Maximum trade allocation | 5.00 USDT |
| Cash reserve | 20% |
| Maximum open positions | 2 |
| Maximum total exposure | 56.00 USDT |
| Daily loss halt | 2.00% |
| Maximum drawdown halt | 5.00% |
| Spread gate | strictly below 8 bps |
| Fee assumption | 0.10% per side |
| Slippage assumption | 2 bps per side |

No parameter or risk-limit change is authorized by this audit.

## 6. Observed Paper ledger snapshot (not a backtest)

Source: `mumin-paper-ledger/latest-state.json` and `latest-report.md`, generated at 2026-11-17T21:42:09Z UTC, tick 351. The snapshot reports:

| Metric | Recorded value |
|---|---:|
| Initial Paper capital | 70.0000 USDT |
| Equity / cash | 68.7324786871 USDT |
| Realized net PnL | -1.2675213129 USDT |
| Total simulated fees | 0.44737914 USDT |
| Closed trades | 45 |
| Wins / losses | 5 / 40 |
| Win rate | 11.11% |
| Return | -1.8107% |
| Drawdown from high watermark | 1.8655% |
| Open positions | 0 |

Derived from the recorded totals, mean net PnL per closed trade is approximately -0.02817 USDT. This is a small, finite observational Paper sample, not a statistical estimate of future expectancy. It is not evidence of a profitable edge.

## 7. Historical research compatibility warning

Previous Smart Sniper 2.0 walk-forward and ablation reports tested a different rule-based strategy implementation, different universe details in some branches, and candle-based execution approximations. Those reports are not a valid backtest of the active `PaperMicroAlphaSwarm` runner.

One earlier six-fold walk-forward report found negative mean series returns (for example -0.0032% with Shield + compounding) and explicitly concluded that positive expectancy had not been demonstrated. Several later candidate ablations were rejected for failing frozen trade-count and profitable-symbol criteria. Those results are useful warnings against threshold tuning, but they cannot validate or invalidate the distinct current Micro-Alpha engine.

Historical OHLCV does not contain historical order-book depth. Consequently, a faithful historical reconstruction of the current three-agent strategy cannot be produced from candles alone. No synthetic depth may be substituted and labelled historical.

## 8. Data and evaluation gates before strategy conclusions

1. Export and hash the persisted public Paper snapshots, decisions and fills; separate any synthetic-source rows.
2. Quantify missing intervals, duplicates, staleness, timestamp alignment and per-symbol coverage before calling the archive usable.
3. Preserve all source records and a SHA-256 manifest; never rewrite an already sealed dataset.
4. Use chronological development and validation partitions for any research design.
5. A confirmation Holdout must begin after the freeze timestamp, be access-controlled, and remain unopened during development or parameter selection. Previously inspected holdouts are not considered untouched.
6. Any backtest must replay the exact active Micro-Alpha rules, with executable-side prices, fees and slippage; where depth is absent, exclude the observation rather than inventing it.
7. Report per-symbol and aggregate net PnL, profit factor, drawdown, trade count, segment stability, and confidence/uncertainty. Do not infer profitability from win rate alone.

**Decision at this baseline:** strategy profitability is **NOT ESTABLISHED**. Mainnet and automated real-money execution remain **NO-GO**.
