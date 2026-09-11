import json
from app.adapters.openai_adapter import (
    detect_provider,
    openai_to_zed_body,
    parse_zed_chunk_to_openai_delta,
    normalize_content_text,
    convert_tools_openai_to_anthropic,
)
from app.adapters.anthropic_adapter import (
    anthropic_to_zed_body,
    parse_zed_chunk_to_anthropic_event,
)
from app.adapters.payload_filter import sanitize_message_content, filter_payload


def test_detect_provider():
    assert detect_provider("claude-opus-5") == "anthropic"
    assert detect_provider("claude-sonnet-5") == "anthropic"
    assert detect_provider("gpt-5.6-luna") == "open_ai"
    assert detect_provider("gemini-3.1-pro") == "google"
    assert detect_provider("grok-build-1") == "x_ai"


def test_normalize_content_text():
    assert normalize_content_text("hello") == "hello"
    assert normalize_content_text([{"type": "text", "text": "foo"}, {"type": "text", "text": "bar"}]) == "foo\nbar"
    assert normalize_content_text(None) == ""


def test_openai_to_zed_body_anthropic():
    req = {
        "model": "claude-opus-5",
        "messages": [
            {"role": "developer", "content": "Be concise."},
            {"role": "user", "content": "Hi"},
        ],
        "temperature": 0.7,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Weather info",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    zed_body = openai_to_zed_body(req)
    assert zed_body["provider"] == "anthropic"
    assert zed_body["model"] == "claude-opus-5"
    assert zed_body["provider_request"]["system"] == "Be concise."
    assert len(zed_body["provider_request"]["messages"]) == 1
    assert zed_body["provider_request"]["messages"][0]["content"] == "Hi"
    assert zed_body["provider_request"]["tools"][0]["name"] == "get_weather"
    assert "input_schema" in zed_body["provider_request"]["tools"][0]


def test_parse_zed_chunk_to_openai_delta():
    # Anthropic text delta inside Zed chunk
    raw = json.dumps({"event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello world"}}})
    res = parse_zed_chunk_to_openai_delta(raw)
    assert res is not None
    delta_str, finish = res
    data = json.loads(delta_str)
    assert data["choices"][0]["delta"]["content"] == "Hello world"
    assert finish is None

    # Status stream_ended
    ended = json.dumps({"status": "stream_ended"})
    res_ended = parse_zed_chunk_to_openai_delta(ended)
    assert res_ended is not None
    assert res_ended[0] == "[DONE]"
    assert res_ended[1] == "stop"


def test_anthropic_adapter():
    req = {
        "model": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    zed_body = anthropic_to_zed_body(req)
    assert zed_body["provider"] == "anthropic"
    assert zed_body["model"] == "claude-sonnet-5"

    raw = json.dumps({"event": {"type": "content_block_delta", "delta": {"text": "response"}}})
    parsed = parse_zed_chunk_to_anthropic_event(raw)
    assert parsed is not None
    assert parsed[0] == "content_block_delta"


def test_payload_filter():
    small_text = "short text"
    assert sanitize_message_content(small_text) == "short text"

    # Oversized base64
    huge_data = "data:image/png;base64," + "A" * 600_000
    res = sanitize_message_content(huge_data, max_size_bytes=500_000)
    assert "[Image omitted" in res
