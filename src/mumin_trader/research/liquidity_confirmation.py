from __future__ import annotations

from decimal import Decimal

MINIMUM_RATIO = Decimal("0.80")
MAXIMUM_RATIO = Decimal("2.50")


def liquidity_confirmation_passes(
    quote_volume_ratio: Decimal | None,
    trade_count_ratio: Decimal | None,
) -> bool:
    """Require both closed-candle activity ratios inside the frozen inclusive band.

    Missing, zero, negative, or non-finite observations fail closed. Ratios are
    computed by the caller from the current closed candle and prior 20 closed bars.
    """
    if quote_volume_ratio is None or trade_count_ratio is None:
        return False
    volume = Decimal(quote_volume_ratio)
    trades = Decimal(trade_count_ratio)
    if (
        not volume.is_finite()
        or not trades.is_finite()
        or volume <= Decimal(0)
        or trades <= Decimal(0)
    ):
        return False
    return (
        MINIMUM_RATIO <= volume <= MAXIMUM_RATIO
        and MINIMUM_RATIO <= trades <= MAXIMUM_RATIO
    )
