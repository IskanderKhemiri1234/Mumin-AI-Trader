"""Deterministic, exchange-independent filters for the eight-asset Spot research stack.

All inputs must represent completed candles. This module is pure: it does not
fetch data, submit orders, read credentials, or enable live execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")
BPS = Decimal("10000")

CORE_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
)


@dataclass(frozen=True, slots=True)
class FilterDecision:
    passed: bool
    reason: str
    score: int
    metrics: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class SymbolAssessment:
    symbol: str
    approved: bool
    score: int
    net_edge_bps: Decimal
    rejection_reasons: tuple[str, ...]


def _d(value: Decimal | str | int) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("INVALID_NUMERIC_INPUT") from None
    if not result.is_finite():
        raise ValueError("NON_FINITE_NUMERIC_INPUT")
    return result


def _ema(values: Sequence[Decimal], period: int) -> Decimal:
    if period <= 0 or len(values) < period:
        raise ValueError("INSUFFICIENT_CLOSED_CANDLES_FOR_EMA")
    multiplier = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], ZERO) / Decimal(period)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def _valid_positive(values: Sequence[Decimal]) -> bool:
    return bool(values) and all(value.is_finite() and value > ZERO for value in values)


def _rsi(closes: Sequence[Decimal], period: int = 14) -> Decimal | None:
    if len(closes) <= period:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    window = changes[-period:]
    gains = sum((max(change, ZERO) for change in window), ZERO) / Decimal(period)
    losses = sum((max(-change, ZERO) for change in window), ZERO) / Decimal(period)
    if losses == ZERO:
        return HUNDRED if gains > ZERO else Decimal("50")
    return HUNDRED - HUNDRED / (ONE + gains / losses)


def evaluate_mtf_trend(
    timeframes: Mapping[str, Sequence[Decimal]],
    *,
    required_timeframes: tuple[str, ...] = ("5m", "15m", "1h", "4h"),
    fast_period: int = 9,
    slow_period: int = 21,
) -> FilterDecision:
    """Require bullish EMA alignment on every required, closed-candle timeframe."""
    if not required_timeframes or fast_period <= 0 or slow_period <= fast_period:
        return FilterDecision(False, "INVALID_MTF_CONFIGURATION", 0)
    alignments = 0
    weakest_gap = Decimal("Infinity")
    for timeframe in required_timeframes:
        raw = timeframes.get(timeframe)
        if raw is None or len(raw) < slow_period:
            return FilterDecision(False, f"MTF_DATA_MISSING:{timeframe}", 0)
        closes = [_d(value) for value in raw]
        if not _valid_positive(closes):
            return FilterDecision(False, f"MTF_DATA_INVALID:{timeframe}", 0)
        fast = _ema(closes, fast_period)
        slow = _ema(closes, slow_period)
        gap_bps = (fast / slow - ONE) * BPS
        weakest_gap = min(weakest_gap, gap_bps)
        if fast > slow and closes[-1] > fast:
            alignments += 1
    if alignments != len(required_timeframes):
        return FilterDecision(
            False, "MTF_BULLISH_ALIGNMENT_FAILED", 0,
            (("aligned_frames", Decimal(alignments)),),
        )
    return FilterDecision(
        True, "MTF_ALL_FRAMES_BULLISH", 100,
        (("aligned_frames", Decimal(alignments)), ("weakest_gap_bps", weakest_gap)),
    )


def evaluate_momentum(
    closes: Sequence[Decimal],
    volumes: Sequence[Decimal],
    *,
    lookback: int = 5,
    minimum_return_bps: Decimal = Decimal("5"),
    minimum_volume_ratio: Decimal = Decimal("1.20"),
    minimum_rsi: Decimal = Decimal("45"),
    maximum_rsi: Decimal = Decimal("68"),
) -> FilterDecision:
    """Require positive price impulse, healthy RSI and confirming volume."""
    if lookback <= 0 or len(closes) < max(lookback + 1, 15) or len(volumes) < 9:
        return FilterDecision(False, "MOMENTUM_WARMUP_INSUFFICIENT_DATA", 0)
    prices = [_d(value) for value in closes]
    volume_values = [_d(value) for value in volumes]
    if not _valid_positive(prices) or not _valid_positive(volume_values):
        return FilterDecision(False, "MOMENTUM_INVALID_PRICE_OR_VOLUME", 0)
    change_bps = (prices[-1] / prices[-1 - lookback] - ONE) * BPS
    rsi_value = _rsi(prices)
    baseline = sum(volume_values[-9:-1], ZERO) / Decimal(8)
    volume_ratio = volume_values[-1] / baseline if baseline > ZERO else ZERO
    if change_bps <= _d(minimum_return_bps):
        return FilterDecision(False, "MOMENTUM_PRICE_IMPULSE_TOO_WEAK", 0,
                              (("return_bps", change_bps),))
    if rsi_value is None or not _d(minimum_rsi) <= rsi_value <= _d(maximum_rsi):
        return FilterDecision(False, "MOMENTUM_RSI_OUTSIDE_ENTRY_BAND", 0,
                              (("rsi", rsi_value or ZERO),))
    if volume_ratio < _d(minimum_volume_ratio):
        return FilterDecision(False, "MOMENTUM_VOLUME_CONFIRMATION_FAILED", 0,
                              (("volume_ratio", volume_ratio),))
    score = 70 + (20 if change_bps >= Decimal("25") else 10)
    score += 10 if volume_ratio >= Decimal("1.5") else 0
    return FilterDecision(True, "MOMENTUM_CONFIRMED", min(score, 100),
                          (("return_bps", change_bps), ("rsi", rsi_value),
                           ("volume_ratio", volume_ratio)))


def evaluate_liquidity(
    *,
    bid: Decimal,
    ask: Decimal,
    quote_volume_24h: Decimal,
    minimum_quote_volume: Decimal = Decimal("1000000"),
    maximum_spread_bps: Decimal = Decimal("8"),
    bid_depth_quote: Decimal | None = None,
    ask_depth_quote: Decimal | None = None,
    minimum_depth_quote: Decimal = ZERO,
) -> FilterDecision:
    """Reject weak turnover, invalid books, wide spreads and inadequate depth."""
    bid_value, ask_value = _d(bid), _d(ask)
    volume = _d(quote_volume_24h)
    if bid_value <= ZERO or ask_value < bid_value or volume < ZERO:
        return FilterDecision(False, "LIQUIDITY_INVALID_BOOK_OR_VOLUME", 0)
    midpoint = (bid_value + ask_value) / Decimal(2)
    if midpoint <= ZERO:
        return FilterDecision(False, "LIQUIDITY_INVALID_MIDPOINT", 0)
    spread = (ask_value - bid_value) / midpoint * BPS
    if spread >= _d(maximum_spread_bps):
        return FilterDecision(False, "LIQUIDITY_SPREAD_LIMIT_REJECTED", 0,
                              (("spread_bps", spread),))
    if volume < _d(minimum_quote_volume):
        return FilterDecision(False, "LIQUIDITY_24H_VOLUME_TOO_LOW", 0,
                              (("quote_volume_24h", volume),))
    if bid_depth_quote is not None or ask_depth_quote is not None:
        if bid_depth_quote is None or ask_depth_quote is None:
            return FilterDecision(False, "LIQUIDITY_DEPTH_DATA_INCOMPLETE", 0)
        bid_depth, ask_depth = _d(bid_depth_quote), _d(ask_depth_quote)
        if min(bid_depth, ask_depth) < _d(minimum_depth_quote):
            return FilterDecision(False, "LIQUIDITY_ORDER_BOOK_DEPTH_TOO_LOW", 0)
    score = 100 if spread <= Decimal("3") else 90 if spread <= Decimal("5") else 75
    return FilterDecision(True, "LIQUIDITY_GATE_PASSED", score,
                          (("spread_bps", spread), ("quote_volume_24h", volume)))


def evaluate_anti_chase_and_breakout(
    *,
    closes: Sequence[Decimal],
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    volumes: Sequence[Decimal],
    maximum_extension_bps: Decimal = Decimal("60"),
    breakout_volume_ratio: Decimal = Decimal("1.20"),
) -> FilterDecision:
    """Avoid extended entries and reject breakouts lacking close/volume confirmation."""
    if min(len(closes), len(highs), len(lows), len(volumes)) < 22:
        return FilterDecision(False, "PRICE_ACTION_WARMUP_INSUFFICIENT_DATA", 0)
    c = [_d(value) for value in closes]
    h = [_d(value) for value in highs]
    low_values = [_d(value) for value in lows]
    v = [_d(value) for value in volumes]
    if not all(_valid_positive(values) for values in (c, h, low_values, v)):
        return FilterDecision(False, "PRICE_ACTION_INVALID_OHLCV", 0)
    if any(low > high for low, high in zip(low_values, h)):
        return FilterDecision(False, "PRICE_ACTION_INVALID_CANDLE_RANGE", 0)
    ema20 = _ema(c, 20)
    extension = (c[-1] / ema20 - ONE) * BPS
    if extension > _d(maximum_extension_bps):
        return FilterDecision(False, "ANTI_CHASE_PRICE_TOO_EXTENDED", 0,
                              (("extension_bps", extension),))
    previous_high = max(h[-21:-1])
    if h[-1] > previous_high and c[-1] <= previous_high:
        return FilterDecision(False, "FALSE_BREAKOUT_CLOSE_FAILED", 0,
                              (("previous_high", previous_high), ("close", c[-1])))
    if c[-1] > previous_high:
        candle_range = h[-1] - low_values[-1]
        close_location = (c[-1] - low_values[-1]) / candle_range if candle_range > ZERO else ZERO
        baseline = sum(v[-9:-1], ZERO) / Decimal(8)
        volume_ratio = v[-1] / baseline if baseline > ZERO else ZERO
        if close_location < Decimal("0.65") or volume_ratio < _d(breakout_volume_ratio):
            return FilterDecision(False, "FALSE_BREAKOUT_CONFIRMATION_FAILED", 0,
                                  (("close_location", close_location),
                                   ("volume_ratio", volume_ratio)))
        return FilterDecision(True, "BREAKOUT_CLOSE_AND_VOLUME_CONFIRMED", 100,
                              (("extension_bps", extension), ("volume_ratio", volume_ratio)))
    return FilterDecision(True, "ANTI_CHASE_GATE_PASSED", 85,
                          (("extension_bps", extension),))


def evaluate_market_regime(
    *,
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    minimum_atr_pct: Decimal = Decimal("0.05"),
    maximum_atr_pct: Decimal = Decimal("0.80"),
    minimum_efficiency_ratio: Decimal = Decimal("0.15"),
) -> FilterDecision:
    """Avoid dead and disorderly markets using ATR% and directional efficiency."""
    if min(len(highs), len(lows), len(closes)) < 16:
        return FilterDecision(False, "REGIME_WARMUP_INSUFFICIENT_DATA", 0)
    h, low_values, c = ([_d(value) for value in values] for values in (highs, lows, closes))
    if not all(_valid_positive(values) for values in (h, low_values, c)):
        return FilterDecision(False, "REGIME_INVALID_OHLC", 0)
    if any(low > high for low, high in zip(low_values, h)):
        return FilterDecision(False, "REGIME_INVALID_CANDLE_RANGE", 0)
    true_ranges = []
    for index in range(len(c) - 14, len(c)):
        previous = c[index - 1]
        true_ranges.append(max(h[index] - low_values[index], abs(h[index] - previous),
                               abs(low_values[index] - previous)))
    atr_pct = sum(true_ranges, ZERO) / Decimal(len(true_ranges)) / c[-1] * HUNDRED
    changes = [abs(c[i] - c[i - 1]) for i in range(len(c) - 14, len(c))]
    path = sum(changes, ZERO)
    efficiency = abs(c[-1] - c[-15]) / path if path > ZERO else ZERO
    if not _d(minimum_atr_pct) <= atr_pct <= _d(maximum_atr_pct):
        return FilterDecision(False, "REGIME_ATR_OUTSIDE_ACCEPTABLE_BAND", 0,
                              (("atr_pct", atr_pct), ("efficiency_ratio", efficiency)))
    if efficiency < _d(minimum_efficiency_ratio):
        return FilterDecision(False, "REGIME_DIRECTIONAL_EFFICIENCY_TOO_LOW", 0,
                              (("atr_pct", atr_pct), ("efficiency_ratio", efficiency)))
    score = 100 if efficiency >= Decimal("0.35") else 80
    return FilterDecision(True, "MARKET_REGIME_ACCEPTABLE", score,
                          (("atr_pct", atr_pct), ("efficiency_ratio", efficiency)))


def evaluate_net_edge(
    *,
    target_pct: Decimal,
    spread_bps: Decimal,
    fee_rate: Decimal = Decimal("0.001"),
    slippage_bps_per_side: Decimal = Decimal("2"),
    minimum_net_edge_bps: Decimal = Decimal("10"),
) -> FilterDecision:
    """Require target edge to exceed estimated round-trip fees, spread and slippage."""
    target, spread = _d(target_pct), _d(spread_bps)
    fee, slippage, minimum = (_d(value) for value in
                              (fee_rate, slippage_bps_per_side, minimum_net_edge_bps))
    if target <= ZERO or min(spread, fee, slippage, minimum) < ZERO:
        return FilterDecision(False, "NET_EDGE_INVALID_COST_OR_TARGET", 0)
    gross = target * BPS / HUNDRED
    costs = fee * BPS * Decimal(2) + spread + slippage * Decimal(2)
    net = gross - costs
    if net < minimum:
        return FilterDecision(False, "NET_EDGE_INSUFFICIENT_AFTER_COSTS", 0,
                              (("gross_edge_bps", gross), ("estimated_cost_bps", costs),
                               ("net_edge_bps", net)))
    score = 100 if net >= minimum * Decimal(3) else 85 if net >= minimum * Decimal(2) else 70
    return FilterDecision(True, "NET_EDGE_PASSED", score,
                          (("gross_edge_bps", gross), ("estimated_cost_bps", costs),
                           ("net_edge_bps", net)))


def assess_symbol(
    symbol: str,
    *,
    trend: FilterDecision,
    momentum: FilterDecision,
    liquidity: FilterDecision,
    price_action: FilterDecision,
    regime: FilterDecision,
    net_edge: FilterDecision,
) -> SymbolAssessment:
    """Aggregate independent gates for one member of the fixed eight-asset universe."""
    normalized = symbol.upper()
    if normalized not in CORE_SYMBOLS:
        raise ValueError("SYMBOL_OUTSIDE_CORE_EIGHT_ASSET_UNIVERSE")
    gates = (trend, momentum, liquidity, price_action, regime, net_edge)
    reasons = tuple(gate.reason for gate in gates if not gate.passed)
    score = sum(gate.score for gate in gates) // len(gates)
    edge_metric = dict(net_edge.metrics).get("net_edge_bps", ZERO)
    return SymbolAssessment(normalized, not reasons, score if not reasons else 0,
                            edge_metric, reasons)


def rank_symbol_assessments(
    assessments: Sequence[SymbolAssessment],
) -> list[SymbolAssessment]:
    """Rank eligible symbols first, then quality score and net edge, deterministically."""
    if any(item.symbol not in CORE_SYMBOLS for item in assessments):
        raise ValueError("SYMBOL_OUTSIDE_CORE_EIGHT_ASSET_UNIVERSE")
    return sorted(assessments, key=lambda item: (
        item.approved, item.score, item.net_edge_bps, item.symbol
    ), reverse=True)
