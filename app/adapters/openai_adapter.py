import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.adapters.payload_filter import filter_payload

logger = logging.getLogger("zed_proxy.openai_adapter")


def detect_provider(model: str) -> str:
    model_lower = model.lower()
    if "claude" in model_lower:
        return "anthropic"
    elif "gemini" in model_lower:
        return "google"
    elif "grok" in model_lower:
        return "x_ai"
    return "open_ai"


def openai_to_zed_body(req: Dict[str, Any]) -> Dict[str, Any]:
    """Converts an OpenAI-style chat completion request into a Zed CompletionBody."""
    model = req.get("model", "claude-sonnet-5")
    provider = detect_provider(model)
    messages = filter_payload(req.get("messages", []))

    if provider == "anthropic":
        # Extract system prompt for Anthropic
        system_content = None
        user_assistant_msgs = []
        for m in messages:
            if m.get("role") == "system":
                if system_content is None:
                    system_content = m.get("content", "")
                else:
                    system_content += "\n" + m.get("content", "")
            else:
                user_assistant_msgs.append(m)

        provider_req = {
            "model": model,
            "messages": user_assistant_msgs,
            "max_tokens": req.get("max_tokens", 4096),
            "stream": True,
        }
        if system_content:
            provider_req["system"] = system_content
        if "temperature" in req:
            provider_req["temperature"] = req["temperature"]
        if "tools" in req:
            provider_req["tools"] = req["tools"]
        if "tool_choice" in req:
            provider_req["tool_choice"] = req["tool_choice"]

    else:
        # OpenAI or Google format
        provider_req = dict(req)
        provider_req["messages"] = messages
        provider_req["stream"] = True

    return {
        "provider": provider,
        "model": model,
        "provider_request": provider_req,
    }


def parse_zed_chunk_to_openai_delta(raw_line: str) -> Optional[str]:
    """Parses a Zed SSE line and formats it as an OpenAI SSE delta chunk."""
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("data:"):
        line = line[len("data:") :].strip()

    if line == "[DONE]":
        return "[DONE]"

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    # Check status messages
    if "status" in data:
        if data["status"] in ("stream_ended", "finished"):
            return "[DONE]"
        return None

    event = data.get("event")
    if not event:
        # If it's already an OpenAI formatted response chunk
        if "choices" in data:
            return json.dumps(data)
        return None

    # Handle Anthropic event mapping to OpenAI
    event_type = event.get("type")
    content = ""

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            content = delta.get("text", "")
        elif delta.get("type") == "thinking_delta":
            # Pass reasoning if client supports it, or as content
            content = delta.get("thinking", "")
    elif event_type == "message_delta":
        return None
    elif event_type == "message_stop":
        return "[DONE]"
    elif "choices" in event:
        return json.dumps(event)

    if content:
        chunk = {
            "id": "chatcmpl-zed",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "zed-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                }
            ],
        }
        return json.dumps(chunk)

    return None
