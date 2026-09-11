import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel

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
            self.status = "active"
            self.cooldown_until = None
            return True
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
        self._refresh_tasks: Dict[str, asyncio.Task] = {}

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
        new_accounts = []
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
            new_accounts.append(account)
        self.accounts = new_accounts

    async def _ensure_token_refreshed(self, account: Account) -> bool:
        """Deduplicated token refresh per account outside of the global pool lock."""
        account_id = account.id

        # If a refresh task is already in-flight for this account, await it (thundering herd defense)
        if account_id in self._refresh_tasks:
            try:
                await self._refresh_tasks[account_id]
                return bool(account.current_llm_token and not account.is_token_expiring(settings.token_refresh_threshold_seconds))
            except Exception:
                return False

        async def _do_refresh():
            try:
                res = await self.refresher.fetch_llm_token(
                    account.user_id, account.access_token, account.organization_id
                )
                account.current_llm_token = res["token"]
                account.expires_at = res["expires_at"]
                account.status = "active"
                account.last_error = None
                return True
            except Exception as e:
                logger.error(f"Failed to refresh token for account {account.id}: {e}")
                account.last_error = str(e)
                return False
            finally:
                self._refresh_tasks.pop(account_id, None)

        task = asyncio.create_task(_do_refresh())
        self._refresh_tasks[account_id] = task
        return await task

    async def get_next_account(self) -> Optional[Account]:
        """Iterative, non-blocking round-robin selection of an account with valid token."""
        for _ in range(max(1, len(self.accounts))):
            account = None
            async with self._lock:
                if not self.accounts:
                    return None

                # Reset cooldown if expired
                now = time.time()
                for a in self.accounts:
                    if a.status == "cooldown" and a.cooldown_until and now >= a.cooldown_until:
                        a.status = "active"
                        a.cooldown_until = None

                available = [a for a in self.accounts if a.is_available()]
                if not available:
                    return None

                self._current_index = (self._current_index + 1) % len(available)
                account = available[self._current_index]
                account.last_used = now
                account.total_requests += 1

            # Network I/O and refresh happen OUTSIDE the global lock
            if account.is_token_expiring(settings.token_refresh_threshold_seconds):
                success = await self._ensure_token_refreshed(account)
                if not success:
                    # Token refresh failed, continue loop to try another account
                    continue

            return account

        return None

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
        tasks = []
        for a in self.accounts:
            if a.status in ("unauthorized", "disabled"):
                continue
            if a.is_token_expiring(settings.token_refresh_threshold_seconds):
                tasks.append(self._ensure_token_refreshed(a))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def start_background_loop(self) -> None:
        if self._bg_task and not self._bg_task.done():
            return

        async def _loop():
            while True:
                try:
                    await self.refresh_all_tokens()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in token refresher loop: {e}")
                await asyncio.sleep(settings.token_refresh_interval_seconds)

        self._bg_task = asyncio.create_task(_loop())

    async def stop_background_loop(self) -> None:
        if self._bg_task:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None

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
