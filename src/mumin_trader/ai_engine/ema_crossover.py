"""Deterministic EMA-crossover signal calculation for isolated Paper research.

This module has no exchange client, credential handling, or order-submission path.
Only caller-supplied CLOSED candle closes are evaluated.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class EMACrossoverSignal:
    signal: str
    fast_previous: Decimal
    fast_current: Decimal
    slow_previous: Decimal
    slow_current: Decimal
    fast_period: int
    slow_period: int


def calculate_ema(closes: Iterable[Decimal], period: int) -> tuple[Decimal, ...]:
    """Calculate an EMA using the first close as the initial seed.

    The caller must supply chronological, fully closed candle closes. Decimal
    arithmetic avoids introducing binary floating-point rounding in signals.
    """
    values = tuple(Decimal(value) for value in closes)
    if period < 2:
        raise ValueError("EMA period must be at least 2.")
    if len(values) < period + 1:
        raise ValueError(f"EMA requires at least {period + 1} closes.")
    if any(not value.is_finite() or value <= ZERO for value in values):
        raise ValueError("EMA closes must be positive finite Decimal values.")

    multiplier = Decimal("2") / Decimal(period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * multiplier + result[-1] * (Decimal("1") - multiplier))
    return tuple(result)


def evaluate_ema_crossover(
    closes: Iterable[Decimal],
    *,
    fast_period: int = 20,
    slow_period: int = 50,
) -> EMACrossoverSignal:
    """Return BUY/SELL only on a fresh fast/slow EMA crossover; otherwise HOLD."""
    if fast_period < 2 or slow_period < 2:
        raise ValueError("EMA periods must be at least 2.")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be smaller than slow_period.")

    values = tuple(Decimal(value) for value in closes)
    required = slow_period + 1
    if len(values) < required:
        raise ValueError(f"EMA crossover requires at least {required} closed candles.")

    fast = calculate_ema(values, fast_period)
    slow = calculate_ema(values, slow_period)
    fast_previous, fast_current = fast[-2], fast[-1]
    slow_previous, slow_current = slow[-2], slow[-1]

    if fast_previous <= slow_previous and fast_current > slow_current:
        signal = "BUY"
    elif fast_previous >= slow_previous and fast_current < slow_current:
        signal = "SELL"
    else:
        signal = "HOLD"

    return EMACrossoverSignal(
        signal=signal,
        fast_previous=fast_previous,
        fast_current=fast_current,
        slow_previous=slow_previous,
        slow_current=slow_current,
        fast_period=fast_period,
        slow_period=slow_period,
    )
