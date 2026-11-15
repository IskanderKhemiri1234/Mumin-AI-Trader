"""Deterministic Paper-only micro-alpha research agents.

Signals use closed one-minute OHLCV candles and public top-of-book depth.
A wick sweep is a price-action proxy, not proof of hidden stop orders or executed flow.
No exchange order methods, credentials, model training, or live-trading path exist here.
"""
from __future__ import annotations

from decimal import Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TEN_THOUSAND = Decimal("10000")


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values)) if values else ZERO


def atr_percent(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], period: int = 14) -> Decimal:
    if period <= 0 or len(closes) < period + 1 or len(highs) != len(closes) or len(lows) != len(closes):
        return ZERO
    ranges = []
    for index in range(len(closes) - period, len(closes)):
        previous = closes[index - 1]
        ranges.append(max(highs[index] - lows[index], abs(highs[index] - previous), abs(lows[index] - previous)))
    return _mean(ranges) / closes[-1] * HUNDRED if closes[-1] > 0 else ZERO


def atr_adaptive_targets(atr_pct: Decimal, *, max_stop_pct: Decimal = Decimal("0.50"), max_target_pct: Decimal = Decimal("1.25")) -> tuple[Decimal, Decimal]:
    atr_pct = max(Decimal("0"), Decimal(atr_pct))
    stop = min(max_stop_pct, max(Decimal("0.20"), atr_pct * Decimal("0.80")))
    target = min(max_target_pct, max(Decimal("0.40"), atr_pct * Decimal("1.80")))
    return stop, target


class LiquiditySweepAgent:
    name = "liquidity_sweep_proxy"

    def evaluate(self, highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], volumes: list[Decimal]) -> str:
        if min(len(highs), len(lows), len(closes), len(volumes)) < 9:
            return "HOLD"
        previous_low, previous_high = min(lows[-9:-1]), max(highs[-9:-1])
        candle_range = highs[-1] - lows[-1]
        average_volume = _mean(volumes[-9:-1])
        if candle_range <= 0 or volumes[-1] <= 0 or average_volume <= 0:
            return "HOLD"
        if volumes[-1] < average_volume * Decimal("1.20"):
            return "HOLD"
        close_location = (closes[-1] - lows[-1]) / candle_range
        if lows[-1] < previous_low and closes[-1] > previous_low and close_location >= Decimal("0.65"):
            return "BUY"
        if highs[-1] > previous_high and closes[-1] < previous_high and close_location <= Decimal("0.35"):
            return "SELL"
        return "HOLD"


class OrderBookImbalanceAgent:
    name = "depth_imbalance"

    def evaluate(self, bid_depth: Decimal, ask_depth: Decimal) -> str:
        bid_depth, ask_depth = Decimal(bid_depth), Decimal(ask_depth)
        total = bid_depth + ask_depth
        if bid_depth <= 0 or ask_depth <= 0 or total <= 0:
            return "HOLD"
        imbalance = (bid_depth - ask_depth) / total
        if imbalance >= Decimal("0.12"):
            return "BUY"
        if imbalance <= Decimal("-0.12"):
            return "SELL"
        return "HOLD"


class MicroAlphaFilterAgent:
    name = "micro_alpha_filter"

    def evaluate(self, sweep_vote: str, depth_vote: str, bid: Decimal, ask: Decimal, max_spread_bps: Decimal, atr_pct_value: Decimal) -> str:
        bid, ask = Decimal(bid), Decimal(ask)
        if bid <= 0 or ask < bid:
            return "HOLD"
        midpoint = (bid + ask) / Decimal("2")
        if midpoint <= 0 or (ask - bid) / midpoint * TEN_THOUSAND > max_spread_bps:
            return "HOLD"
        if not Decimal("0.02") <= atr_pct_value <= Decimal("2.00"):
            return "HOLD"
        return sweep_vote if sweep_vote == depth_vote and sweep_vote in {"BUY", "SELL"} else "HOLD"


class PaperMicroAlphaSwarm:
    """Three-agent consensus: wick-sweep proxy, order-book depth, and risk filter."""

    def __init__(self) -> None:
        self.sweep = LiquiditySweepAgent()
        self.depth = OrderBookImbalanceAgent()
        self.filter = MicroAlphaFilterAgent()

    def evaluate_detailed(
        self, *, closes: list[Decimal], highs: list[Decimal], lows: list[Decimal],
        volumes: list[Decimal], bid_depth: Decimal, ask_depth: Decimal,
        bid: Decimal, ask: Decimal, max_spread_bps: Decimal,
    ) -> dict[str, Decimal | str]:
        atr = atr_percent(highs, lows, closes)
        stop, target = atr_adaptive_targets(atr)
        bid, ask = Decimal(bid), Decimal(ask)
        midpoint = (bid + ask) / Decimal("2") if bid > 0 and ask >= bid else ZERO
        spread_bps = ((ask - bid) / midpoint * TEN_THOUSAND) if midpoint > 0 else ZERO
        if len(closes) < 15:
            return {
                "signal": "HOLD", "atr_pct": atr, "stop_pct": stop, "target_pct": target,
                "sweep_vote": "HOLD", "sweep_reason": "WARMUP_REQUIRES_15_CLOSED_CANDLES",
                "depth_vote": "HOLD", "depth_reason": "NOT_EVALUATED_DURING_WARMUP",
                "filter_vote": "HOLD", "filter_reason": "WARMUP",
                "spread_bps": spread_bps, "bid_depth": Decimal(bid_depth),
                "ask_depth": Decimal(ask_depth),
            }

        sweep_vote = self.sweep.evaluate(highs, lows, closes, volumes)
        average_volume = _mean(volumes[-9:-1])
        candle_range = highs[-1] - lows[-1]
        if candle_range <= 0:
            sweep_reason = "INVALID_CANDLE_RANGE"
        elif volumes[-1] <= 0 or average_volume <= 0:
            sweep_reason = "INVALID_VOLUME"
        elif volumes[-1] < average_volume * Decimal("1.20"):
            sweep_reason = "VOLUME_BELOW_1_20X_AVERAGE"
        elif sweep_vote == "BUY":
            sweep_reason = "LOW_SWEPT_AND_RECLAIMED_WITH_STRONG_CLOSE"
        elif sweep_vote == "SELL":
            sweep_reason = "HIGH_SWEPT_AND_REJECTED_WITH_WEAK_CLOSE"
        else:
            sweep_reason = "NO_QUALIFYING_WICK_SWEEP"

        bid_depth, ask_depth = Decimal(bid_depth), Decimal(ask_depth)
        total_depth = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else ZERO
        depth_vote = self.depth.evaluate(bid_depth, ask_depth)
        if bid_depth <= 0 or ask_depth <= 0:
            depth_reason = "MISSING_OR_ZERO_DEPTH_SIDE"
        elif depth_vote == "BUY":
            depth_reason = "BID_DEPTH_IMBALANCE_AT_LEAST_12_PERCENT"
        elif depth_vote == "SELL":
            depth_reason = "ASK_DEPTH_IMBALANCE_AT_LEAST_12_PERCENT"
        else:
            depth_reason = "DEPTH_IMBALANCE_WITHIN_NEUTRAL_BAND"

        if bid <= 0 or ask < bid:
            filter_reason = "INVALID_BID_ASK"
            signal = "HOLD"
        elif spread_bps > max_spread_bps:
            filter_reason = "SPREAD_ABOVE_LIMIT"
            signal = "HOLD"
        elif not Decimal("0.02") <= atr <= Decimal("2.00"):
            filter_reason = "ATR_OUTSIDE_0_02_TO_2_00_PERCENT"
            signal = "HOLD"
        elif sweep_vote != depth_vote or sweep_vote not in {"BUY", "SELL"}:
            filter_reason = "AGENT_DISAGREEMENT_OR_NO_DIRECTIONAL_VOTE"
            signal = "HOLD"
        else:
            filter_reason = "SWEEP_AND_DEPTH_CONSENSUS_PASSED"
            signal = sweep_vote

        return {
            "signal": signal, "atr_pct": atr, "stop_pct": stop, "target_pct": target,
            "sweep_vote": sweep_vote, "sweep_reason": sweep_reason,
            "depth_vote": depth_vote, "depth_reason": depth_reason,
            "filter_vote": signal, "filter_reason": filter_reason,
            "spread_bps": spread_bps, "bid_depth": bid_depth,
            "ask_depth": ask_depth, "depth_imbalance": imbalance,
        }

    def evaluate(self, *, closes: list[Decimal], highs: list[Decimal], lows: list[Decimal], volumes: list[Decimal], bid_depth: Decimal, ask_depth: Decimal, bid: Decimal, ask: Decimal, max_spread_bps: Decimal) -> tuple[str, Decimal, Decimal, Decimal, str]:
        details = self.evaluate_detailed(
            closes=closes, highs=highs, lows=lows, volumes=volumes,
            bid_depth=bid_depth, ask_depth=ask_depth, bid=bid, ask=ask,
            max_spread_bps=max_spread_bps,
        )
        reason = (
            "SWEEP_DEPTH_CONSENSUS"
            if details["signal"] != "HOLD"
            else f"NO_CONSENSUS:{details['sweep_vote']}/{details['depth_vote']}:{details['filter_reason']}"
        )
        return (
            str(details["signal"]), Decimal(details["atr_pct"]),
            Decimal(details["stop_pct"]), Decimal(details["target_pct"]), reason,
        )
