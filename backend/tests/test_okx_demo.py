"""OKX Demo Trading client + LiveTradingManager.connect_okx tests."""

import base64
import hashlib
import hmac
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_okx_sign_matches_hmac_base64():
    from okx_demo_client import OkxRestClient

    client = OkxRestClient(
        api_key="key",
        api_secret="secret",
        passphrase="pass",
        demo=True,
    )
    ts = "2020-12-08T09:08:57.715Z"
    path = "/api/v5/account/balance"
    sign = client._sign(ts, "GET", path, "")
    expected = base64.b64encode(
        hmac.new(b"secret", f"{ts}GET{path}".encode(), hashlib.sha256).digest()
    ).decode()
    assert sign == expected


@pytest.mark.asyncio
async def test_connect_okx_verified_on_balance():
    from live_trading import LiveTradingManager

    mgr = LiveTradingManager()
    fake_balances = [
        {
            "details": [
                {"ccy": "USDT", "eq": "10000", "availBal": "10000", "frozenBal": "0"},
            ]
        }
    ]
    with patch("okx_demo_client.OkxRestClient.get_balance", new_callable=AsyncMock) as m:
        m.return_value = fake_balances
        result = await mgr.connect_okx(
            "testkey12345", "testsecret12345", "demo-pass", demo=True
        )
    assert result["success"] is True
    assert result["verified"] is True
    assert result["demo"] is True
    assert mgr.is_connected("okx")


@pytest.mark.asyncio
async def test_connect_okx_auth_error():
    from live_trading import LiveTradingManager, OkxAuthError
    from okx_demo_client import OkxAuthError as ClientAuthError

    mgr = LiveTradingManager()
    with patch(
        "okx_demo_client.OkxRestClient.get_balance",
        new_callable=AsyncMock,
        side_effect=ClientAuthError("bad key"),
    ):
        with pytest.raises(OkxAuthError):
            await mgr.connect_okx("testkey12345", "testsecret12345", "demo-pass", demo=True)


@pytest.mark.asyncio
async def test_submit_and_cancel_okx_order():
    from live_trading import AdapterConnection, LiveTradingManager

    mgr = LiveTradingManager()
    client = MagicMock()
    client.place_spot_order = AsyncMock(
        return_value={"ordId": "12345", "clOrdId": "c1", "sCode": "0"}
    )
    client.cancel_order = AsyncMock(return_value={"ordId": "12345", "sCode": "0"})
    mgr._connections["okx"] = AdapterConnection(
        adapter_id="okx",
        connection_id="CONN-OKX-TEST",
        status="connected",
        okx_client=client,
        okx_demo=True,
    )
    mgr._is_active = True

    placed = await mgr.submit_order(
        {"instrument": "BTC-USDT", "side": "buy", "type": "limit", "quantity": "0.01", "price": "10000"}
    )
    assert placed["success"] is True
    assert placed["exchange"] == "OKX"
    assert placed["exchange_order_id"] == "12345"

    cancelled = await mgr.cancel_order("12345", symbol="BTC-USDT")
    assert cancelled["success"] is True
    client.cancel_order.assert_awaited()


def test_adapter_connect_okx_requires_passphrase(client):
    r = client.post(
        "/api/adapters/okx/connect",
        json={"api_key": "testkey12345", "api_secret": "testsecret12345"},
    )
    assert r.status_code == 400
    assert "passphrase" in r.json()["detail"].lower()


def test_adapter_connect_okx_mocked(client):
    with patch(
        "routers.adapters.live_manager.connect_okx",
        new_callable=AsyncMock,
        return_value={
            "connection_id": "CONN-OKX-MOCK",
            "verified": True,
            "demo": True,
            "account_info": {},
        },
    ) as m:
        r = client.post(
            "/api/adapters/okx/connect",
            json={
                "api_key": "testkey12345",
                "api_secret": "testsecret12345",
                "passphrase": "demo-pass",
                "demo": True,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["demo"] is True
    m.assert_awaited_once()
