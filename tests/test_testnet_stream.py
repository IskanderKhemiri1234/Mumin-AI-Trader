from collections import deque
from decimal import Decimal
import time

import httpx
import pytest

from mumin_trader.exchanges.binance.testnet_stream import (
    REST_BASE_URL,
    SOURCE,
    BinanceSpotTestnetStream,
)


@pytest.mark.asyncio
async def test_testnet_rest_bootstrap_is_public_and_seeds_liquidity():
    calls = []
    server_time = 1_700_000_000_000

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/ticker/bookTicker"):
            return httpx.Response(200, json={
                "symbol": "BTCUSDT", "bidPrice": "99.9", "askPrice": "100.1"
            })
        if path.endswith("/depth"):
            return httpx.Response(200, json={
                "lastUpdateId": 123,
                "bids": [["99.9", "10"], ["99.8", "5"]],
                "asks": [["100.1", "2"], ["100.2", "1"]],
            })
        if path.endswith("/klines"):
            rows = [
                [i, "100", "101", "99", "100", "10", i + 1]
                for i in range(20)
            ]
            rows.append([21, "100", "101", "99", "100", "40", server_time - 1])
            return httpx.Response(200, json=rows)
        if path.endswith("/ticker/price"):
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "100"})
        if path.endswith("/time"):
            return httpx.Response(200, json={"serverTime": server_time})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=REST_BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    feed = BinanceSpotTestnetStream(("BTCUSDT",), client=client)
    await feed.bootstrap()

    state = feed._states["BTCUSDT"]
    assert state.price == Decimal("100")
    assert state.bid == Decimal("99.9")
    assert state.ask == Decimal("100.1")
    assert state.volume_ratio == Decimal("4")
    assert state.imbalance > Decimal("0")
    assert all(request.url.path.startswith("/api/v3/") for request in calls)
    assert all("X-MBX-APIKEY" not in request.headers for request in calls)
    await feed.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_testnet_stream_decodes_book_quote_depth_trade_and_closed_kline():
    feed = BinanceSpotTestnetStream(("ETHUSDT",))
    state = feed._states["ETHUSDT"]
    state.bid = Decimal("2999")
    state.ask = Decimal("3001")
    state.bid_levels = {Decimal("2999"): Decimal("10")}
    state.ask_levels = {Decimal("3001"): Decimal("2")}
    state.last_quote_update_ms = int(time.time() * 1000)
    state.last_depth_update_ms = int(time.time() * 1000)
    state.closed_volumes = deque(
        [Decimal("10")] * 20,
        maxlen=21,
    )
    state.volume_ratio = Decimal("1")

    quote = feed._handle_message({
        "stream": "ethusdt@bookTicker",
        "data": {"s": "ETHUSDT", "b": "3000", "B": "4", "a": "3002", "A": "3"},
    })
    assert quote is None
    assert state.bid == Decimal("3000")
    assert state.ask == Decimal("3002")

    depth = feed._handle_message({
        "stream": "ethusdt@depth20@100ms",
        "data": {
            "lastUpdateId": 500,
            "bids": [["3000", "12"], ["2999", "2"]],
            "asks": [["3002", "3"]],
        },
    })
    assert depth is None
    assert state.imbalance > Decimal("0")

    feed._handle_message({
        "stream": "ethusdt@kline_1m",
        "data": {"e": "kline", "E": 1_700_000_000_000,
                 "k": {"x": True, "v": "30"}},
    })
    assert state.volume_ratio == Decimal("3")

    tick = feed._handle_message({
        "stream": "ethusdt@trade",
        "data": {"e": "trade", "E": 1_700_000_000_100,
                 "s": "ETHUSDT", "p": "3005"},
    })
    assert tick is not None
    assert tick.source == SOURCE
    assert tick.symbol == "ETHUSDT"
    assert tick.price == Decimal("3005")
    assert tick.bid == Decimal("3000")
    assert tick.ask == Decimal("3002")
    assert tick.volume_ratio == Decimal("3")
    await feed.close()


def test_testnet_feed_rejects_non_allowlisted_symbols():
    with pytest.raises(ValueError, match="allowlist"):
        BinanceSpotTestnetStream(("BTCUSDT", "NOTREALUSDT"))
