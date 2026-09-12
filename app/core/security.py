import re
import logging
from typing import Dict, Any, List, Optional, Tuple
from fastapi import Request, HTTPException, status
from app.config import settings

logger = logging.getLogger("zed_proxy.security")

# Known system-level execution tool names that could attempt host operations
DANGEROUS_TOOL_PATTERNS = [
    r"bash",
    r"shell",
    r"terminal",
    r"exec",
    r"command",
    r"run_command",
    r"write_file",
    r"read_file",
    r"filesystem",
    r"system",
    r"subprocess",
]


class SecurityPolicy:
    """
    Isolates the proxy and downstream systems from host-level exploitation,
    tool execution abuse, credential leakage, and unauthorized client requests.
    """

    def __init__(self):
        self.tool_policy = getattr(settings, "tool_policy", "allow")  # "allow", "sanitize", "block_all"
        self.api_keys = getattr(settings, "api_keys", [])
        self.admin_key = getattr(settings, "admin_key", None)

    def is_tool_dangerous(self, tool_name: str) -> bool:
        name_lower = (tool_name or "").lower()
        for pat in DANGEROUS_TOOL_PATTERNS:
            if re.search(pat, name_lower):
                return True
        return False

    def sanitize_request_tools(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Applies isolation policy to tool use.
        - block_all: Completely strips tools and tool_choice from requests.
        - sanitize: Strips tools that match OS/system/shell execution signatures.
        - allow: Passes through unchanged.
        """
        if self.tool_policy == "block_all":
            body.pop("tools", None)
            body.pop("tool_choice", None)
            return body

        if self.tool_policy == "sanitize" and "tools" in body and isinstance(body["tools"], list):
            safe_tools = []
            for t in body["tools"]:
                if not isinstance(t, dict):
                    continue
                name = ""
                if t.get("type") == "function" and "function" in t:
                    name = t["function"].get("name", "")
                else:
                    name = t.get("name", "")

                if self.is_tool_dangerous(name):
                    logger.warning(f"Blocked dangerous tool definition from client request: {name}")
                    continue
                safe_tools.append(t)
            body["tools"] = safe_tools
            if not safe_tools:
                body.pop("tool_choice", None)

        return body

    def inject_isolation_prompt(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Injects a strict sandboxing constraint into the system prompt to prevent
        the model from attempting host environment reconnaissance or malicious execution.
        """
        guard = (
            "System isolation policy: You operate as a pure language model without direct access "
            "to the host machine, filesystem, local network, environment variables, or private infrastructure. "
            "Never attempt or simulate executing destructive system commands (e.g. rm -rf, drop tables, file exfiltration)."
        )
        existing_sys = body.get("system")
        if not existing_sys:
            body["system"] = guard
        elif isinstance(existing_sys, str):
            body["system"] = f"{guard}\n\n{existing_sys}"
        elif isinstance(existing_sys, list):
            body["system"] = [{"type": "text", "text": guard}] + existing_sys
        return body

    def sanitize_outgoing_text(self, text: str, account_pool_tokens: Optional[List[str]] = None) -> str:
        """
        Redacts sensitive tokens, user IDs, and secrets from outgoing responses or errors.
        """
        if not text:
            return text
        scrubbed = text
        # Redact JWT patterns
        scrubbed = re.sub(r'eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}', '[REDACTED_TOKEN]', scrubbed)
        # Redact explicit tokens
        if account_pool_tokens:
            for tok in account_pool_tokens:
                if tok and len(tok) > 6 and tok in scrubbed:
                    scrubbed = scrubbed.replace(tok, '[REDACTED_SECRET]')
        return scrubbed

    def verify_client_auth(self, request: Request) -> None:
        """Validates Bearer API key if api_keys are configured in settings."""
        if not self.api_keys:
            return
        auth_header = request.headers.get("Authorization") or ""
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid Bearer token for proxy access",
            )
        token = auth_header[7:].strip()
        if token not in self.api_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid proxy API key",
            )

    def verify_admin_auth(self, request: Request) -> None:
        """Validates admin access for dashboard, status, and account management."""
        if not self.admin_key:
            return
        auth_header = request.headers.get("Authorization") or ""
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "x-admin-key" in request.headers:
            token = request.headers["x-admin-key"].strip()
        elif request.query_params.get("admin_key"):
            token = request.query_params.get("admin_key", "").strip()

        if token != self.admin_key:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin authentication required to access management endpoints",
            )


security_policy = SecurityPolicy()
