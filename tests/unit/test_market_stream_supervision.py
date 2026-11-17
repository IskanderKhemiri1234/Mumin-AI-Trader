import pytest

from mumin_trader.exchanges.binance.market_stream_service import MarketStreamService


@pytest.mark.asyncio
async def test_market_snapshot_exposes_disconnect_and_reconnect_state():
    service = MarketStreamService()
    service._client.connected = False
    service._client.reconnect_count = 3
    service._client.last_error = "ConnectionClosed"
    snapshot = await service.snapshot()
    assert snapshot["status"] == "disconnected"
    assert snapshot["connection_state"] == "connecting"
    assert snapshot["reconnect_count"] == 3
    assert snapshot["last_error"] == "ConnectionClosed"
    await service.close()


@pytest.mark.asyncio
async def test_market_snapshot_marks_old_prices_stale():
    service = MarketStreamService()
    service._assets["BTCUSDT"] = {
        "symbol": "BTCUSDT", "price": 60000.0, "change": 0.1,
        "event_time_ms": 1, "received_at": "2020-01-01T00:00:00+00:00",
    }
    service._last_received_at = None
    service._client.connected = False
    snapshot = await service.snapshot()
    assert snapshot["status"] == "stale"
    assert snapshot["assets"][0]["symbol"] == "BTCUSDT"
    await service.close()
