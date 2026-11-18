from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from mumin_trader.ai_engine.strategy_engine import CORE_SYMBOLS, StrategyEngine
from mumin_trader.advanced_filters import (
    evaluate_anti_chase_and_breakout,
    evaluate_liquidity,
    evaluate_market_regime,
    evaluate_momentum,
    evaluate_mtf_trend,
    evaluate_net_edge,
)
from mumin_trader.exchanges.binance.rest_client import BinanceSpotRESTClient

ZERO = Decimal("0")
BPS = Decimal("10000")
TIMEFRAMES = ("5m", "15m", "1h", "4h")


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class MarketCandidate:
    symbol: str
    bid: Decimal
    ask: Decimal
    spread_bps: Decimal
    quote_volume: Decimal
    change_percent: Decimal
    last_price: Decimal = ZERO
    signal: str = "HOLD"
    rsi: Decimal | None = None
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    expected_move_bps: Decimal = ZERO
    strategy_score: int = 0
    stop_loss_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    net_edge_bps: Decimal = ZERO
    filter_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ClosedOHLCV:
    closes: list[Decimal]
    highs: list[Decimal]
    lows: list[Decimal]
    volumes: list[Decimal]


def _closed_ohlcv(candles: Sequence[Sequence[Any]]) -> _ClosedOHLCV:
    """Parse Binance klines and deliberately exclude the still-forming last candle."""
    parsed: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    for candle in candles[:-1]:
        if len(candle) <= 5:
            continue
        values = tuple(_decimal(candle[index]) for index in (2, 3, 4, 5))
        if any(value is None or value <= ZERO for value in values):
            continue
        high, low, close, volume = values
        if low > high or not low <= close <= high:
            continue
        parsed.append((high, low, close, volume))
    return _ClosedOHLCV(
        closes=[item[2] for item in parsed],
        highs=[item[0] for item in parsed],
        lows=[item[1] for item in parsed],
        volumes=[item[3] * item[2] for item in parsed],
    )


def _ema(values: list[Decimal], period: int) -> Decimal:
    if len(values) < period:
        raise ValueError("Not enough candles for EMA.")
    multiplier = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], ZERO) / Decimal(period)
    for value in values[period:]:
        result = (value - result) * multiplier + result
    return result


def _rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, ZERO) for change in changes[-period:]]
    losses = [max(-change, ZERO) for change in changes[-period:]]
    average_gain = sum(gains, ZERO) / Decimal(period)
    average_loss = sum(losses, ZERO) / Decimal(period)
    if average_loss == ZERO:
        return Decimal("100") if average_gain > ZERO else Decimal("50")
    relative_strength = average_gain / average_loss
    return Decimal("100") - (Decimal("100") / (Decimal(1) + relative_strength))


def screen_tickers(
    tickers: list[dict[str, Any]],
    quote_asset: str = "USDT",
    min_quote_volume: Decimal = ZERO,
) -> list[dict[str, Any]]:
    """Liquidity-only filter; this function does not create a trade signal."""
    quote_asset = quote_asset.upper()
    selected = []
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        volume = _decimal(item.get("quoteVolume", "0"))
        if (
            symbol.endswith(quote_asset)
            and volume is not None
            and volume >= min_quote_volume
        ):
            selected.append(item)
    return sorted(
        selected,
        key=lambda row: _decimal(row.get("quoteVolume", 0)) or ZERO,
        reverse=True,
    )


class MarketScanner:
    """Eight-asset Spot scanner; every BUY candidate must pass all advanced gates."""

    def __init__(
        self,
        quote_asset: str = "USDT",
        min_quote_volume: Decimal = Decimal("1000000"),
        max_spread_bps: Decimal = Decimal("12"),
        allowed_symbols: set[str] | None = None,
        interval: str = "5m",
        candle_limit: int = 100,
        max_symbols: int = 8,
        min_expected_move_bps: Decimal = Decimal("35"),
    ) -> None:
        self.quote_asset = quote_asset.upper()
        self.min_quote_volume = Decimal(min_quote_volume)
        self.max_spread_bps = Decimal(max_spread_bps)
        requested = {s.upper() for s in allowed_symbols} if allowed_symbols else set(CORE_SYMBOLS)
        self.allowed_symbols = requested.intersection(CORE_SYMBOLS)
        self.interval = interval
        self.candle_limit = max(60, min(int(candle_limit), 500))
        self.max_symbols = max(1, min(int(max_symbols), len(CORE_SYMBOLS)))
        self.min_expected_move_bps = Decimal(min_expected_move_bps)
        self.strategy_engine = StrategyEngine()
        self._book: dict[str, tuple[Decimal, Decimal]] = {}
        self._volume: dict[str, tuple[Decimal, Decimal]] = {}

    def ingest(self, event: dict[str, Any]) -> MarketCandidate | None:
        """Accept ticker/book events for display only; stream events cannot approve trades."""
        symbol = str(event.get("s", event.get("symbol", ""))).upper()
        if symbol not in self.allowed_symbols:
            return None
        volume = _decimal(event.get("q", event.get("quoteVolume")))
        change = _decimal(event.get("P", event.get("priceChangePercent", "0")))
        bid = _decimal(event.get("b", event.get("bidPrice")))
        ask = _decimal(event.get("a", event.get("askPrice")))
        if volume is not None:
            self._volume[symbol] = (volume, change or ZERO)
        if bid is not None and ask is not None and bid > ZERO and ask >= bid:
            self._book[symbol] = (bid, ask)
        if symbol not in self._volume or symbol not in self._book:
            return None
        quote_volume, change_percent = self._volume[symbol]
        bid, ask = self._book[symbol]
        if quote_volume < self.min_quote_volume:
            return None
        spread = ((ask - bid) / ((ask + bid) / Decimal(2))) * BPS
        if spread > self.max_spread_bps:
            return None
        return MarketCandidate(
            symbol=symbol,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            quote_volume=quote_volume,
            change_percent=change_percent,
            last_price=(bid + ask) / Decimal(2),
        )

    async def scan(self, client: BinanceSpotRESTClient) -> list[MarketCandidate]:
        """Evaluate each core symbol independently using closed candles on four frames."""
        exchange_info = await client.exchange_info()
        tickers = await client.ticker_24h()
        if not isinstance(tickers, list):
            raise RuntimeError("Binance 24h ticker endpoint returned an unexpected payload.")

        trading_symbols = {
            str(item.get("symbol", "")).upper()
            for item in exchange_info.get("symbols", [])
            if item.get("status") == "TRADING"
            and item.get("isSpotTradingAllowed", True)
            and item.get("quoteAsset", "").upper() == self.quote_asset
        }
        rows = [
            row for row in tickers
            if str(row.get("symbol", "")).upper() in trading_symbols
            and str(row.get("symbol", "")).upper() in self.allowed_symbols
        ]
        rows = screen_tickers(rows, self.quote_asset, self.min_quote_volume)[: self.max_symbols]
        candidates: list[MarketCandidate] = []

        for row in rows:
            symbol = str(row["symbol"]).upper()
            bid = _decimal(row.get("bidPrice"))
            ask = _decimal(row.get("askPrice"))
            last = _decimal(row.get("lastPrice"))
            volume = _decimal(row.get("quoteVolume"))
            change = _decimal(row.get("priceChangePercent", "0")) or ZERO
            if bid is None or ask is None or last is None or volume is None:
                continue
            if bid <= ZERO or ask < bid or last <= ZERO:
                continue
            spread = ((ask - bid) / ((ask + bid) / Decimal(2))) * BPS
            if spread > self.max_spread_bps:
                continue

            frame_data: dict[str, _ClosedOHLCV] = {}
            for timeframe in TIMEFRAMES:
                raw = await client.klines(symbol, interval=timeframe, limit=self.candle_limit)
                frame_data[timeframe] = _closed_ohlcv(raw)

            base = frame_data["5m"]
            if min(len(base.closes), len(base.highs), len(base.lows), len(base.volumes)) < 30:
                continue

            trend = evaluate_mtf_trend({
                timeframe: frame_data[timeframe].closes for timeframe in TIMEFRAMES
            })
            momentum = evaluate_momentum(
                base.closes, base.volumes, lookback=10
            )
            liquidity = evaluate_liquidity(
                bid=bid,
                ask=ask,
                quote_volume_24h=volume,
                minimum_quote_volume=self.min_quote_volume,
                maximum_spread_bps=min(self.max_spread_bps, Decimal("8")),
            )
            price_action = evaluate_anti_chase_and_breakout(
                closes=base.closes,
                highs=base.highs,
                lows=base.lows,
                volumes=base.volumes,
            )
            regime = evaluate_market_regime(
                highs=base.highs, lows=base.lows, closes=base.closes
            )
            net_edge = evaluate_net_edge(
                target_pct=self.strategy_engine.take_profit_percent,
                spread_bps=spread,
            )
            advanced_gates = (trend, momentum, liquidity, price_action, regime, net_edge)
            filter_reasons = tuple(gate.reason for gate in advanced_gates if not gate.passed)

            fast = _ema(base.closes, 9)
            slow = _ema(base.closes, 21)
            rsi = _rsi(base.closes, 14)
            momentum_bps = (
                (base.closes[-1] / base.closes[-11] - Decimal(1)) * BPS
                if len(base.closes) >= 11 else ZERO
            )
            closes_15m = frame_data["15m"].closes
            closes_1h = frame_data["1h"].closes
            decision = self.strategy_engine.evaluate(
                symbol=symbol,
                entry_price=last,
                spread_bps=spread,
                quote_volume=volume,
                rsi=rsi,
                momentum_bps=momentum_bps,
                timeframe_trends={
                    "fast": fast > slow,
                    "15m": len(closes_15m) >= 21 and _ema(closes_15m, 9) > _ema(closes_15m, 21),
                    "1h": len(closes_1h) >= 21 and _ema(closes_1h, 9) > _ema(closes_1h, 21),
                },
                advanced_filter_reasons=filter_reasons,
            )
            gate_score = sum(gate.score for gate in advanced_gates) // len(advanced_gates)
            combined_score = (decision.score + gate_score) // 2 if decision.approved else decision.score
            candidates.append(
                MarketCandidate(
                    symbol=symbol,
                    bid=bid,
                    ask=ask,
                    spread_bps=spread,
                    quote_volume=volume,
                    change_percent=change,
                    last_price=last,
                    signal="BUY_CANDIDATE" if decision.approved and not filter_reasons else "HOLD",
                    rsi=rsi,
                    ema_fast=fast,
                    ema_slow=slow,
                    expected_move_bps=max(momentum_bps, ZERO),
                    strategy_score=combined_score,
                    stop_loss_price=decision.stop_loss_price,
                    take_profit_price=decision.take_profit_price,
                    net_edge_bps=dict(net_edge.metrics).get("net_edge_bps", ZERO),
                    filter_reasons=filter_reasons or (() if decision.approved else (decision.reason,)),
                )
            )
        return self.rank(candidates)

    @staticmethod
    def rank(candidates: list[MarketCandidate]) -> list[MarketCandidate]:
        """Approved candidates rank first, then score, net edge, and market liquidity."""
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.signal == "BUY_CANDIDATE",
                candidate.strategy_score,
                candidate.net_edge_bps,
                candidate.quote_volume / (Decimal(1) + candidate.spread_bps),
                candidate.symbol,
            ),
            reverse=True,
        )
