# Mumin AI Trader — Prospective Holdout and Walk-Forward Protocol

**Status:** PRE-REGISTRATION DRAFT / HOLDOUT NOT YET SEALED  
**Frozen strategy:** `PaperMicroAlphaSwarm`  
**Strategy source blob:** `5a1fec59b4524e672fb4cb2929ccd29e61f7a18b`  
**Paper runner source blob:** `64d7c6c031af19ef54978dafe3a6ae318f99fda4`  
**Public data adapter blob:** `095e0ae7ae80631c62da9432d33dde654f7f638b`  
**Current data archive:** `b32277cb9e5abd18ffcb43eee7c3c8577d4052079ff0f2442520ed25cb055ec8`

## 1. Why there is no valid final Holdout yet

The first archived dataset is observation-only. It has 2,462 public snapshots, but 98 of 369 cycles are incomplete, 35,386 estimated one-minute slots are missing, 12 duplicate candle snapshots exist, and the largest observed gap is about 2 hours 43 minutes. Only 48 rows contain all 21 hourly macro closes and 24 rows have causal hourly timestamps. The archive correctly reports `replay_ready=false` and `holdout_eligible=false`.

Older Smart Sniper research branches and previously inspected Holdout experiments are not eligible as a final untouched confirmation set. Reusing them after multiple ablations would contaminate the confirmation process.

## 2. Strategy and data freeze

The strategy under evaluation is the active public Paper path, not the separate inactive `StrategyEngine` score-75 class. Freeze these exact code hashes before collecting confirmation data:

- Micro-Alpha strategy: `5a1fec59b4524e672fb4cb2929ccd29e61f7a18b`
- Paper runner: `64d7c6c031af19ef54978dafe3a6ae318f99fda4`
- Public market-data adapter: `095e0ae7ae80631c62da9432d33dde654f7f638b`

The universe is BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, AVAXUSDT and LINKUSDT.

Required observation fields include one-minute OHLCV and close time, bid/ask, order-book depth, the 21 closed hourly macro closes and their exact close timestamps, source receive time, strategy decisions, fills, fees, simulated slippage, and explicit exit trigger. Synthetic-source rows must be excluded.

Do not change strategy thresholds, universe, costs, position sizing, or risk limits during the confirmation window. Any strategy change invalidates the freeze and requires a new confirmation window.

## 3. Development and walk-forward

1. Use only a development partition to debug the replay engine and diagnose data quality.
2. Use chronological walk-forward folds for model evaluation. Each fold receives at least 30 preceding observations as warm-up with entries disabled; evaluation begins strictly after warm-up.
3. Do not shuffle timestamps. No future candle, macro close, order-book snapshot or fill may enter a prior decision.
4. Use executable-side prices (ask for buys, bid for sells), both-side fees, slippage, spread and actual recorded exit triggers.
5. Report aggregate and per-symbol net PnL, expectancy, profit factor, drawdown, trade count, exposure, time in market, and segment stability.
6. Walk-forward folds are not the final Holdout and may be used for research decisions only before the confirmation freeze.

## 4. Prospective confirmation Holdout

A new forward period must start only after the always-on collector has passed its uptime and data-quality acceptance checks and the exact strategy/code hashes have been frozen. The confirmation data must be access-controlled so developers cannot inspect outcomes during tuning. The current public append-only branch is provenance storage, not access control; it does not by itself constitute a sealed Holdout.

Minimum collection window: 30 calendar days **and** at least 100 closed round trips, whichever takes longer. If the strategy produces fewer than 100 trades, continue collecting; do not relax entry filters merely to reach the count.

Before opening the confirmation results, freeze the exact dataset SHA-256, date range, symbols, cost model, segment boundaries and acceptance metrics in a signed manifest. The final evaluator should be a deterministic one-shot job that records the manifest hash and publishes the report without allowing parameter changes.

## 5. Confirmation acceptance criteria

All conditions are mandatory:

1. Positive aggregate net PnL after fees, spread and slippage.
2. Net profit factor at least 1.20.
3. Lower bound of a 95% day-block-bootstrap confidence interval for net expectancy above zero.
4. Maximum portfolio drawdown no greater than 5%; daily loss halt remains at 2%.
5. At least 100 fully closed round trips, with no open position counted as closed.
6. Positive net result in at least three of four predeclared chronological segments.
7. No single trade contributes 25% or more of total net profit; no single symbol contributes more than 50%.
8. Per-symbol results, trade counts and concentration are reported; symbols with inadequate sample size remain unapproved rather than being assumed profitable.
9. All accounting reconciles exactly, source coverage and timestamps pass, and no synthetic data or look-ahead is present.
10. The independent security, operational recovery and emergency-stop gates also pass.

A failed criterion is NO-GO. No parameter tuning is allowed after the confirmation dataset is opened. Any change requires a new prospective confirmation period.

## 6. Current decision

- Current strategy profitability: **NOT ESTABLISHED**.
- Current observation archive: **NOT REPLAY READY**.
- Current Holdout: **NOT SEALED / NOT AVAILABLE**.
- Testnet order execution: **NO-GO**.
- Mainnet execution: **NO-GO**.

The next prerequisite is reliable continuous public-data collection with complete timestamped snapshots. No real order is authorized by this protocol.
