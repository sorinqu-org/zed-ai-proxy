import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_status_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_accounts" in data
        assert "available_accounts" in data
        assert "accounts" in data


@pytest.mark.asyncio
async def test_dashboard_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Zed AI Proxy Dashboard" in resp.text


@pytest.mark.asyncio
async def test_models_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # List models
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        model_ids = [m["id"] for m in data["data"]]
        assert "claude-opus-5" in model_ids
        assert "claude-sonnet-5" in model_ids

        # Retrieve specific model
        resp_model = await client.get("/v1/models/claude-opus-5")
        assert resp_model.status_code == 200
        assert resp_model.json()["id"] == "claude-opus-5"

        # Not found model
        resp_404 = await client.get("/v1/models/non-existent-model")
        assert resp_404.status_code == 404
        assert "error" in resp_404.json()
