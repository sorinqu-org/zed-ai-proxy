import pytest
from app.core.billing import (
    calculate_cost,
    format_currency,
    format_tokens,
    build_billing_url,
    PLAN_DEFAULT_LIMITS,
)
from app.core.account_pool import Account, AccountPool


def test_calculate_cost():
    # Sonnet: $3/M in, $15/M out
    cost = calculate_cost("claude-3-7-sonnet", 1_000_000, 1_000_000)
    assert round(cost, 2) == 18.00

    # Opus: $15/M in, $75/M out
    cost_opus = calculate_cost("claude-opus-5", 100_000, 100_000)
    assert round(cost_opus, 2) == 9.00

    # Haiku: $0.80/M in, $4.00/M out
    cost_haiku = calculate_cost("claude-haiku-5", 1_000_000, 1_000_000)
    assert round(cost_haiku, 2) == 4.80


def test_format_helpers():
    assert format_currency(12.3456) == "$12.35"
    assert format_currency(0) == "$0.00"
    assert format_currency(None) == "$0.00"

    assert format_tokens(0) == "0"
    assert format_tokens(500) == "500"
    assert format_tokens(1500) == "1.5k"
    assert format_tokens(2_500_000) == "2.50M"


def test_build_billing_url():
    url = build_billing_url("org_01m24akygwy207azwerfw280fe")
    assert url == "https://dashboard.zed.dev/org_01m24akygwy207azwerfw280fe/billing/usage"
    assert build_billing_url(None) == "https://dashboard.zed.dev/billing/usage"


def test_account_record_usage():
    acc = Account(
        id="acc-test",
        user_id="123",
        access_token="tok",
        spend_limit=20.00,
        spend_used=0.00,
        balance_remaining=20.00,
    )
    # Record 1M in, 1M out with sonnet ($18 cost)
    cost = acc.record_usage(1_000_000, 1_000_000, model="claude-sonnet-5")
    assert round(cost, 2) == 18.00
    assert acc.tokens_used == 2_000_000
    assert round(acc.spend_used, 2) == 18.00
    assert round(acc.balance_remaining, 2) == 2.00

    # Exceeding limit clamps balance to 0.0
    acc.record_usage(1_000_000, 1_000_000, model="claude-sonnet-5")
    assert acc.balance_remaining == 0.0


def test_pool_totals_and_summary():
    pool = AccountPool()
    pool.load_from_dict([
        {
            "id": "acc-1",
            "user_id": "u1",
            "access_token": "t1",
            "organization_id": "org_vip",
            "org_name": "VIP Org",
            "plan": "zed_vip",
            "spend_limit": 100.0,
            "spend_used": 10.0,
            "balance_remaining": 90.0,
            "tokens_used": 5000,
        },
        {
            "id": "acc-2",
            "user_id": "u2",
            "access_token": "t2",
            "organization_id": "org_pro",
            "org_name": "Pro Org",
            "plan": "zed_pro",
            "spend_limit": 10.0,
            "spend_used": 2.5,
            "balance_remaining": 7.5,
            "tokens_used": 1200,
        },
    ])

    assert pool.get_total_spend_limit() == 110.0
    assert pool.get_total_balance_remaining() == 97.5
    assert pool.get_total_spend_used() == 12.5
    assert pool.get_total_tokens_used() == 6200

    summary = pool.get_status_summary()
    assert summary["total_accounts"] == 2
    assert summary["total_balance_remaining"] == 97.5
    assert summary["total_spend_limit"] == 110.0
    assert summary["total_spend_used"] == 12.5
    assert summary["total_tokens_used"] == 6200
    assert len(summary["accounts"]) == 2
    assert summary["accounts"][0]["billing_url"] == "https://dashboard.zed.dev/org_vip/billing/usage"


def test_fetch_orb_portal_billing_mock(monkeypatch):
    import json
    import io
    from app.core.billing import fetch_orb_portal_billing

    class MockResponse:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def mock_urlopen(req, timeout=15.0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "customer_from_link" in url:
            return MockResponse({
                "customer": {
                    "id": "cust_123",
                    "name": "Zed VIP Test",
                    "pricing_unit": {"id": "unit_usd", "symbol": "$"}
                },
                "account": {
                    "currencies": [{"id": "unit_usd", "symbol": "$"}]
                }
            })
        elif "ledger_summary" in url:
            return MockResponse({
                "credits_balance": "15.64082815",
                "credit_blocks": [
                    {"maximum_initial_balance": "20.00", "balance": "15.64082815"}
                ]
            })
        raise ValueError(f"Unexpected url: {url}")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    res = fetch_orb_portal_billing("https://portal.withorb.com/view?token=test_token_123")
    assert res is not None
    assert res["balance_remaining"] == 15.64
    assert res["spend_limit"] == 20.00
    assert res["spend_used"] == 4.36
    assert res["customer_name"] == "Zed VIP Test"
    assert res["portal_token"] == "test_token_123"

