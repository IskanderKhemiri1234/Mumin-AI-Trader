> **Validation status:** These earlier findings predate the per-position 2.5R/3R target persistence fix now included in the follow-up validation branch. They are historical baseline results only and must not be treated as validation of the updated simulator.

# Smart Sniper 2.0 — preliminary walk-forward findings

**Research run:** GitHub Actions run [#43524247461](https://github.com/IskanderKhemiri1234/Mumin-AI-Trader/actions/runs/43524247461)  
**Commit evaluated:** `39a9b1930c15f81f8bc2e84b90dcc5caac1aad9c`  
**Artifact:** `smart-sniper-walk-forward-21` (six JSON reports; artifact ID `14083359011`)  
**Data source:** Binance public closed candles  
**Research date:** 2026-11-13 UTC

## Coverage

- Symbols: BTCUSDT, ETHUSDT, BNBUSDT
- Intervals: 1m, 5m, 15m, 1h, 4h, 1d
- Explicit spread assumptions: 5, 10, 20 basis points
- 54 symbol/interval/spread series
- Six rolling out-of-sample folds per series
- 1,296 fold evaluations across four ablation configurations
- Fee and slippage assumptions are included in the simulator
- The research uses a 1.02 USDT seed ticket; incremental sizing is drawn only from realized net profit and remains capped

## Aggregate results

The figures below are equally weighted averages of the 54 series' mean fold returns. Maximum drawdown is the largest observed series-level maximum drawdown. They are not pooled account returns.

| Configuration | Mean series return | Positive series | Maximum observed drawdown | Closed trades | Incremental profit re-used |
|---|---:|---:|---:|---:|---:|
| Shield + compounding | -0.0032% | 6 / 54 | 0.1645% | 593 | 1.2258 USDT |
| Shield only | -0.0031% | 6 / 54 | 0.1626% | 593 | 0 |
| No shield + compounding | -0.0178% | 3 / 54 | 0.5156% | 1,707 | 7.0301 USDT |
| Neither | -0.0175% | 3 / 54 | 0.5155% | 1,707 | 0 |

## Interpretation

1. **The shield shows a risk-control signal in this sample.** The shielded configurations generated fewer entries and materially lower worst observed drawdown than the unshielded configurations. This is consistent with its design as an entry gate and staged de-risking control.
2. **The strategy has not demonstrated positive aggregate expectancy.** The combined configuration's average series return remained slightly negative, and only 6 of 54 series had a positive mean across their six folds. This is not evidence of a profitable trading edge.
3. **Compounding did not improve this sample's aggregate return.** Shield + compounding was marginally below shield-only on mean return and had a slightly higher worst observed drawdown. The amount re-used was small. The matrix currently demonstrates constrained accounting and sizing behavior, not a proven growth advantage.
4. The 1d subset had a positive average in this sample, but this is a small, isolated subset and must not be selected as a strategy without a new untouched validation period.

## Decision

**Do not merge this PR as a proven profit engine on the basis of these results.** The code and tests may establish that the controls execute as specified, but the historical evaluation does not establish profitable expectancy or absolute loss prevention. Further strategy research must use fresh, untouched periods, realistic execution assumptions, benchmark comparisons, and robustness checks before any production promotion.

## Material limitations

- Each symbol/interval used up to 1,000 recent closed candles; this is not a multi-year intraday dataset.
- Historical order-book depth was unavailable. Spread values of 5/10/20 bps are scenarios, not archived observed spreads.
- The simulator evaluates stop and target conditions at candle closes, not reconstructed intrabar paths.
- The strategy is deterministic and rule-based, not a trained machine-learning model.
- These are historical simulation results, not live execution results and not a forecast.
