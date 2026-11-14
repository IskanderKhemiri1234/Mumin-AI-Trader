from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

import mumin_trader.research.walk_forward as walk_forward_engine
from mumin_trader.research.walk_forward import HistoricalCandle, _evaluate_fold
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy
from run_binance_walk_forward import ALLOWED, HIGHER_TIMEFRAMES, fetch_history

ZERO = Decimal(0)
INITIAL_CASH = Decimal(70)
ACTIVE_SPREADS = (Decimal(5), Decimal(10))
STRESS_SPREADS = (Decimal(20), Decimal(30))
ROLLING_LOOKBACK = 240
MIN_ROLLING_HISTORY = 60


@dataclass(frozen=True, slots=True)
class AtrBand:
    name: str
    lower: Decimal | None = None
    upper: Decimal | None = None
    lower_quantile: Decimal | None = None
    upper_quantile: Decimal | None = None

    @property
    def dynamic(self) -> bool:
        return self.lower_quantile is not None

    def bounds(self, prior_atr: list[Decimal]) -> tuple[Decimal, Decimal] | None:
        if not self.dynamic:
            assert self.lower is not None and self.upper is not None
            return self.lower, self.upper
        history = [value for value in prior_atr[-ROLLING_LOOKBACK:] if value > ZERO]
        if len(history) < MIN_ROLLING_HISTORY:
            return None
        assert self.lower_quantile is not None and self.upper_quantile is not None
        ordered = sorted(history)
        return (
            _quantile(ordered, self.lower_quantile),
            _quantile(ordered, self.upper_quantile),
        )

    def accepts(self, current: Decimal, prior_atr: list[Decimal]) -> bool:
        bounds = self.bounds(prior_atr)
        return bounds is not None and bounds[0] <= current <= bounds[1]

    def as_dict(self) -> dict[str, str]:
        if self.dynamic:
            return {
                "name": self.name,
                "mode": "causal_rolling_quantile",
                "lookback_bars": str(ROLLING_LOOKBACK),
                "minimum_prior_observations": str(MIN_ROLLING_HISTORY),
                "lower_quantile": str(self.lower_quantile),
                "upper_quantile": str(self.upper_quantile),
            }
        return {
            "name": self.name,
            "mode": "fixed_band",
            "lower_atr_percent": str(self.lower),
            "upper_atr_percent": str(self.upper),
        }


def _quantile(ordered: list[Decimal], probability: Decimal) -> Decimal:
    if not ordered:
        raise ValueError("Cannot calculate a quantile from an empty sample.")
    position = Decimal(len(ordered) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - Decimal(lower_index)
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def candidate_bands() -> list[AtrBand]:
    return [
        AtrBand("baseline_0.08_1.20", Decimal("0.08"), Decimal("1.20")),
        AtrBand("fixed_0.05_1.50", Decimal("0.05"), Decimal("1.50")),
        AtrBand("fixed_0.03_2.00", Decimal("0.03"), Decimal("2.00")),
        AtrBand("fixed_0.02_3.00", Decimal("0.02"), Decimal("3.00")),
        AtrBand("fixed_0.10_0.80", Decimal("0.10"), Decimal("0.80")),
        AtrBand("fixed_0.05_0.80", Decimal("0.05"), Decimal("0.80")),
        AtrBand("rolling_q05_q95", lower_quantile=Decimal("0.05"), upper_quantile=Decimal("0.95")),
        AtrBand("rolling_q02_q98", lower_quantile=Decimal("0.02"), upper_quantile=Decimal("0.98")),
        AtrBand("rolling_q10_q90", lower_quantile=Decimal("0.10"), upper_quantile=Decimal("0.90")),
    ]


def _atr_series(candles: list[HistoricalCandle]) -> list[Decimal]:
    values: list[Decimal] = []
    for index, candle in enumerate(candles):
        window = candles[max(0, index - 14):index + 1]
        true_ranges = [
            max(
                window[offset].high - window[offset].low,
                abs(window[offset].high - window[offset - 1].close),
                abs(window[offset].low - window[offset - 1].close),
            )
            for offset in range(1, len(window))
        ]
        if not true_ranges:
            values.append(ZERO)
            continue
        recent = true_ranges[-14:]
        values.append(sum(recent, ZERO) / Decimal(len(recent)) / candle.close * Decimal(100))
    return values


class _ResearchOnlyAtrStrategy(SmartSniperStrategy):
    """Applies an experimental ATR gate while keeping all other rules unchanged."""

    def __init__(self, band: AtrBand, prior_atr: list[Decimal]) -> None:
        super().__init__()
        self.band = band
        self.prior_atr = [value for value in prior_atr if value > ZERO][-ROLLING_LOOKBACK:]

    def assess(
        self,
        closes: list[Decimal],
        spread_bps: Decimal = ZERO,
        *,
        higher_timeframe_closes: dict[str, list[Decimal]] | None = None,
        atr_percent: Decimal | None = None,
    ):
        actual_atr = Decimal(atr_percent if atr_percent is not None else ZERO)
        accepted = self.band.accepts(actual_atr, self.prior_atr)
        # An in-band sentinel bypasses only the production ATR gate. The actual
        # ATR is restored below; all other production strategy gates stay intact.
        assessment = super().assess(
            closes,
            spread_bps=spread_bps,
            higher_timeframe_closes=higher_timeframe_closes,
            atr_percent=Decimal("0.50"),
        )
        self.prior_atr.append(actual_atr)
        self.prior_atr = self.prior_atr[-ROLLING_LOOKBACK:]
        if not accepted and assessment.signal == "BUY":
            assessment = replace(
                assessment,
                signal="HOLD",
                atr_percent=actual_atr,
                reasons=assessment.reasons + ("Research-only ATR calibration gate rejected this candle.",),
            )
        else:
            assessment = replace(assessment, atr_percent=actual_atr)
        return assessment


def _evaluate_with_band(
    candles: list[HistoricalCandle],
    higher_timeframes: dict[str, list[HistoricalCandle]],
    atr_values: list[Decimal],
    start: int,
    end: int,
    band: AtrBand,
):
    original_strategy_factory = walk_forward_engine.SmartSniperStrategy
    walk_forward_engine.SmartSniperStrategy = lambda: _ResearchOnlyAtrStrategy(band, atr_values[:start])
    try:
        return _evaluate_fold(
            candles,
            start,
            end,
            higher_timeframes=higher_timeframes,
            initial_cash=INITIAL_CASH,
            enable_shield=True,
            enable_compounding=False,
        )
    finally:
        walk_forward_engine.SmartSniperStrategy = original_strategy_factory


def _metrics_payload(metrics) -> dict[str, object]:
    return {
        "return_percent": str(metrics.total_return_percent),
        "ending_equity_usdt": str(metrics.ending_equity),
        "net_pnl_usdt": str(metrics.ending_equity - INITIAL_CASH),
        "closed_trades": metrics.closed_trades,
        "wins": metrics.wins,
        "losses": metrics.losses,
        "win_rate_percent": str(metrics.win_rate_percent),
        "profit_factor": str(metrics.profit_factor) if metrics.profit_factor is not None else None,
        "max_drawdown_percent": str(metrics.max_drawdown_percent),
        "fees_paid_usdt": str(metrics.fees_paid),
        "shield_blocks": metrics.shield_blocks,
        "buy_candidates": metrics.buy_candidates,
        "blocked_buy_candidates": metrics.blocked_buy_candidates,
        "entry_orders": metrics.entry_orders,
    }


def _aggregate(items: list[dict[str, object]]) -> dict[str, object]:
    returns = [Decimal(str(item["return_percent"])) for item in items]
    drawdowns = [Decimal(str(item["max_drawdown_percent"])) for item in items]
    return {
        "independent_series": len(items),
        "mean_return_percent": str(sum(returns, ZERO) / Decimal(len(returns))) if items else "0",
        "total_net_pnl_usdt_across_independent_series": str(
            sum((Decimal(str(item["net_pnl_usdt"])) for item in items), ZERO)
        ),
        "total_closed_trades": sum(int(item["closed_trades"]) for item in items),
        "total_fees_paid_usdt": str(
            sum((Decimal(str(item["fees_paid_usdt"])) for item in items), ZERO)
        ),
        "mean_max_drawdown_percent": str(sum(drawdowns, ZERO) / Decimal(len(drawdowns))) if items else "0",
    }


def calibrate_interval(
    interval: str,
    symbols: list[str],
    histories: dict[tuple[str, str], list[dict]],
) -> dict[str, object]:
    bands = candidate_bands()
    baseline = bands[0]
    validation_results: dict[str, list[dict[str, object]]] = {band.name: [] for band in bands}
    prepared: dict[str, dict[str, object]] = {}

    for symbol in symbols:
        raw = histories[(symbol, interval)]
        higher_names = HIGHER_TIMEFRAMES[interval]
        all_spreads = (*ACTIVE_SPREADS, *STRESS_SPREADS)
        candles_by_spread = {
            spread: [HistoricalCandle(**row, spread_bps=spread) for row in raw]
            for spread in all_spreads
        }
        higher_by_spread = {
            spread: {
                slot: [
                    HistoricalCandle(**row, spread_bps=spread)
                    for row in histories[(symbol, timeframe)]
                ]
                for slot, timeframe in zip(("higher_1", "higher_2"), higher_names)
            }
            for spread in all_spreads
        }
        n = len(raw)
        train_end = int(n * 0.70)
        validation_end = int(n * 0.85)
        if train_end < 240 or validation_end - train_end < 60 or n - validation_end < 60:
            raise RuntimeError(f"Insufficient candles for leakage-safe 70/15/15 split: {symbol} {interval} ({n}).")
        atr_values = _atr_series(candles_by_spread[ACTIVE_SPREADS[0]])
        prepared[symbol] = {
            "candles": candles_by_spread,
            "higher": higher_by_spread,
            "atr": atr_values,
            "train_end": train_end,
            "validation_end": validation_end,
            "count": n,
            "train_end_timestamp_ms": raw[train_end - 1]["timestamp_ms"],
            "validation_end_timestamp_ms": raw[validation_end - 1]["timestamp_ms"],
            "last_timestamp_ms": raw[-1]["timestamp_ms"],
        }

    # Candidate selection uses validation only. The first 70% is historical
    # warm-up; the final 15% remains sealed until the candidate is locked.
    for band in bands:
        for symbol in symbols:
            data = prepared[symbol]
            for spread in ACTIVE_SPREADS:
                metrics = _evaluate_with_band(
                    data["candles"][spread],
                    data["higher"][spread],
                    data["atr"],
                    data["train_end"],
                    data["validation_end"],
                    band,
                )
                validation_results[band.name].append(_metrics_payload(metrics))

    baseline_validation = _aggregate(validation_results[baseline.name])
    eligible = []
    for band in bands[1:]:
        summary = _aggregate(validation_results[band.name])
        if (
            int(summary["total_closed_trades"]) >= 30
            and Decimal(str(summary["mean_return_percent"]))
            > Decimal(str(baseline_validation["mean_return_percent"]))
        ):
            eligible.append((
                Decimal(str(summary["mean_return_percent"])),
                -Decimal(str(summary["mean_max_drawdown_percent"])),
                band,
            ))
    selected = max(eligible, key=lambda item: (item[0], item[1]))[2] if eligible else baseline

    holdout_results: list[dict[str, object]] = []
    for symbol in symbols:
        data = prepared[symbol]
        for spread in (*ACTIVE_SPREADS, *STRESS_SPREADS):
            comparison = {}
            test_bands = (baseline, selected) if selected.name != baseline.name else (baseline,)
            for band in test_bands:
                metrics = _evaluate_with_band(
                    data["candles"][spread],
                    data["higher"][spread],
                    data["atr"],
                    data["validation_end"],
                    data["count"],
                    band,
                )
                comparison[band.name] = _metrics_payload(metrics)
            holdout_results.append({
                "symbol": symbol,
                "spread_assumption_bps": str(spread),
                "baseline": comparison[baseline.name],
                "selected": comparison[selected.name],
            })

    validation_summary = {
        band.name: _aggregate(validation_results[band.name])
        for band in bands
    }
    holdout_summary: dict[str, dict[str, object]] = {}
    for spread in (*ACTIVE_SPREADS, *STRESS_SPREADS):
        rows = [item for item in holdout_results if item["spread_assumption_bps"] == str(spread)]
        holdout_summary[str(spread)] = {
            "baseline": _aggregate([item["baseline"] for item in rows]),
            "selected": _aggregate([item["selected"] for item in rows]),
        }

    return {
        "interval": interval,
        "data_split": "chronological, non-overlapping 70% history/warm-up | 15% validation | 15% sealed holdout",
        "split_boundaries_by_symbol": {
            symbol: {
                "closed_candles": prepared[symbol]["count"],
                "train_end_timestamp_ms": prepared[symbol]["train_end_timestamp_ms"],
                "validation_end_timestamp_ms": prepared[symbol]["validation_end_timestamp_ms"],
                "holdout_start_timestamp_ms": prepared[symbol]["validation_end_timestamp_ms"],
                "holdout_end_timestamp_ms": prepared[symbol]["last_timestamp_ms"],
            }
            for symbol in symbols
        },
        "selected_atr_band_from_validation_only": selected.as_dict(),
        "validation_candidate_summary": validation_summary,
        "sealed_holdout_summary_by_spread_bps": holdout_summary,
        "sealed_holdout_per_symbol": holdout_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only ATR band calibration with a chronological sealed holdout."
    )
    parser.add_argument("--symbols", default=",".join(sorted(ALLOWED)))
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/atr-calibration.json"))
    args = parser.parse_args()
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    if not symbols or any(symbol not in ALLOWED for symbol in symbols):
        raise SystemExit(f"Symbols must be selected from: {', '.join(sorted(ALLOWED))}")
    if not 400 <= args.limit <= 10000:
        raise SystemExit("--limit must be between 400 and 10000.")

    required_timeframes = set(HIGHER_TIMEFRAMES)
    for pair in HIGHER_TIMEFRAMES.values():
        required_timeframes.update(pair)
    histories: dict[tuple[str, str], list[dict]] = {}
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json"}) as client:
        for symbol in symbols:
            for timeframe in sorted(required_timeframes):
                histories[(symbol, timeframe)] = fetch_history(
                    client, symbol, timeframe, args.limit
                )

    reports = [
        calibrate_interval(interval, symbols, histories)
        for interval in HIGHER_TIMEFRAMES
    ]
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source": "BINANCE_PUBLIC_CLOSED_CANDLES",
        "research_only": True,
        "production_strategy_modified": False,
        "paper_only": True,
        "capital_reference_usdt": "70",
        "stop_loss_percent_fixed": "0.50",
        "take_profit_rule": "unchanged strategy-selected 1.25%-1.50% (2.5R/3R)",
        "compounding_enabled": False,
        "shield_enabled": True,
        "execution_cost_assumptions": {
            "taker_fee_per_side_percent": "0.10",
            "slippage_bps_per_side": "2",
            "spread_scenarios_bps": ["5", "10", "20", "30"],
            "spread_fill_model": "half spread on entry and half spread on exit",
            "strategy_cost_gate_includes_existing_4_bps_buffer": True,
        },
        "split_policy": {
            "chronological": True,
            "train_history_percent": 70,
            "validation_percent": 15,
            "sealed_holdout_percent": 15,
            "segments_overlap": False,
            "candidate_selection_uses_holdout": False,
            "dynamic_atr_uses_only_prior_closed_candles": True,
        },
        "candidate_bands": [band.as_dict() for band in candidate_bands()],
        "interpretation_limits": [
            "Each symbol/timeframe/spread result is an independent 70 USDT Paper simulation, not a combined multi-symbol portfolio.",
            "ATR candidates are selected on validation only; the final chronological 15% is evaluated only after selection.",
            "Rolling quantile bands use only the preceding 240 ATR observations and require 60 prior observations.",
            "All non-ATR strategy gates, target selection, Shield, fees, slippage and spread fill assumptions remain unchanged.",
            "Compounding is disabled to isolate ATR effects; the research engine's fixed ticket and reserve behavior remain unchanged.",
            "Stops and targets are evaluated using the existing candle-close research simulator, not reconstructed intrabar order-book paths.",
            "A positive result in this historical holdout is not a guarantee of future profitability and does not authorize production changes.",
        ],
        "results": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
