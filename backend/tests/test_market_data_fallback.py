"""Market data instruments list must stay complete when Binance is down."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import market_data_service as svc


@pytest.fixture(autouse=True)
def reset_caches():
    svc._symbol_cache.clear()
    svc._instruments_cache = None
    yield
    svc._symbol_cache.clear()
    svc._instruments_cache = None


def test_complete_instruments_fills_missing_with_fallback():
    partial = [
        {
            "symbol": "BTCUSDT",
            "base": "BTC",
            "quote": "USDT",
            "exchange": "BINANCE",
            "price": 1.0,
            "change_24h": 0.0,
            "bid": 0.9,
            "ask": 1.1,
            "volume_24h": 1.0,
            "timestamp": "t",
        }
    ]
    result = svc._complete_instruments(partial)
    syms = [r["symbol"] for r in result]
    assert syms == svc.SYMBOLS
    assert result[0]["price"] == 1.0
    assert result[1]["symbol"] == "ETHUSDT"
    assert result[1]["price"] == svc._FALLBACK["ETHUSDT"]["price"]


@pytest.mark.asyncio
async def test_get_instruments_full_list_when_binance_fails(monkeypatch):
    # Simulate prior single-symbol cache (the bug that shrunk the UI list)
    svc._symbol_cache["BTCUSDT"] = {
        "data": svc._fallback_for("BTCUSDT"),
        "fetched_at": 0.0,
    }

    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            raise RuntimeError("binance unreachable")

    monkeypatch.setattr(svc.httpx, "AsyncClient", BoomClient)

    result = await svc.get_instruments()
    assert [r["symbol"] for r in result] == svc.SYMBOLS
    assert len(result) == len(svc.SYMBOLS)
