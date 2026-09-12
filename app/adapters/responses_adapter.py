import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.adapters.openai_adapter import (
    detect_provider,
    normalize_openai_model,
    openai_to_zed_body,
    parse_zed_chunk_to_openai_delta,
)

logger = logging.getLogger("zed_proxy.responses_adapter")


def convert_responses_input_to_messages(
    input_data: Any, instructions: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Converts OpenAI Responses API input and instructions into standard messages list."""
    messages: List[Dict[str, Any]] = []

    if instructions:
        messages.append({"role": "system", "content": instructions})

    if not input_data:
        return messages

    if isinstance(input_data, str):
        messages.append({"role": "user", "content": input_data})
        return messages

    if isinstance(input_data, dict):
        input_data = [input_data]

    if isinstance(input_data, list):
        for item in input_data:
            if not isinstance(item, dict):
                continue

            role = item.get("role")
            item_type = item.get("type")

            if role:
                content = item.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for part in content:
                        if isinstance(part, str):
                            text_parts.append(part)
                        elif isinstance(part, dict):
                            if part.get("type") in ("text", "input_text"):
                                text_parts.append(part.get("text", ""))
                    content = "\n".join(text_parts)
                messages.append({"role": role, "content": content})
            elif item_type == "message":
                msg_role = item.get("role", "user")
                raw_c = item.get("content", "")
                messages.append({"role": msg_role, "content": str(raw_c)})
            elif item_type == "function_call_output":
                call_id = item.get("call_id", "")
                output = item.get("output", "")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": str(output),
                })

    return messages


def responses_to_zed_body(req: Dict[str, Any]) -> Dict[str, Any]:
    """Converts a /v1/responses request payload into Zed CompletionBody."""
    model = req.get("model", "claude-sonnet-5")
    input_data = req.get("input", "")
    instructions = req.get("instructions")
    messages = convert_responses_input_to_messages(input_data, instructions=instructions)

    openai_req = {
        "model": model,
        "messages": messages,
        "stream": req.get("stream", True),
    }
    if "tools" in req:
        openai_req["tools"] = req["tools"]
    if "tool_choice" in req:
        openai_req["tool_choice"] = req["tool_choice"]
    if "temperature" in req:
        openai_req["temperature"] = req["temperature"]
    if "max_output_tokens" in req:
        openai_req["max_tokens"] = req["max_output_tokens"]

    return openai_to_zed_body(openai_req)
