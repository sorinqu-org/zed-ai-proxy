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
    content = zed_body["provider_request"]["messages"][0]["content"]
    assert content == "Hi" or content == [{"type": "text", "text": "Hi"}]
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


def test_model_normalization():
    from app.adapters.anthropic_adapter import normalize_anthropic_model
    from app.adapters.openai_adapter import normalize_openai_model

    # Anthropic mappings
    assert normalize_anthropic_model("claude-3-7-sonnet-20250219") == "claude-sonnet-5"
    assert normalize_anthropic_model("claude-3-5-sonnet-20241022") == "claude-sonnet-5"
    assert normalize_anthropic_model("claude-3-opus-20240229") == "claude-opus-5"
    assert normalize_anthropic_model("claude-3-5-haiku-20241022") == "claude-haiku-4-5"
    assert normalize_anthropic_model("claude-opus-5") == "claude-opus-5"

    # OpenAI mappings
    assert normalize_openai_model("gpt-4o") == "gpt-5.6-luna"
    assert normalize_openai_model("o1") == "gpt-5.6-luna"
    assert normalize_openai_model("o3-mini") == "gpt-5.6-luna"
    assert normalize_openai_model("claude-3-7-sonnet-20250219") == "claude-sonnet-5"
    assert normalize_openai_model("claude-3-opus-20240229") == "claude-opus-5"
    assert normalize_openai_model("gemini-1.5-pro") == "gemini-3.1-pro"
    assert normalize_openai_model("gemini-1.5-flash") == "gemini-3-flash"


def test_responses_adapter():
    from app.adapters.responses_adapter import (
        convert_responses_input_to_messages,
        responses_to_zed_body,
    )

    # String input with instructions
    msgs = convert_responses_input_to_messages("Hello", instructions="System prompt")
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "System prompt"}
    assert msgs[1] == {"role": "user", "content": "Hello"}

    # List of message items
    input_items = [
        {"role": "user", "content": [{"type": "input_text", "text": "What is 2+2?"}]}
    ]
    msgs2 = convert_responses_input_to_messages(input_items)
    assert len(msgs2) == 1
    assert msgs2[0]["role"] == "user"
    assert "2+2" in msgs2[0]["content"]

    # Full responses request to zed body
    req = {
        "model": "claude-sonnet-5",
        "input": "Calculate 5*5",
        "instructions": "Be precise",
    }
    zed_body = responses_to_zed_body(req)
    assert zed_body["provider"] == "anthropic"
    assert zed_body["model"] == "claude-sonnet-5"


