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

        # Test route alias /models without /v1
        resp_alias = await client.get("/models")
        assert resp_alias.status_code == 200
        assert resp_alias.json()["object"] == "list"

        # Retrieve specific model
        resp_model = await client.get("/v1/models/claude-opus-5")
        assert resp_model.status_code == 200
        assert resp_model.json()["id"] == "claude-opus-5"

        # Not found model
        resp_404 = await client.get("/v1/models/non-existent-model")
        assert resp_404.status_code == 404
        assert "error" in resp_404.json()


@pytest.mark.asyncio
async def test_account_dashboard_and_stats_endpoint():
    from app.core.account_pool import Account
    test_acc = Account(
        id="acc-dash-test",
        name="Dashboard Test Account",
        user_id="999888",
        access_token="tok_test",
        spend_limit=20.0,
        spend_used=2.5,
        balance_remaining=17.5,
        orb_portal_url="https://portal.withorb.com/view?token=test_token",
    )
    test_acc.record_usage(
        input_tokens=5000,
        output_tokens=1200,
        model="claude-3-7-sonnet",
        cache_write_tokens=3000,
        cache_read_tokens=10000,
    )
    app.state.pool.add_account(test_acc)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Valid account dashboard
        resp = await client.get("/dashboard/accounts/acc-dash-test")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Dashboard Test Account" in resp.text
        assert "Cache Write" in resp.text
        assert "Cache Read" in resp.text
        assert "Input Tokens" in resp.text
        assert "Output Tokens" in resp.text
        assert "claude-3-7-sonnet" in resp.text
        assert "portal.withorb.com" in resp.text

        # Valid account stats JSON
        resp_stats = await client.get("/api/accounts/acc-dash-test/stats")
        assert resp_stats.status_code == 200
        data = resp_stats.json()
        assert data["id"] == "acc-dash-test"
        assert data["cache_write_tokens_total"] == 3000
        assert data["cache_read_tokens_total"] == 10000
        assert "claude-3-7-sonnet" in data["model_stats"]

        # 404 for nonexistent account
        resp_404 = await client.get("/dashboard/accounts/nonexistent")
        assert resp_404.status_code == 404
        resp_stats_404 = await client.get("/api/accounts/nonexistent/stats")
        assert resp_stats_404.status_code == 404
