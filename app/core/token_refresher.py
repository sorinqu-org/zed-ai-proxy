import base64
import json
import logging
import time
from typing import Optional, Dict, Any
import httpx
from app.config import settings

logger = logging.getLogger("zed_proxy.token_refresher")


def decode_jwt_exp(token: str) -> Optional[int]:
    """Extracts expiration timestamp (exp) from JWT token without secret verification."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        padding = "=" * ((4 - len(parts[1]) % 4) % 4)
        payload_bytes = base64.urlsafe_b64decode(parts[1] + padding)
        payload = json.loads(payload_bytes.decode("utf-8"))
        return payload.get("exp")
    except Exception as e:
        logger.debug(f"Failed to decode JWT expiration: {e}")
        return None


class TokenRefresher:
    def __init__(self, zed_api_url: Optional[str] = None, zed_version: Optional[str] = None):
        self.zed_api_url = zed_api_url or settings.zed_api_url
        self.zed_version = zed_version or settings.zed_version

    async def fetch_llm_token(
        self,
        user_id: str,
        access_token: str,
        organization_id: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """Calls POST /client/llm_tokens on cloud.zed.dev to obtain an ephemeral LLM token."""
        url = f"{self.zed_api_url}/client/llm_tokens"
        headers = {
            "Authorization": f"{user_id} {access_token}",
            "Content-Type": "application/json",
            "x-zed-version": self.zed_version,
            "User-Agent": f"Zed/{self.zed_version}",
        }
        body = {"organization_id": organization_id}

        async def _do_post(c: httpx.AsyncClient) -> httpx.Response:
            return await c.post(url, headers=headers, json=body, timeout=15.0)

        if client:
            resp = await _do_post(client)
        else:
            async with httpx.AsyncClient() as c:
                resp = await _do_post(c)

        if resp.status_code != 200:
            safe_err = resp.text.replace("\n", " ").replace("\r", " ")[:200]
            logger.error(
                f"Failed to fetch LLM token for user_id={user_id}: "
                f"HTTP {resp.status_code} - {safe_err}"
            )
            resp.raise_for_status()

        data = resp.json()
        token = data.get("token")
        if not token:
            raise ValueError(f"Invalid response from Zed API: missing 'token' in {data}")

        exp = decode_jwt_exp(token)
        if not exp:
            exp = int(time.time()) + 3600

        return {
            "token": token,
            "expires_at": exp,
        }
