from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx

# Deliberately pinned to Binance production Spot REST. This module exposes only
# signed account reads; it has no order, transfer, margin, futures, or withdrawal API.
BINANCE_SPOT_READONLY_BASE_URL = "https://api.binance.com"


class BinanceReadOnlyAccountError(RuntimeError):
    pass


async def read_binance_spot_account(
    api_key: str,
    api_secret: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    if not api_key or not api_secret:
        raise BinanceReadOnlyAccountError("Read-only Binance account credentials are not configured.")

    timeout = httpx.Timeout(8.0, connect=5.0)
    async with httpx.AsyncClient(
        base_url=BINANCE_SPOT_READONLY_BASE_URL,
        timeout=timeout,
        headers={"X-MBX-APIKEY": api_key, "Accept": "application/json"},
        transport=transport,
    ) as client:
        try:
            clock_response = await client.get("/api/v3/time")
            clock_response.raise_for_status()
            server_time = int(clock_response.json()["serverTime"])

            params = {"timestamp": str(server_time), "recvWindow": "5000"}
            query = urlencode(params)
            signature = hmac.new(
                api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            account_response = await client.get(
                "/api/v3/account", params={**params, "signature": signature}
            )
            account_response.raise_for_status()
            account = account_response.json()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Do not propagate request URLs, signatures, headers, or credential material.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                reason = "Binance rejected the credentials or signature."
            elif status == 403:
                reason = "Binance denied this account request from the current network."
            elif status == 429:
                reason = "Binance rate limit reached; retry later."
            else:
                reason = "Binance account could not be verified."
            raise BinanceReadOnlyAccountError(reason) from None

    if not isinstance(account, dict) or not isinstance(account.get("balances"), list):
        raise BinanceReadOnlyAccountError("Binance returned an incomplete Spot account response.")
    if account.get("accountType") != "SPOT":
        raise BinanceReadOnlyAccountError("The connected account did not explicitly identify as SPOT.")

    balances: list[dict[str, str]] = []
    for item in account["balances"]:
        if not isinstance(item, dict):
            raise BinanceReadOnlyAccountError("Binance returned a malformed balance row.")
        try:
            free = Decimal(str(item.get("free", "0")))
            locked = Decimal(str(item.get("locked", "0")))
        except (InvalidOperation, TypeError, ValueError):
            raise BinanceReadOnlyAccountError("Binance returned a non-numeric balance.") from None
        if not free.is_finite() or not locked.is_finite() or free < 0 or locked < 0:
            raise BinanceReadOnlyAccountError("Binance returned an invalid balance.")
        if free or locked:
            asset = str(item.get("asset", "")).upper()
            if not asset or len(asset) > 20:
                raise BinanceReadOnlyAccountError("Binance returned an invalid asset identifier.")
            balances.append({"asset": asset, "free": str(free), "locked": str(locked)})

    return {
        "status": "connected",
        "exchange": "BINANCE",
        "account_type": "SPOT",
        "can_trade_account_flag": bool(account.get("canTrade", False)),
        "balances": balances,
        "balance_assets": len(balances),
        "checked_at_unix": int(time.time()),
        "access_scope_warning": (
            "This endpoint performs account reads only. Binance does not expose the API-key "
            "permission scope in this response; configure the key as read-only and disable "
            "trading and withdrawals in Binance API Management."
        ),
        "order_submission": "NOT_IMPLEMENTED",
        "withdrawals": "NOT_IMPLEMENTED",
        "transfers": "NOT_IMPLEMENTED",
        "margin_and_futures": "NOT_IMPLEMENTED",
    }
