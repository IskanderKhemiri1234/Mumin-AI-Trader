from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
from config.config import Settings


class BinanceAPIError(RuntimeError):
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"Binance API error {status_code}: {payload}")


class BinanceSpotRESTClient:
    """Minimal async Spot REST client: public data and signed read-only endpoints only."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.rest_base_url,
            timeout=settings.BINANCE_TIMEOUT_SECONDS,
            headers={"X-MBX-APIKEY": settings.BINANCE_API_KEY.get_secret_value()},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None, signed: bool = False) -> Any:
        query = dict(params or {})
        if signed:
            secret = self.settings.BINANCE_API_SECRET.get_secret_value()
            api_key = self.settings.BINANCE_API_KEY.get_secret_value()
            if not api_key or not secret:
                raise RuntimeError("Signed Binance request requires API credentials.")
            query["timestamp"] = int(time.time() * 1000)
            query["recvWindow"] = self.settings.BINANCE_RECV_WINDOW_MS
            encoded = urlencode(query, doseq=True)
            query["signature"] = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        response = await self._client.request(method, path, params=query)
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text[:500]}
        if response.is_error:
            raise BinanceAPIError(response.status_code, payload)
        return payload

    async def ping(self) -> bool:
        await self._request("GET", "/api/v3/ping")
        return True

    async def server_time(self) -> int:
        return int((await self._request("GET", "/api/v3/time"))["serverTime"])

    async def exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol.upper()} if symbol else None)

    async def ticker_24h(self, symbol: str | None = None) -> Any:
        return await self._request("GET", "/api/v3/ticker/24hr", {"symbol": symbol.upper()} if symbol else None)

    async def klines(self, symbol: str, interval: str = "5m", limit: int = 500) -> list[list[Any]]:
        return await self._request("GET", "/api/v3/klines", {"symbol": symbol.upper(), "interval": interval, "limit": limit})

    async def account(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/account", signed=True)

    async def account_commission(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", "/api/v3/account/commission", {"symbol": symbol.upper()}, signed=True)

    @staticmethod
    def decimal(value: Any) -> Decimal:
        return Decimal(str(value))
