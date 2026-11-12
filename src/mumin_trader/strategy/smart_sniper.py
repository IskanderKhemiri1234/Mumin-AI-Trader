from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class SniperConfig:
    min_momentum_bps: Decimal = Decimal("8")
    min_volume_ratio: Decimal = Decimal("1.50")
    min_book_imbalance: Decimal = Decimal("0.12")
    initial_stop_pct: Decimal = Decimal("0.50")
    trailing_activation_pct: Decimal = Decimal("0.35")
    trailing_distance_pct: Decimal = Decimal("0.40")
    reversal_momentum_bps: Decimal = Decimal("8")
    reversal_book_imbalance: Decimal = Decimal("-0.10")


@dataclass(frozen=True, slots=True)
class EntrySignal:
    accepted: bool
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExitDecision:
    high_water_price: Decimal
    trailing_stop_price: Decimal
    should_exit: bool
    reason: str


def evaluate_entry(
    momentum_bps: Decimal,
    volume_ratio: Decimal,
    book_imbalance: Decimal,
    config: SniperConfig = SniperConfig(),
) -> EntrySignal:
    """Require aligned momentum, abnormal volume and positive depth imbalance."""
    momentum_bps = Decimal(momentum_bps)
    volume_ratio = Decimal(volume_ratio)
    book_imbalance = Decimal(book_imbalance)
    reasons = []
    if momentum_bps >= config.min_momentum_bps:
        reasons.append("MOMENTUM")
    if volume_ratio >= config.min_volume_ratio:
        reasons.append("VOLUME_SURGE")
    if book_imbalance >= config.min_book_imbalance:
        reasons.append("BID_IMBALANCE")
    return EntrySignal(len(reasons) == 3, len(reasons), tuple(reasons))


def evaluate_exit(
    entry_price: Decimal,
    high_water_price: Decimal,
    current_price: Decimal,
    momentum_bps: Decimal,
    book_imbalance: Decimal,
    config: SniperConfig = SniperConfig(),
) -> ExitDecision:
    """Update a persisted high-water mark and calculate a ratcheting protective stop."""
    entry = Decimal(entry_price)
    high = max(Decimal(high_water_price), Decimal(current_price))
    price = Decimal(current_price)
    momentum = Decimal(momentum_bps)
    imbalance = Decimal(book_imbalance)
    if min(entry, high, price) <= ZERO:
        raise ValueError("Entry, high-water and current prices must be positive.")

    hard_stop = entry * (Decimal(1) - config.initial_stop_pct / Decimal(100))
    activation = entry * (Decimal(1) + config.trailing_activation_pct / Decimal(100))
    trailing_active = high >= activation
    trailing_stop = (
        high * (Decimal(1) - config.trailing_distance_pct / Decimal(100))
        if trailing_active else hard_stop
    )
    stop_price = max(hard_stop, trailing_stop)

    if price <= stop_price:
        reason = "TRAILING_STOP" if trailing_active else "INITIAL_STOP"
        return ExitDecision(high, stop_price, True, reason)

    # A momentum reversal is accepted only when the order book also turns ask-heavy.
    if momentum <= -config.reversal_momentum_bps and imbalance <= config.reversal_book_imbalance:
        return ExitDecision(high, stop_price, True, "CONFIRMED_MOMENTUM_REVERSAL")

    return ExitDecision(high, stop_price, False, "HOLD_TREND")


def volume_surge_ratio(volumes: list[Decimal]) -> Decimal:
    """Latest closed candle volume divided by the mean of preceding closed candles."""
    if len(volumes) < 2:
        return ZERO
    values = [Decimal(value) for value in volumes]
    baseline = values[:-1]
    if any(value < ZERO for value in values):
        raise ValueError("Volumes cannot be negative.")
    average = sum(baseline, ZERO) / Decimal(len(baseline))
    return values[-1] / average if average > ZERO else ZERO


def order_book_imbalance(
    bid_notional: Decimal,
    ask_notional: Decimal,
) -> Decimal:
    """Normalized top-of-book depth imbalance in [-1, 1], measured in quote notional."""
    bid = Decimal(bid_notional)
    ask = Decimal(ask_notional)
    if bid < ZERO or ask < ZERO:
        raise ValueError("Order-book notionals cannot be negative.")
    total = bid + ask
    return (bid - ask) / total if total > ZERO else ZERO
