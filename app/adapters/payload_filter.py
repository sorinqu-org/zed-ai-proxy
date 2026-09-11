import logging
from typing import Any, Dict, List, Optional
from app.config import settings

logger = logging.getLogger("zed_proxy.payload_filter")


def sanitize_message_content(content: Any, max_size_bytes: int = settings.max_image_size_bytes) -> Any:
    """Sanitizes image payloads or oversized binary blobs in message contents."""
    if content is None:
        return ""

    if isinstance(content, str):
        # Check for base64 image data URIs
        if content.startswith("data:image/") and ";base64," in content:
            if len(content) > max_size_bytes:
                logger.info(f"Stripped large base64 image of size {len(content)} bytes")
                return f"[Image omitted: size was {len(content)} bytes]"
        return content

    if isinstance(content, list):
        sanitized = []
        for item in content:
            sanitized.append(sanitize_message_content(item, max_size_bytes))
        return sanitized

    if isinstance(content, dict):
        # Handle OpenAI / Anthropic image block structures
        block_type = content.get("type")
        if block_type in ("image_url", "image") or "source" in content or "image_url" in content:
            # Check size
            source_data = ""
            if "image_url" in content and isinstance(content["image_url"], dict):
                source_data = content["image_url"].get("url", "")
            elif "source" in content and isinstance(content["source"], dict):
                source_data = content["source"].get("data", "")

            if source_data and len(source_data) > max_size_bytes:
                logger.info(f"Stripped oversized image block of size {len(source_data)} bytes")
                return {
                    "type": "text",
                    "text": f"[Image omitted: size was {len(source_data)} bytes]"
                }

        # Otherwise recursively sanitize remaining fields
        new_dict = {}
        for k, v in content.items():
            new_dict[k] = sanitize_message_content(v, max_size_bytes)
        return new_dict

    return content


def filter_payload(messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Recursively processes and filters the messages payload."""
    if not messages:
        return []
    cleaned = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        cleaned_msg = dict(msg)
        if "content" in cleaned_msg:
            cleaned_msg["content"] = sanitize_message_content(cleaned_msg["content"])
        cleaned.append(cleaned_msg)
    return cleaned
