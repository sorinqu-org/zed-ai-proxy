import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

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


def normalize_content_text(content: Any) -> str:
    """Safely extracts plain text from content whether it is a string or list of blocks."""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content)


def convert_tools_openai_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Converts OpenAI/Codex tools list into Anthropic format with guaranteed input_schema."""
    anthropic_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            schema = func.get("parameters") or {"type": "object", "properties": {}}
            anthropic_tools.append(
                {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": schema,
                }
            )
        else:
            name = tool.get("name", "")
            desc = tool.get("description", "")
            schema = tool.get("input_schema") or tool.get("parameters") or {"type": "object", "properties": {}}
            anthropic_tools.append(
                {
                    "name": name,
                    "description": desc,
                    "input_schema": schema,
                }
            )
    return anthropic_tools


def convert_tool_choice_to_anthropic(choice: Any) -> Any:
    """Converts OpenAI tool_choice into Anthropic format."""
    if isinstance(choice, str):
        if choice == "auto":
            return {"type": "auto"}
        elif choice in ("required", "any"):
            return {"type": "any"}
    return choice


def convert_messages_for_anthropic(messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Extracts system/developer messages and ensures alternating user/assistant turns
    with proper tool_result conversion.
    """
    system_parts = []
    processed_turns = []

    for m in messages:
        role = m.get("role")
        raw_content = m.get("content")

        if role in ("system", "developer"):
            text = normalize_content_text(raw_content)
            if text:
                system_parts.append(text)
            continue

        if role == "tool":
            # Map OpenAI tool response into an Anthropic tool_result block in a user turn
            tool_call_id = m.get("tool_call_id", "")
            tool_content = normalize_content_text(raw_content)
            tool_block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": tool_content,
                "is_error": False,
            }
            # If previous message is already user, append block; otherwise new user turn
            if processed_turns and processed_turns[-1]["role"] == "user":
                last_c = processed_turns[-1]["content"]
                if isinstance(last_c, list):
                    last_c.append(tool_block)
                else:
                    processed_turns[-1]["content"] = [{"type": "text", "text": str(last_c)}, tool_block]
            else:
                processed_turns.append({"role": "user", "content": [tool_block]})
            continue

        if role in ("user", "assistant"):
            # Check for assistant tool_calls from OpenAI format
            if role == "assistant" and "tool_calls" in m and isinstance(m["tool_calls"], list):
                content_blocks = []
                if raw_content:
                    content_blocks.append({"type": "text", "text": normalize_content_text(raw_content)})
                for tc in m["tool_calls"]:
                    func = tc.get("function", {})
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except Exception:
                        args = {}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                            "name": func.get("name", ""),
                            "input": args,
                        }
                    )
                processed_turns.append({"role": "assistant", "content": content_blocks})
                continue

            # Standard turn
            new_text = normalize_content_text(raw_content)
            if processed_turns and processed_turns[-1]["role"] == role:
                # Merge consecutive same-role turns for Anthropic compliance
                prev = processed_turns[-1]
                prev_c = prev["content"]
                if isinstance(prev_c, list):
                    prev_c.append({"type": "text", "text": new_text})
                else:
                    prev["content"] = [{"type": "text", "text": str(prev_c) + "\n\n" + new_text}]
            else:
                processed_turns.append({"role": role, "content": [{"type": "text", "text": new_text}]})

    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return system_prompt, processed_turns


def normalize_openai_model(model: str) -> str:
    """Maps client model strings to Zed Cloud model identifiers."""
    m = model.lower().strip()
    if "opus" in m:
        return "claude-opus-5"
    if "haiku" in m:
        return "claude-haiku-4-5"
    if "fable" in m:
        return "claude-fable-5"
    if "sonnet" in m:
        return "claude-sonnet-5"
    if "gemini" in m:
        if "pro" in m or "ultra" in m or "3.1" in m:
            return "gemini-3.1-pro"
        return "gemini-3-flash"
    if "sol" in m:
        return "gpt-5.6-sol"
    if "luna" in m or "gpt" in m or "o1" in m or "o3" in m or "o4" in m or "codex" in m:
        return "gpt-5.6-luna"
    if "claude" in m:
        return "claude-sonnet-5"
    return model


def openai_to_zed_body(req: Dict[str, Any]) -> Dict[str, Any]:
    """Converts an OpenAI-style chat completion request into a Zed CompletionBody."""
    raw_model = req.get("model", "claude-sonnet-5")
    model = normalize_openai_model(raw_model)
    provider = detect_provider(model)
    raw_messages = filter_payload(req.get("messages") or [])

    if provider == "anthropic":
        system_content, user_assistant_msgs = convert_messages_for_anthropic(raw_messages)
        provider_req: Dict[str, Any] = {
            "model": model,
            "messages": user_assistant_msgs,
            "max_tokens": req.get("max_tokens", 4096),
            "stream": True,
        }
        if system_content:
            provider_req["system"] = system_content
        if "temperature" in req:
            provider_req["temperature"] = req["temperature"]
        if "tools" in req and isinstance(req["tools"], list):
            provider_req["tools"] = convert_tools_openai_to_anthropic(req["tools"])
        if "tool_choice" in req:
            provider_req["tool_choice"] = convert_tool_choice_to_anthropic(req["tool_choice"])

    else:
        # OpenAI or Google format
        provider_req = dict(req)
        provider_req["messages"] = raw_messages
        provider_req["stream"] = True

    return {
        "provider": provider,
        "model": model,
        "provider_request": provider_req,
    }


def make_openai_chunk(
    req_id: str,
    model: str,
    delta: Dict[str, Any],
    finish_reason: Optional[str] = None,
) -> str:
    """Generates an RFC-compliant OpenAI chat completion chunk."""
    chunk = {
        "id": req_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return json.dumps(chunk)


def parse_zed_chunk_to_openai_delta(
    raw_line: str,
    req_id: str = "chatcmpl-zed",
    model: str = "zed-model",
) -> Optional[Tuple[str, Optional[str]]]:
    """
    Parses a Zed SSE line.
    Returns (json_chunk_str, finish_reason) or None.
    If terminal, finish_reason will be populated.
    """
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("data:"):
        line = line[len("data:") :].strip()

    if line == "[DONE]":
        return ("[DONE]", "stop")

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    # Check status messages
    if "status" in data:
        if data["status"] in ("stream_ended", "finished"):
            return ("[DONE]", "stop")
        return None

    event = data.get("event")
    if not event:
        if "choices" in data:
            return (json.dumps(data), None)
        return None

    event_type = event.get("type")

    # 1. Content Block Start (check for tool_use)
    if event_type == "content_block_start":
        cb = event.get("content_block", {})
        if cb.get("type") == "tool_use":
            tool_call = {
                "index": event.get("index", 0),
                "id": cb.get("id"),
                "type": "function",
                "function": {
                    "name": cb.get("name", ""),
                    "arguments": "",
                },
            }
            return (make_openai_chunk(req_id, model, {"tool_calls": [tool_call]}), None)
        return None

    # 2. Content Block Delta
    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text", "")
            if text:
                return (make_openai_chunk(req_id, model, {"content": text}), None)
        elif delta_type == "thinking_delta":
            thinking = delta.get("thinking", "")
            if thinking:
                return (make_openai_chunk(req_id, model, {"reasoning_content": thinking}), None)
        elif delta_type == "input_json_delta":
            partial_args = delta.get("partial_json", "")
            tool_call = {
                "index": event.get("index", 0),
                "function": {"arguments": partial_args},
            }
            return (make_openai_chunk(req_id, model, {"tool_calls": [tool_call]}), None)
        return None

    # 3. Message Delta / Stop
    if event_type == "message_delta":
        delta_info = event.get("delta", {})
        stop_reason = delta_info.get("stop_reason")
        openai_finish = "stop"
        if stop_reason == "tool_use":
            openai_finish = "tool_calls"
        elif stop_reason == "max_tokens":
            openai_finish = "length"
        return (make_openai_chunk(req_id, model, {}, finish_reason=openai_finish), openai_finish)

    if event_type == "message_stop":
        return ("[DONE]", "stop")

    if "choices" in event:
        return (json.dumps(event), None)

    return None
