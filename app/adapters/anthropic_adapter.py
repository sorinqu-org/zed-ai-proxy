import json
import logging
from typing import Any, Dict, Optional, Tuple

from app.adapters.payload_filter import filter_payload
from app.adapters.openai_adapter import detect_provider

logger = logging.getLogger("zed_proxy.anthropic_adapter")


def normalize_anthropic_model(model: str) -> str:
    """Maps client model strings to Zed Cloud model identifiers."""
    m = model.lower().strip()
    if "opus" in m:
        return "claude-opus-5"
    if "haiku" in m:
        return "claude-haiku-4-5"
    if "fable" in m:
        return "claude-fable-5"
    if "sonnet" in m or "claude" in m:
        return "claude-sonnet-5"
    return model


def anthropic_to_zed_body(req: Dict[str, Any]) -> Dict[str, Any]:
    """Converts an Anthropic /v1/messages request into a Zed CompletionBody."""
    raw_model = req.get("model", "claude-sonnet-5")
    model = normalize_anthropic_model(raw_model)
    provider = detect_provider(model)
    provider_req = dict(req)
    provider_req["model"] = model
    if "messages" in provider_req:
        provider_req["messages"] = filter_payload(provider_req["messages"])
    provider_req["stream"] = True

    return {
        "provider": provider,
        "model": model,
        "provider_request": provider_req,
    }


def parse_zed_chunk_to_anthropic_event(raw_line: str) -> Optional[Tuple[str, str]]:
    """
    Parses a raw line from Zed SSE.
    Returns a tuple of (event_type, json_data_str) suitable for Anthropic SSE format.
    """
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("data:"):
        line = line[len("data:") :].strip()

    if line == "[DONE]":
        return ("message_stop", json.dumps({"type": "message_stop"}))

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    if "status" in data:
        if data["status"] in ("stream_ended", "finished"):
            return ("message_stop", json.dumps({"type": "message_stop"}))
        return None

    event = data.get("event")
    if not event:
        return None

    event_type = event.get("type", "message_delta")
    return (event_type, json.dumps(event))
