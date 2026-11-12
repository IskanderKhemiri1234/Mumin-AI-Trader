from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)


class BinanceWebSocketClient:
    """Resilient Binance Spot public market-stream consumer; no order capability."""

    def __init__(
        self,
        streams: list[str] | None = None,
        base_url: str = "wss://stream.binance.com:9443/stream",
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
        heartbeat_seconds: float = 20.0,
    ) -> None:
        self.streams = [s.strip().lower() for s in (streams or ["!ticker@arr"]) if s.strip()]
        if not self.streams:
            raise ValueError("At least one Binance stream is required")
        if reconnect_initial_seconds <= 0 or reconnect_max_seconds < reconnect_initial_seconds:
            raise ValueError("Invalid reconnect backoff configuration")
        self.base_url = base_url.rstrip("/")
        self.reconnect_initial_seconds = reconnect_initial_seconds
        self.reconnect_max_seconds = reconnect_max_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._stop = asyncio.Event()

    @property
    def url(self) -> str:
        return f"{self.base_url}?streams={'/'.join(self.streams)}"

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def decode_message(raw: str | bytes) -> dict[str, Any]:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Unexpected Binance WebSocket payload")
        if "data" in payload and isinstance(payload["data"], dict):
            return payload["data"]
        return payload

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        delay = self.reconnect_initial_seconds
        self._stop.clear()
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=self.heartbeat_seconds,
                    ping_timeout=self.heartbeat_seconds,
                    close_timeout=5,
                    max_queue=1000,
                ) as socket:
                    logger.info("Connected to Binance market stream")
                    delay = self.reconnect_initial_seconds
                    async for raw in socket:
                        if self._stop.is_set():
                            break
                        try:
                            yield self.decode_message(raw)
                        except (ValueError, json.JSONDecodeError):
                            logger.warning("Ignoring malformed Binance stream message")
            except asyncio.CancelledError:
                raise
            except (OSError, TimeoutError, ConnectionClosed, WebSocketException) as exc:
                if self._stop.is_set():
                    break
                logger.warning("Binance stream disconnected: %s", type(exc).__name__)
            if not self._stop.is_set():
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.reconnect_max_seconds)
