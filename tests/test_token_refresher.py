import base64
import json
import time
import pytest
import httpx
from app.core.token_refresher import TokenRefresher, decode_jwt_exp


def test_decode_jwt_exp():
    now = int(time.time())
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": now + 7200}).encode()).decode().rstrip("=")
    dummy_jwt = f"{header}.{payload}."

    exp = decode_jwt_exp(dummy_jwt)
    assert exp == now + 7200


@pytest.mark.asyncio
async def test_fetch_llm_token_mock():
    refresher = TokenRefresher(zed_api_url="http://mock.zed.dev")

    # Mock transport
    now = int(time.time()) + 3600
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": now}).encode()).decode().rstrip("=")
    dummy_jwt = f"{header}.{payload}."

    def mock_handler(request: httpx.Request):
        assert request.headers.get("Authorization") == "123 pass"
        return httpx.Response(200, json={"token": dummy_jwt})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await refresher.fetch_llm_token("123", "pass", client=client)
        assert res["token"] == dummy_jwt
        assert res["expires_at"] == now
