from decimal import Decimal

import httpx
import pytest

from mumin_trader.exchanges.binance.public_market_data import (
    ALLOWED_SYMBOLS,
    BASE_URL,
    PublicMarketData,
    simulated_fill_price,
)


@pytest.mark.asyncio
async def test_public_market_data_uses_unauthenticated_endpoints_and_closed_candle():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/ticker/bookTicker"):
            return httpx.Response(200, json={
                "symbol": "ETHUSDT", "bidPrice": "2999.9", "askPrice": "3000.1"
            })
        if request.url.path.endswith("/klines"):
            return httpx.Response(200, json=[
                [1, "2990", "3010", "2980", "2995", "10", 1_699_999_999_000],
                [2, "2995", "3020", "2990", "3010", "12", 1_700_000_001_000],
            ])
        if request.url.path.endswith("/time"):
            return httpx.Response(200, json={"serverTime": 1_700_000_000_000})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    feed = PublicMarketData(client=client)
    snapshot = await feed.snapshot("ETHUSDT")

    assert snapshot.symbol == "ETHUSDT"
    assert snapshot.bid == Decimal("2999.9")
    assert snapshot.ask == Decimal("3000.1")
    assert snapshot.last_closed_price == Decimal("2995")
    assert snapshot.candle_close_time_ms == 1_699_999_999_000
    assert snapshot.source == "BINANCE_PUBLIC_MARKET_DATA"
    assert len(calls) == 5
    assert {request.url.params.get('interval') for request in calls if request.url.path.endswith('/klines')} == {'1m', '5m', '15m'}
    assert all("X-MBX-APIKEY" not in request.headers for request in calls)
    await feed.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_public_market_data_rejects_symbols_outside_allowlist():
    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(
        lambda request: httpx.Response(500)
    ))
    feed = PublicMarketData(client=client)
    with pytest.raises(ValueError, match="allowlist"):
        await feed.snapshot("NOTREALUSDT")
    await feed.close()
    await client.aclose()


def test_paper_fill_uses_ask_for_buy_and_bid_for_sell_with_slippage():
    bid = Decimal("99")
    ask = Decimal("101")
    assert simulated_fill_price("BUY", bid, ask) == Decimal("101.0202")
    assert simulated_fill_price("SELL", bid, ask) == Decimal("98.9802")
    with pytest.raises(ValueError):
        simulated_fill_price("HOLD", bid, ask)


def test_allowlist_contains_only_the_three_approved_spot_pairs():
    assert ALLOWED_SYMBOLS == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}


@pytest.mark.asyncio
async def test_public_market_data_calculates_volume_and_trade_count_anomaly_ratios():
    server_time = 1_800_000_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker/bookTicker"):
            return httpx.Response(200, json={
                "symbol": "BTCUSDT", "bidPrice": "60000", "askPrice": "60001"
            })
        if request.url.path.endswith("/klines"):
            rows = []
            for index in range(21):
                close_time = server_time - 21_000 + index * 1_000
                quote_volume = "200" if index == 20 else "100"
                trade_count = "20" if index == 20 else "10"
                rows.append([
                    index, "60000", "60100", "59900", "60000",
                    "1", close_time, quote_volume, trade_count,
                ])
            return httpx.Response(200, json=rows)
        if request.url.path.endswith("/time"):
            return httpx.Response(200, json={"serverTime": server_time})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    feed = PublicMarketData(client=client)
    snapshot = await feed.snapshot("BTCUSDT")

    assert snapshot.quote_volume_ratio == Decimal("2")
    assert snapshot.trade_count_ratio == Decimal("2")
    await feed.close()
    await client.aclose()
