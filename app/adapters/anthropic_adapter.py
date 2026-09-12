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

    # Extract any system messages out of messages array into provider_req["system"]
    system_parts = []
    if "system" in provider_req and provider_req["system"]:
        sys = provider_req["system"]
        if isinstance(sys, str):
            system_parts.append(sys)
        elif isinstance(sys, list):
            for b in sys:
                if isinstance(b, dict) and "text" in b:
                    system_parts.append(b["text"])
                elif isinstance(b, str):
                    system_parts.append(b)

    filtered_msgs = []
    raw_msgs = provider_req.get("messages") or []
    for m in raw_msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("system", "developer"):
            c = m.get("content", "")
            if isinstance(c, str):
                system_parts.append(c)
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and "text" in b:
                        system_parts.append(b["text"])
                    elif isinstance(b, str):
                        system_parts.append(b)
        else:
            filtered_msgs.append(m)

    if system_parts:
        provider_req["system"] = "\n\n".join(system_parts)

    if "max_tokens" not in provider_req or not provider_req["max_tokens"]:
        provider_req["max_tokens"] = 4096

    if "betas" in provider_req and isinstance(provider_req["betas"], list):
        allowed_betas = {"compact_20260112"}
        filtered_betas = [b for b in provider_req["betas"] if b in allowed_betas]
        if filtered_betas:
            provider_req["betas"] = filtered_betas
        else:
            del provider_req["betas"]

    # Claude Code sends context_management: {edits: [{type: "clear_thinking_20251015"}]} which Zed Cloud rejects
    if "context_management" in provider_req:
        del provider_req["context_management"]

    provider_req["messages"] = filter_payload(filtered_msgs)
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
