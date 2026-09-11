import pytest
import time
from app.core.account_pool import Account, AccountPool


def test_account_availability():
    acc = Account(id="1", user_id="u1", access_token="t1", status="active")
    assert acc.is_available() is True

    acc.status = "disabled"
    assert acc.is_available() is False

    acc.status = "cooldown"
    acc.cooldown_until = time.time() + 100
    assert acc.is_available() is False

    # Expired cooldown recovers
    acc.cooldown_until = time.time() - 1
    assert acc.is_available() is True
    assert acc.status == "active"


def test_account_token_expiring():
    acc = Account(id="1", user_id="u1", access_token="t1")
    assert acc.is_token_expiring() is True  # No token yet

    acc.current_llm_token = "valid_jwt"
    acc.expires_at = int(time.time()) + 1000
    assert acc.is_token_expiring(threshold_seconds=300) is False

    acc.expires_at = int(time.time()) + 100
    assert acc.is_token_expiring(threshold_seconds=300) is True


@pytest.mark.asyncio
async def test_pool_round_robin():
    pool = AccountPool()
    pool.load_from_dict(
        [
            {"id": "a1", "user_id": "u1", "access_token": "t1", "current_llm_token": "tok1", "expires_at": int(time.time()) + 3600},
            {"id": "a2", "user_id": "u2", "access_token": "t2", "current_llm_token": "tok2", "expires_at": int(time.time()) + 3600},
        ]
    )

    acc1 = await pool.get_next_account()
    acc2 = await pool.get_next_account()
    acc3 = await pool.get_next_account()

    assert acc1.id != acc2.id
    assert acc3.id == acc1.id


@pytest.mark.asyncio
async def test_pool_cooldown_handling():
    pool = AccountPool()
    pool.load_from_dict(
        [
            {"id": "a1", "user_id": "u1", "access_token": "t1", "current_llm_token": "tok1", "expires_at": int(time.time()) + 3600},
            {"id": "a2", "user_id": "u2", "access_token": "t2", "current_llm_token": "tok2", "expires_at": int(time.time()) + 3600},
        ]
    )

    pool.mark_rate_limited("a1", cooldown_seconds=60)
    summary = pool.get_status_summary()
    assert summary["available_accounts"] == 1

    # Next call should pick a2
    picked = await pool.get_next_account()
    assert picked.id == "a2"
