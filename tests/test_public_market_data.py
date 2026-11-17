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
            if request.url.params.get("interval") == "1h":
                return httpx.Response(200, json=[
                    [index, "2990", "3010", "2980", "3000", "10", 1_699_999_999_000]
                    for index in range(21)
                ])
            return httpx.Response(200, json=[
                [1, "2990", "3010", "2980", "2995", "10", 1_699_999_999_000],
                [2, "2995", "3020", "2990", "3010", "12", 1_700_000_001_000],
            ])
        if request.url.path.endswith("/depth"):
            return httpx.Response(200, json={
                "bids": [["2999.9", "4.0"]], "asks": [["3000.1", "2.0"]]
            })
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
    assert snapshot.candle_open == Decimal("2990")
    assert snapshot.candle_high == Decimal("3010")
    assert snapshot.candle_low == Decimal("2980")
    assert snapshot.candle_volume == Decimal("10")
    assert snapshot.bid_depth_qty == Decimal("4.0")
    assert snapshot.ask_depth_qty == Decimal("2.0")
    assert len(snapshot.macro_hourly_closes) == 21
    assert snapshot.macro_hourly_closes[-1] == Decimal("3000")
    assert len(snapshot.macro_hourly_close_times_ms) == 21
    assert snapshot.macro_hourly_close_times_ms[-1] == 1_699_999_999_000
    assert snapshot.source == "BINANCE_PUBLIC_MARKET_DATA"
    assert len(calls) == 5
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


def test_allowlist_contains_all_eight_approved_spot_pairs():
    assert ALLOWED_SYMBOLS == {
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
        "XRPUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    }



@pytest.mark.asyncio
async def test_public_market_data_rejects_stale_one_minute_candle():
    server_time = 1_700_000_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ticker/bookTicker"):
            return httpx.Response(200, json={"symbol": "BTCUSDT", "bidPrice": "60000", "askPrice": "60001"})
        if path.endswith("/klines"):
            if request.url.params.get("interval") == "1h":
                return httpx.Response(200, json=[
                    [index, "60000", "60100", "59900", "60000", "10", server_time - 1000]
                    for index in range(21)
                ])
            return httpx.Response(200, json=[
                [1, "60000", "60100", "59900", "60000", "10", server_time - 181_000]
            ])
        if path.endswith("/depth"):
            return httpx.Response(200, json={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
        if path.endswith("/time"):
            return httpx.Response(200, json={"serverTime": server_time})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    feed = PublicMarketData(client=client)
    try:
        with pytest.raises(ValueError, match="stale"):
            await feed.snapshot("BTCUSDT")
    finally:
        await feed.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_public_market_data_rejects_stale_hourly_macro_candles():
    server_time = 1_700_000_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ticker/bookTicker"):
            return httpx.Response(200, json={"symbol": "BTCUSDT", "bidPrice": "60000", "askPrice": "60001"})
        if path.endswith("/klines"):
            if request.url.params.get("interval") == "1h":
                return httpx.Response(200, json=[
                    [index, "60000", "60100", "59900", "60000", "10", server_time - 7_201_000]
                    for index in range(21)
                ])
            return httpx.Response(200, json=[
                [1, "60000", "60100", "59900", "60000", "10", server_time - 1000]
            ])
        if path.endswith("/depth"):
            return httpx.Response(200, json={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
        if path.endswith("/time"):
            return httpx.Response(200, json={"serverTime": server_time})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    feed = PublicMarketData(client=client)
    try:
        with pytest.raises(ValueError, match="Hourly macro-trend data is stale"):
            await feed.snapshot("BTCUSDT")
    finally:
        await feed.close()
        await client.aclose()
