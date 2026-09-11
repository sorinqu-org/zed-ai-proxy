import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

from app.config import settings
from app.core.token_refresher import TokenRefresher

logger = logging.getLogger("zed_proxy.account_pool")


class Account(BaseModel):
    id: str
    name: str = ""
    user_id: str
    access_token: str
    organization_id: Optional[str] = None
    current_llm_token: Optional[str] = None
    expires_at: Optional[int] = None
    status: str = "active"  # active, cooldown, unauthorized, disabled
    cooldown_until: Optional[float] = None
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    last_error: Optional[str] = None
    last_used: Optional[float] = None

    def is_available(self) -> bool:
        if self.status in ("unauthorized", "disabled"):
            return False
        if self.status == "cooldown":
            if self.cooldown_until and time.time() < self.cooldown_until:
                return False
            # Cooldown expired, restore active
            self.status = "active"
            self.cooldown_until = None
        return True

    def is_token_expiring(self, threshold_seconds: int = 300) -> bool:
        if not self.current_llm_token or not self.expires_at:
            return True
        return time.time() + threshold_seconds >= self.expires_at


class AccountPool:
    def __init__(self, token_refresher: Optional[TokenRefresher] = None):
        self.accounts: List[Account] = []
        self._current_index: int = 0
        self._lock = asyncio.Lock()
        self.refresher = token_refresher or TokenRefresher()
        self._bg_task: Optional[asyncio.Task] = None

    def load_from_file(self, path: Path) -> None:
        if not path.exists():
            logger.warning(f"Accounts file {path} not found.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.load_from_dict(data)
            logger.info(f"Loaded {len(self.accounts)} accounts from {path}")
        except Exception as e:
            logger.error(f"Failed to load accounts from {path}: {e}")

    def load_from_dict(self, data: Any) -> None:
        raw_list = data if isinstance(data, list) else data.get("accounts", [])
        self.accounts = []
        for i, item in enumerate(raw_list):
            acc_id = str(item.get("id") or item.get("user_id") or f"acc-{i+1}")
            account = Account(
                id=acc_id,
                name=item.get("name", f"Account-{acc_id}"),
                user_id=str(item.get("user_id", "")),
                access_token=str(item.get("access_token", "")),
                organization_id=item.get("organization_id"),
                current_llm_token=item.get("current_llm_token"),
                expires_at=item.get("expires_at"),
                status=item.get("status", "active"),
            )
            self.accounts.append(account)

    async def get_next_account(self) -> Optional[Account]:
        """Round-robin selection of an available account with valid LLM token."""
        async with self._lock:
            if not self.accounts:
                return None

            available = [a for a in self.accounts if a.is_available()]
            if not available:
                # Check if all accounts are in cooldown; return earliest recovering account
                cooldowns = [a for a in self.accounts if a.status == "cooldown" and a.cooldown_until]
                if cooldowns:
                    cooldowns.sort(key=lambda a: a.cooldown_until or 0)
                    earliest = cooldowns[0]
                    wait_time = (earliest.cooldown_until or 0) - time.time()
                    if wait_time <= 5.0:
                        # Small wait, let it recover
                        await asyncio.sleep(max(0.1, wait_time))
                        earliest.status = "active"
                        earliest.cooldown_until = None
                        return earliest
                return None

            # Pick next round-robin
            self._current_index = (self._current_index + 1) % len(available)
            account = available[self._current_index]
            account.last_used = time.time()
            account.total_requests += 1

            # Ensure token is valid
            if account.is_token_expiring(settings.token_refresh_threshold_seconds):
                try:
                    res = await self.refresher.fetch_llm_token(
                        account.user_id, account.access_token, account.organization_id
                    )
                    account.current_llm_token = res["token"]
                    account.expires_at = res["expires_at"]
                    account.status = "active"
                except Exception as e:
                    logger.error(f"Failed to refresh token for account {account.id}: {e}")
                    account.last_error = str(e)
                    # If this one fails, try another account
                    if len(available) > 1:
                        return await self.get_next_account()

            return account

    def mark_rate_limited(self, account_id: str, cooldown_seconds: Optional[int] = None) -> None:
        cd = cooldown_seconds or settings.rate_limit_cooldown_seconds
        for a in self.accounts:
            if a.id == account_id:
                a.status = "cooldown"
                a.cooldown_until = time.time() + cd
                a.failed_requests += 1
                a.last_error = f"Rate limited. Cooling down for {cd}s"
                logger.warning(f"Account {account_id} marked as rate limited for {cd}s")
                break

    def mark_unauthorized(self, account_id: str, error_msg: str) -> None:
        for a in self.accounts:
            if a.id == account_id:
                a.status = "unauthorized"
                a.failed_requests += 1
                a.last_error = error_msg
                logger.error(f"Account {account_id} unauthorized: {error_msg}")
                break

    def mark_success(self, account_id: str) -> None:
        for a in self.accounts:
            if a.id == account_id:
                a.successful_requests += 1
                a.last_error = None
                break

    async def refresh_all_tokens(self) -> None:
        """Periodic background worker to refresh expiring tokens."""
        for a in self.accounts:
            if a.status == "unauthorized" or a.status == "disabled":
                continue
            if a.is_token_expiring(settings.token_refresh_threshold_seconds):
                try:
                    res = await self.refresher.fetch_llm_token(
                        a.user_id, a.access_token, a.organization_id
                    )
                    a.current_llm_token = res["token"]
                    a.expires_at = res["expires_at"]
                    logger.info(f"Refreshed token for account {a.id}, expires at {a.expires_at}")
                except Exception as e:
                    logger.error(f"Background refresh failed for {a.id}: {e}")
                    a.last_error = str(e)

    async def start_background_loop(self) -> None:
        async def _loop():
            while True:
                try:
                    await self.refresh_all_tokens()
                except Exception as e:
                    logger.error(f"Error in token refresher loop: {e}")
                await asyncio.sleep(settings.token_refresh_interval_seconds)

        self._bg_task = asyncio.create_task(_loop())

    def stop_background_loop(self) -> None:
        if self._bg_task:
            self._bg_task.cancel()

    def get_status_summary(self) -> Dict[str, Any]:
        now = time.time()
        accounts_info = []
        for a in self.accounts:
            exp_remaining = (a.expires_at - now) if a.expires_at else None
            cd_remaining = (a.cooldown_until - now) if a.cooldown_until and a.cooldown_until > now else 0
            accounts_info.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "user_id": a.user_id,
                    "status": a.status,
                    "has_token": bool(a.current_llm_token),
                    "token_expires_in_seconds": int(exp_remaining) if exp_remaining is not None else None,
                    "cooldown_remaining_seconds": int(cd_remaining),
                    "total_requests": a.total_requests,
                    "successful_requests": a.successful_requests,
                    "failed_requests": a.failed_requests,
                    "last_error": a.last_error,
                    "last_used": a.last_used,
                }
            )

        active_count = sum(1 for a in self.accounts if a.is_available())
        return {
            "total_accounts": len(self.accounts),
            "available_accounts": active_count,
            "accounts": accounts_info,
        }
