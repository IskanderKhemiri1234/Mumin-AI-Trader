from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Sequence

from mumin_trader.risk.anti_loss_quantum_shield import AntiLossQuantumShield
from mumin_trader.strategy.compounding import DynamicCompoundingMatrix
from mumin_trader.strategy.smart_sniper import SmartSniperStrategy

ZERO = Decimal("0")
BPS = Decimal("10000")
FEE_RATE = Decimal("0.001")
SLIPPAGE_BPS = Decimal("2")
INITIAL_CASH = Decimal("70")
RESERVE_CASH = Decimal("14")
BASE_TICKET = Decimal("1")
MAX_TICKET = Decimal("5")
STOP_LOSS_PERCENT = Decimal("0.50")
TAKE_PROFIT_PERCENT = Decimal("1.00")


@dataclass(frozen=True, slots=True)
class HistoricalCandle:
    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    quote_volume: Decimal
    trade_count: Decimal
    spread_bps: Decimal

    def validate(self) -> None:
        values = (
            self.open, self.high, self.low, self.close,
            self.quote_volume, self.trade_count, self.spread_bps,
        )
        if self.timestamp_ms <= 0 or any(not Decimal(v).is_finite() for v in values):
            raise ValueError("Historical candle contains non-finite or invalid fields.")
        if min(self.open, self.high, self.low, self.close) <= ZERO:
            raise ValueError("Historical OHLC prices must be positive.")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Historical candle high is inconsistent.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Historical candle low is inconsistent.")
        if self.quote_volume < ZERO or self.trade_count < ZERO or self.spread_bps < ZERO:
            raise ValueError("Volume, trade count and spread must be non-negative.")


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    start_index: int
    end_index: int
    ending_equity: Decimal
    total_return_percent: Decimal
    max_drawdown_percent: Decimal
    closed_trades: int
    wins: int
    losses: int
    win_rate_percent: Decimal
    profit_factor: Decimal | None
    fees_paid: Decimal
    shield_blocks: int
    buy_candidates: int
    blocked_buy_candidates: int
    entry_orders: int
    compounding_reinvested: Decimal


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    warmup_bars: int
    test_bars: int
    step_bars: int
    folds: tuple[BacktestMetrics, ...]


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _evaluate_fold(
    candles: Sequence[HistoricalCandle],
    start: int,
    end: int,
    *,
    initial_cash: Decimal = INITIAL_CASH,
) -> BacktestMetrics:
    strategy = SmartSniperStrategy()
    shield = AntiLossQuantumShield()
    compounding = DynamicCompoundingMatrix()
    cash = Decimal(initial_cash)
    realized = ZERO
    fees_paid = ZERO
    high_water = cash
    max_drawdown = ZERO
    closed_trades = wins = losses = shield_blocks = 0
    buy_candidates = blocked_buy_candidates = entry_orders = 0
    gross_wins = gross_losses = ZERO
    compounding_reinvested = ZERO
    position: dict[str, Decimal] | None = None
    pending_entry: dict[str, Decimal] | None = None

    for index in range(start, end):
        bar = candles[index]
        if pending_entry is not None:
            half_spread = bar.spread_bps / (BPS * Decimal("2"))
            entry_fill = bar.open * (Decimal("1") + half_spread)
            entry_fill *= Decimal("1") + SLIPPAGE_BPS / BPS
            allocation = min(
                pending_entry["allocation"],
                max(ZERO, cash - RESERVE_CASH),
            )
            quantity = (
                allocation / (entry_fill * (Decimal("1") + FEE_RATE))
            ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            entry_gross = quantity * entry_fill
            entry_fee = (entry_gross * FEE_RATE).quantize(Decimal("0.00000001"))
            if quantity > ZERO and entry_gross + entry_fee >= Decimal("1") and entry_gross + entry_fee <= cash - RESERVE_CASH:
                cash -= entry_gross + entry_fee
                fees_paid += entry_fee
                position = {
                    "quantity": quantity,
                    "entry_gross": entry_gross,
                    "entry_fee": entry_fee,
                    "realized_net": ZERO,
                }
            pending_entry = None

        closes = [item.close for item in candles[: index + 1]]
        assessment = strategy.assess(closes, spread_bps=bar.spread_bps)
        prior = candles[index - 1].close if index > 0 else bar.close
        price_change_bps = (bar.close / prior - Decimal("1")) * BPS
        prior_window = candles[max(0, index - 20):index]
        median_volume = _median([item.quote_volume for item in prior_window])
        median_trades = _median([item.trade_count for item in prior_window])
        volume_ratio = (
            bar.quote_volume / median_volume
            if len(prior_window) == 20 and median_volume and median_volume > ZERO
            else None
        )
        trade_ratio = (
            bar.trade_count / median_trades
            if len(prior_window) == 20 and median_trades and median_trades > ZERO
            else None
        )
        shield_decision = shield.assess(
            spread_bps=bar.spread_bps,
            quote_volume_ratio=volume_ratio,
            trade_count_ratio=trade_ratio,
            price_change_bps=price_change_bps,
            candle_age_ms=0,
        )

        if shield_decision.block_entries:
            shield_blocks += 1
        if assessment.signal == "BUY":
            buy_candidates += 1
            if shield_decision.block_entries:
                blocked_buy_candidates += 1

        if position is not None:
            half_spread = bar.spread_bps / (BPS * Decimal("2"))
            exit_fill = bar.close * (Decimal("1") - half_spread)
            exit_fill *= Decimal("1") - SLIPPAGE_BPS / BPS
            quantity = position["quantity"]
            exit_gross = quantity * exit_fill
            exit_fee = (exit_gross * FEE_RATE).quantize(Decimal("0.00000001"))
            entry_cost = position["entry_gross"] + position["entry_fee"]
            net_return_percent = (
                (exit_gross - exit_fee - entry_cost) / entry_cost * Decimal("100")
                if entry_cost > ZERO else Decimal("-100")
            )
            hard_exit = (
                net_return_percent <= -STOP_LOSS_PERCENT
                or net_return_percent >= TAKE_PROFIT_PERCENT
                or assessment.signal == "SELL"
            )
            exit_fraction = Decimal("1") if hard_exit else shield_decision.exit_fraction
            quantity_sold = (
                quantity * exit_fraction
            ).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
            if quantity_sold > ZERO:
                fraction = quantity_sold / quantity
                sold_gross = quantity_sold * exit_fill
                sold_fee = (sold_gross * FEE_RATE).quantize(Decimal("0.00000001"))
                sold_entry_gross = position["entry_gross"] * fraction
                sold_entry_fee = position["entry_fee"] * fraction
                net_trade = sold_gross - sold_fee - sold_entry_gross - sold_entry_fee
                cash += sold_gross - sold_fee
                realized += net_trade
                fees_paid += sold_fee
                position["realized_net"] += net_trade
                remaining = quantity - quantity_sold
                if remaining > ZERO:
                    position["quantity"] = remaining
                    position["entry_gross"] -= sold_entry_gross
                    position["entry_fee"] -= sold_entry_fee
                else:
                    closed_trades += 1
                    if position["realized_net"] > ZERO:
                        wins += 1
                        gross_wins += position["realized_net"]
                    else:
                        losses += 1
                        gross_losses += abs(position["realized_net"])
                    position = None

        if (
            position is None and pending_entry is None
            and assessment.signal == "BUY" and not shield_decision.block_entries
        ):
            drawdown_percent = (
                (high_water - cash) / high_water * Decimal("100")
                if high_water > ZERO else ZERO
            )
            matrix = compounding.assess(
                realized_net_pnl=realized,
                momentum_score=assessment.score,
                drawdown_percent=drawdown_percent,
            )
            # A $1 research seed is exposed; only the incremental amount above
            # it can come from realized profits. The $5 hard ticket ceiling stays.
            allocation = min(MAX_TICKET, BASE_TICKET + matrix.reinvestment_budget)
            allocation = min(allocation, max(ZERO, cash - RESERVE_CASH))
            if allocation >= Decimal("1"):
                pending_entry = {"allocation": allocation}
                entry_orders += 1
                compounding_reinvested += max(ZERO, allocation - BASE_TICKET)

        marked_equity = cash
        if position is not None:
            marked_equity += position["quantity"] * bar.close
        high_water = max(high_water, marked_equity)
        if high_water > ZERO:
            max_drawdown = max(
                max_drawdown,
                (high_water - marked_equity) / high_water * Decimal("100"),
            )

    # Liquidate any remaining Paper position at the final test close so every
    # fold reports realized, fee-adjusted out-of-sample equity.
    if position is not None:
        last = candles[end - 1]
        half_spread = last.spread_bps / (BPS * Decimal("2"))
        exit_fill = last.close * (Decimal("1") - half_spread)
        exit_fill *= Decimal("1") - SLIPPAGE_BPS / BPS
        gross = position["quantity"] * exit_fill
        fee = (gross * FEE_RATE).quantize(Decimal("0.00000001"))
        net_trade = gross - fee - position["entry_gross"] - position["entry_fee"]
        cash += gross - fee
        realized += net_trade
        fees_paid += fee
        closed_trades += 1
        total_trade_net = position["realized_net"] + net_trade
        if total_trade_net > ZERO:
            wins += 1
            gross_wins += total_trade_net
        else:
            losses += 1
            gross_losses += abs(total_trade_net)

    win_rate = (
        Decimal(wins) / Decimal(closed_trades) * Decimal("100")
        if closed_trades else ZERO
    )
    profit_factor = (
        gross_wins / gross_losses if gross_losses > ZERO
        else (None if gross_wins == ZERO else Decimal("999999"))
    )
    return BacktestMetrics(
        start, end, cash,
        (cash / Decimal(initial_cash) - Decimal("1")) * Decimal("100"),
        max_drawdown, closed_trades, wins, losses, win_rate,
        profit_factor, fees_paid, shield_blocks, buy_candidates,
        blocked_buy_candidates, entry_orders, compounding_reinvested,
    )


def run_backtest(
    candles: Sequence[HistoricalCandle],
    *,
    warmup_bars: int = 60,
    initial_cash: Decimal = INITIAL_CASH,
) -> BacktestMetrics:
    """Run one strictly out-of-sample test segment after an indicator warm-up."""
    if warmup_bars < 30 or len(candles) <= warmup_bars:
        raise ValueError("At least 30 warm-up bars and one test bar are required.")
    _validate_series(candles)
    return _evaluate_fold(candles, warmup_bars, len(candles), initial_cash=initial_cash)


def walk_forward(
    candles: Sequence[HistoricalCandle],
    *,
    warmup_bars: int = 240,
    test_bars: int = 120,
    step_bars: int = 120,
    initial_cash: Decimal = INITIAL_CASH,
) -> WalkForwardReport:
    """Evaluate consecutive unseen windows using only earlier bars as warm-up.

    This is a rolling out-of-sample evaluation of a fixed rule-based strategy;
    it does not train an ML model or tune parameters on the test windows.
    """
    if warmup_bars < 30 or test_bars <= 0 or step_bars <= 0:
        raise ValueError("Invalid walk-forward window sizes.")
    _validate_series(candles)
    folds: list[BacktestMetrics] = []
    start = warmup_bars
    while start + test_bars <= len(candles):
        folds.append(
            _evaluate_fold(
                candles, start, start + test_bars, initial_cash=initial_cash
            )
        )
        start += step_bars
    if not folds:
        raise ValueError("Not enough candles for one complete walk-forward fold.")
    return WalkForwardReport(warmup_bars, test_bars, step_bars, tuple(folds))


def _validate_series(candles: Sequence[HistoricalCandle]) -> None:
    if len(candles) < 31:
        raise ValueError("At least 31 historical candles are required.")
    previous_timestamp = 0
    for candle in candles:
        candle.validate()
        if candle.timestamp_ms <= previous_timestamp:
            raise ValueError("Historical candles must be strictly chronological.")
        previous_timestamp = candle.timestamp_ms
