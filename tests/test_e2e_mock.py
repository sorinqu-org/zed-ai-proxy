import json
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.account_pool import Account, AccountPool


@pytest.mark.asyncio
async def test_e2e_chat_completions_streaming(monkeypatch):
    pool = AccountPool()
    pool.load_from_dict([
        {"id": "acc-mock", "user_id": "123", "access_token": "secret", "current_llm_token": "valid_token", "expires_at": 9999999999}
    ])
    app.state.pool = pool

    async def mock_forward_to_zed_cloud(zed_body, pool_instance, client=None, max_retries=3):
        lines = [
            'data: {"status": "started"}\n\n',
            'data: {"event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello from Zed Proxy!"}}}\n\n',
            'data: {"status": "stream_ended"}\n\n',
        ]
        async def aiter_lines():
            for line in lines:
                yield line

        class FakeResp:
            status_code = 200
            headers = {}
            def aiter_lines(self):
                return aiter_lines()
            async def aclose(self):
                pass

        return FakeResp(), pool.accounts[0]

    monkeypatch.setattr("app.main.forward_to_zed_cloud", mock_forward_to_zed_cloud)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Streaming
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-5",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        text = resp.text
        assert "data: " in text
        assert "assistant" in text  # Initial role chunk
        assert "Hello from Zed Proxy!" in text
        assert "data: [DONE]" in text

        # 2. Non-Streaming
        resp_non_stream = await client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-5",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert resp_non_stream.status_code == 200
        data = resp_non_stream.json()
        assert data["choices"][0]["message"]["content"] == "Hello from Zed Proxy!"
        assert data["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_e2e_anthropic_messages(monkeypatch):
    pool = AccountPool()
    pool.load_from_dict([
        {"id": "acc-mock", "user_id": "123", "access_token": "secret", "current_llm_token": "valid_token", "expires_at": 9999999999}
    ])
    app.state.pool = pool

    async def mock_forward_to_zed_cloud(zed_body, pool_instance, client=None, max_retries=3):
        lines = [
            'data: {"event": {"type": "message_start", "message": {"id": "msg_upstream", "usage": {"input_tokens": 15}}}}\n\n',
            'data: {"event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Anthropic response"}}}\n\n',
            'data: {"event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 8}}}\n\n',
            'data: {"event": {"type": "message_stop"}}\n\n',
        ]
        async def aiter_lines():
            for line in lines:
                yield line

        class FakeResp:
            status_code = 200
            headers = {}
            def aiter_lines(self):
                return aiter_lines()
            async def aclose(self):
                pass

        return FakeResp(), pool.accounts[0]

    monkeypatch.setattr("app.main.forward_to_zed_cloud", mock_forward_to_zed_cloud)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-5",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "msg_upstream"
        assert data["content"][0]["text"] == "Anthropic response"
        assert data["usage"]["input_tokens"] == 15
        assert data["usage"]["output_tokens"] == 8
