from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

from mumin_trader.exchanges.binance.websocket_client import BinanceWebSocketClient

logger = logging.getLogger(__name__)

WATCH_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
)


class MarketStreamService:
    """One server-owned public Binance stream shared by every dashboard client."""

    def __init__(self) -> None:
        streams = [f"{symbol.lower()}@ticker" for symbol in WATCH_SYMBOLS]
        self._client = BinanceWebSocketClient(streams=streams)
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._assets: dict[str, dict[str, Any]] = {}
        self._last_received_at: datetime | None = None
        self._connection_state = "connecting"
        self._reconnect_count = 0
        self._last_error: str | None = None
        self._stop_requested = False

    async def start(self) -> None:
        async with self._lock:
            if self._task is None or self._task.done():
                self._stop_requested = False
                self._connection_state = "connecting"
                self._task = asyncio.create_task(
                    self._consume(), name="mumin-binance-public-market-stream"
                )

    async def close(self) -> None:
        self._stop_requested = True
        self._connection_state = "stopped"
        self._client.stop()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            age = (
                (now - self._last_received_at).total_seconds()
                if self._last_received_at is not None
                else None
            )
            connected = bool(self._client.connected)
            status = (
                "live" if connected and age is not None and age <= 30
                else "stale" if self._assets
                else "connecting" if connected or self._connection_state == "connecting"
                else "disconnected"
            )
            assets = [dict(self._assets[symbol]) for symbol in WATCH_SYMBOLS if symbol in self._assets]
            return {
                "status": status,
                "source": "BINANCE_PUBLIC_SERVER_STREAM",
                "updated_at": self._last_received_at.isoformat() if self._last_received_at else None,
                "age_seconds": round(age, 3) if age is not None else None,
                "connection_state": "connected" if connected else self._connection_state,
                "reconnect_count": max(self._reconnect_count, self._client.reconnect_count),
                "last_error": self._client.last_error or self._last_error,
                "assets": assets,
            }

    async def _consume(self) -> None:
        delay = 1.0
        while not self._stop_requested:
            try:
                self._connection_state = "connecting"
                async for event in self._client.events():
                    if self._stop_requested:
                        return
                    payload = event.get("data") if isinstance(event, dict) else None
                    records = payload if isinstance(payload, list) else [event]
                    received = datetime.now(timezone.utc)
                    updates: dict[str, dict[str, Any]] = {}
                    for item in records:
                        if not isinstance(item, dict):
                            continue
                        symbol = str(item.get("s", "")).upper()
                        if symbol not in WATCH_SYMBOLS:
                            continue
                        try:
                            price = float(item["c"])
                            change = float(item["P"])
                            event_time = int(item.get("E", 0))
                        except (KeyError, TypeError, ValueError):
                            continue
                        if not math.isfinite(price) or price <= 0 or not math.isfinite(change):
                            continue
                        updates[symbol] = {
                            "symbol": symbol, "price": price, "change": change,
                            "event_time_ms": event_time, "received_at": received.isoformat(),
                        }
                    if updates:
                        async with self._lock:
                            self._assets.update(updates)
                            self._last_received_at = received
                            self._connection_state = "connected"
                            self._last_error = None
                        delay = 1.0
                if not self._stop_requested:
                    self._connection_state = "disconnected"
                    self._reconnect_count += 1
                    self._last_error = "Market stream ended unexpectedly."
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop_requested:
                    return
                logger.exception("Server-side Binance market stream failed")
                self._connection_state = "disconnected"
                self._reconnect_count += 1
                self._last_error = type(exc).__name__
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
