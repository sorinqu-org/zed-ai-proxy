import json
import pytest
from pathlib import Path
from scripts.sync_account import upsert_account, validate_zed_credentials


def test_upsert_new_account(tmp_path: Path):
    acc_file = tmp_path / "accounts.json"
    res = upsert_account(acc_file, user_id="111", access_token="tok1", account_name="Acc 1")

    assert res["action"] == "created"
    assert res["account"]["id"] == "acc-1"
    assert res["account"]["user_id"] == "111"
    assert res["account"]["name"] == "Acc 1"
    assert res["total_accounts"] == 1

    with open(acc_file) as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["id"] == "acc-1"


def test_upsert_update_existing_account(tmp_path: Path):
    acc_file = tmp_path / "accounts.json"
    upsert_account(acc_file, user_id="111", access_token="tok1", account_name="Original Name")

    # Update the account with new token and name
    res = upsert_account(acc_file, user_id="111", access_token="tok1_updated", account_name="Updated Name")
    assert res["action"] == "updated"
    assert res["account"]["id"] == "acc-1"
    assert res["account"]["access_token"] == "tok1_updated"
    assert res["account"]["name"] == "Updated Name"
    assert res["total_accounts"] == 1

    with open(acc_file) as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["access_token"] == "tok1_updated"
    assert data[0]["name"] == "Updated Name"


def test_upsert_multiple_accounts(tmp_path: Path):
    acc_file = tmp_path / "accounts.json"
    upsert_account(acc_file, user_id="111", access_token="tok1", account_name="User 1")
    res2 = upsert_account(acc_file, user_id="222", access_token="tok2", account_name="User 2")

    assert res2["action"] == "created"
    assert res2["account"]["id"] == "acc-2"
    assert res2["total_accounts"] == 2

    with open(acc_file) as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["user_id"] == "111"
    assert data[1]["user_id"] == "222"
