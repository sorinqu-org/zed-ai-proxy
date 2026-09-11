import logging
from typing import Any, Dict, List
from app.config import settings

logger = logging.getLogger("zed_proxy.payload_filter")


def sanitize_message_content(content: Any, max_size_bytes: int = settings.max_image_size_bytes) -> Any:
    """Sanitizes image payloads or oversized binary blobs in message contents."""
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
        new_dict = {}
        for k, v in content.items():
            if k in ("image_url", "image", "source") and isinstance(v, dict):
                # OpenAI / Anthropic image block structures
                url = v.get("url") or v.get("data")
                if url and isinstance(url, str) and len(url) > max_size_bytes:
                    logger.info(f"Stripped image block '{k}' of size {len(url)} bytes")
                    new_dict["type"] = "text"
                    new_dict["text"] = f"[Image omitted: size was {len(url)} bytes]"
                    continue
            new_dict[k] = sanitize_message_content(v, max_size_bytes)
        return new_dict

    return content


def filter_payload(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Recursively processes and filters the messages payload."""
    cleaned = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        cleaned_msg = dict(msg)
        if "content" in cleaned_msg:
            cleaned_msg["content"] = sanitize_message_content(cleaned_msg["content"])
        cleaned.append(cleaned_msg)
    return cleaned
