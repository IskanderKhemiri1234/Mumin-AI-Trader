from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

import mumin_trader.main as main_module
from mumin_trader.exchanges.binance.account_readonly import (
    BINANCE_SPOT_READONLY_BASE_URL,
    BinanceReadOnlyAccountError,
    read_binance_spot_account,
)
from mumin_trader.main import app


@pytest.mark.asyncio
async def test_binance_readonly_account_is_pinned_and_uses_only_get_requests():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert str(request.url).startswith(BINANCE_SPOT_READONLY_BASE_URL)
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        if request.url.path == "/api/v3/account":
            query = parse_qs(request.url.query.decode())
            assert query["timestamp"] == ["1700000000000"]
            assert query["recvWindow"] == ["5000"]
            assert len(query["signature"][0]) == 64
            assert request.headers["X-MBX-APIKEY"] == "read-key"
            return httpx.Response(200, json={
                "accountType": "SPOT",
                "canTrade": True,
                "balances": [
                    {"asset": "USDT", "free": "70.00", "locked": "0"},
                    {"asset": "BTC", "free": "0", "locked": "0"},
                ],
            })
        return httpx.Response(404)

    result = await read_binance_spot_account(
        "read-key", "read-secret", transport=httpx.MockTransport(handler)
    )

    assert result["status"] == "connected"
    assert result["account_type"] == "SPOT"
    assert result["balances"] == [{"asset": "USDT", "free": "70.00", "locked": "0"}]
    assert result["order_submission"] == "NOT_IMPLEMENTED"
    assert result["withdrawals"] == "NOT_IMPLEMENTED"
    assert [request.method for request in seen] == ["GET", "GET"]
    assert all(request.url.path in {"/api/v3/time", "/api/v3/account"} for request in seen)


@pytest.mark.asyncio
async def test_binance_readonly_account_rejects_non_spot_account():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v3/time":
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        return httpx.Response(200, json={"accountType": "MARGIN", "balances": []})

    with pytest.raises(BinanceReadOnlyAccountError, match="SPOT"):
        await read_binance_spot_account(
            "read-key", "read-secret", transport=httpx.MockTransport(handler)
        )


def test_binance_readonly_endpoint_requires_dashboard_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    response = TestClient(app).get("/api/binance/account/read-only")
    assert response.status_code == 503


def test_binance_readonly_endpoint_requires_server_side_credentials(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.delenv("BINANCE_ACCOUNT_READONLY_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_ACCOUNT_READONLY_API_SECRET", raising=False)
    response = TestClient(app).get(
        "/api/binance/account/read-only",
        headers={"X-Dashboard-Password": "server-only-password"},
    )
    assert response.status_code == 409


def test_binance_readonly_endpoint_returns_sanitized_account_snapshot(monkeypatch):
    from fastapi.testclient import TestClient

    async def fake_read(api_key: str, api_secret: str):
        assert api_key == "server-key"
        assert api_secret == "server-secret"
        return {"status": "connected", "account_type": "SPOT", "balances": []}

    monkeypatch.setenv("DASHBOARD_PASSWORD", "server-only-password")
    monkeypatch.setenv("BINANCE_ACCOUNT_READONLY_API_KEY", "server-key")
    monkeypatch.setenv("BINANCE_ACCOUNT_READONLY_API_SECRET", "server-secret")
    monkeypatch.setattr(main_module, "read_binance_spot_account", fake_read)

    response = TestClient(app).get(
        "/api/binance/account/read-only",
        headers={"X-Dashboard-Password": "server-only-password"},
    )
    assert response.status_code == 200
    assert response.json()["account_type"] == "SPOT"
    assert "server-secret" not in response.text
