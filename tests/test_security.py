import pytest
from app.core.security import SecurityPolicy


def test_is_tool_dangerous():
    sec = SecurityPolicy()
    assert sec.is_tool_dangerous("bash") is True
    assert sec.is_tool_dangerous("run_command") is True
    assert sec.is_tool_dangerous("execute_shell") is True
    assert sec.is_tool_dangerous("write_file") is True
    assert sec.is_tool_dangerous("read_file") is True
    assert sec.is_tool_dangerous("get_weather") is False
    assert sec.is_tool_dangerous("calculate_tax") is False


def test_sanitize_request_tools_sanitize_mode():
    sec = SecurityPolicy()
    sec.tool_policy = "sanitize"
    body = {
        "tools": [
            {"type": "function", "function": {"name": "bash", "parameters": {}}},
            {"type": "function", "function": {"name": "get_weather", "parameters": {}}},
            {"name": "write_file", "input_schema": {}},
        ]
    }
    cleaned = sec.sanitize_request_tools(body)
    assert len(cleaned["tools"]) == 1
    assert cleaned["tools"][0]["function"]["name"] == "get_weather"


def test_sanitize_request_tools_block_all():
    sec = SecurityPolicy()
    sec.tool_policy = "block_all"
    body = {
        "tools": [
            {"name": "get_weather"},
        ],
        "tool_choice": "auto",
    }
    cleaned = sec.sanitize_request_tools(body)
    assert "tools" not in cleaned
    assert "tool_choice" not in cleaned


def test_inject_isolation_prompt():
    sec = SecurityPolicy()
    body = {"messages": [{"role": "user", "content": "hello"}]}
    guarded = sec.inject_isolation_prompt(body)
    assert "System isolation policy" in guarded["system"]


def test_sanitize_outgoing_text():
    sec = SecurityPolicy()
    raw = "An error occurred with token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.doNotLeakThisSignature and secret client_secret_value_xyz"
    cleaned = sec.sanitize_outgoing_text(raw, account_pool_tokens=["client_secret_value_xyz"])
    assert "eyJ" not in cleaned
    assert "[REDACTED_TOKEN]" in cleaned
    assert "client_secret_value_xyz" not in cleaned
    assert "[REDACTED_SECRET]" in cleaned
