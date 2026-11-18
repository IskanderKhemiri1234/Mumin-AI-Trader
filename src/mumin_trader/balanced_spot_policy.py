"""Approved balanced Spot policy for the isolated Mumin Paper engine.

This module defines policy only. It does not connect to Binance or submit orders.
The existing 5 USDT per-trade notional ceiling is intentionally preserved.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class BalancedSpotPolicy:
    """Immutable, fail-closed settings for the first BTCUSDT strategy release."""

    symbol: str = "BTCUSDT"
    entry_interval: str = "15m"
    initial_capital_usdt: Decimal = Decimal("70.00")
    risk_budget_per_trade_usdt: Decimal = Decimal("0.35")
    max_position_quote_usdt: Decimal = Decimal("5.00")
    min_position_quote_usdt: Decimal = Decimal("5.00")
    max_open_positions: int = 1
    trading_mode: str = "PAPER"
    trading_environment: str = "TESTNET"
    spot_only: bool = True
    leverage: Decimal = ZERO
    mainnet_enabled: bool = False

    # Two 0.1% commissions plus two 2-bps simulated slippage legs.
    estimated_round_trip_cost_rate: Decimal = Decimal("0.0024")

    def __post_init__(self) -> None:
        if self.symbol != "BTCUSDT":
            raise ValueError("The first balanced release is restricted to BTCUSDT.")
        if self.entry_interval != "15m":
            raise ValueError("The first balanced release requires 15m entry candles.")
        if self.initial_capital_usdt != Decimal("70.00"):
            raise ValueError("Paper capital is fixed at 70.00 USDT.")
        if self.risk_budget_per_trade_usdt != Decimal("0.35"):
            raise ValueError("Per-trade risk budget is fixed at 0.35 USDT.")
        if self.max_position_quote_usdt != Decimal("5.00"):
            raise ValueError("The existing 5.00 USDT per-trade notional cap must be preserved.")
        if self.min_position_quote_usdt != Decimal("5.00"):
            raise ValueError("The existing 5.00 USDT minimum notional must be preserved.")
        if self.max_open_positions != 1:
            raise ValueError("The balanced release permits one open position only.")
        if self.trading_mode != "PAPER" or self.trading_environment != "TESTNET":
            raise ValueError("The balanced policy is restricted to isolated TESTNET/PAPER mode.")
        if not self.spot_only or self.leverage != ZERO:
            raise ValueError("Only unleveraged Spot policy is permitted.")
        if self.mainnet_enabled:
            raise ValueError("Binance Mainnet must remain disabled.")
        if self.estimated_round_trip_cost_rate < ZERO:
            raise ValueError("Estimated round-trip costs cannot be negative.")

    def position_quote_for_risk(
        self,
        stop_loss_pct: Decimal,
        available_cash_usdt: Decimal,
    ) -> Decimal:
        """Return a cent-rounded notional within risk, cash and legacy caps.

        stop_loss_pct is expressed as a percentage (for example Decimal("0.5")
        means a 0.5% stop). Estimated fees and slippage are included in the
        risk-budget sizing denominator. Return zero when the result cannot
        satisfy the existing 5 USDT minimum.
        """
        stop_pct = Decimal(stop_loss_pct)
        available_cash = Decimal(available_cash_usdt)
        if not stop_pct.is_finite() or stop_pct <= ZERO or stop_pct >= ONE_HUNDRED:
            raise ValueError("stop_loss_pct must be finite and between 0 and 100.")
        if not available_cash.is_finite() or available_cash < ZERO:
            raise ValueError("available_cash_usdt must be finite and non-negative.")

        stop_rate = stop_pct / ONE_HUNDRED
        total_risk_rate = stop_rate + self.estimated_round_trip_cost_rate
        if total_risk_rate <= ZERO:
            return ZERO

        risk_limited_quote = self.risk_budget_per_trade_usdt / total_risk_rate
        quote = min(
            risk_limited_quote,
            self.max_position_quote_usdt,
            available_cash,
        ).quantize(CENT, rounding=ROUND_DOWN)

        if quote < self.min_position_quote_usdt:
            return ZERO
        return quote


DEFAULT_BALANCED_SPOT_POLICY = BalancedSpotPolicy()
