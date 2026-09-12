import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel

from app.config import settings
from app.core.token_refresher import TokenRefresher
from app.core.billing import (
    build_billing_url,
    calculate_cost,
    fetch_zed_account_details,
    fetch_live_orb_billing,
    fetch_orb_portal_billing,
    PLAN_DEFAULT_LIMITS,
)

logger = logging.getLogger("zed_proxy.account_pool")


class Account(BaseModel):
    id: str
    name: str = ""
    user_id: str
    access_token: str
    organization_id: Optional[str] = None
    org_name: Optional[str] = None
    plan: str = "zed_pro"
    billing_url: Optional[str] = None
    orb_portal_url: Optional[str] = None
    orb_token: Optional[str] = None
    session_cookie: Optional[str] = None
    spend_limit: float = 10.00
    spend_used: float = 0.00
    balance_remaining: float = 10.00
    tokens_used: int = 0
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    cache_write_tokens_total: int = 0
    cache_read_tokens_total: int = 0
    model_stats: Dict[str, Dict[str, Any]] = {}
    model_requests_used: int = 0
    model_requests_limit: Optional[int] = None
    last_billing_sync: Optional[float] = None
    current_llm_token: Optional[str] = None
    expires_at: Optional[int] = None
    status: str = "active"  # active, cooldown, unauthorized, disabled
    cooldown_until: Optional[float] = None
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    last_error: Optional[str] = None
    last_used: Optional[float] = None

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> float:
        cost = calculate_cost(
            model,
            input_tokens,
            output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        total_tok = input_tokens + output_tokens + cache_write_tokens + cache_read_tokens
        self.tokens_used += total_tok
        self.input_tokens_total += input_tokens
        self.output_tokens_total += output_tokens
        self.cache_write_tokens_total += cache_write_tokens
        self.cache_read_tokens_total += cache_read_tokens
        self.spend_used += cost
        self.balance_remaining = max(0.0, round(self.spend_limit - self.spend_used, 4))
        self.last_used = time.time()

        # Update per-model breakdown
        norm_model = (model or "unknown").strip()
        if not norm_model:
            norm_model = "unknown"
        if norm_model not in self.model_stats:
            self.model_stats[norm_model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
                "requests_count": 0,
                "cost_usd": 0.0,
                "last_used": None,
            }
        st = self.model_stats[norm_model]
        st["input_tokens"] += input_tokens
        st["output_tokens"] += output_tokens
        st["cache_write_tokens"] += cache_write_tokens
        st["cache_read_tokens"] += cache_read_tokens
        st["total_tokens"] += total_tok
        st["requests_count"] += 1
        st["cost_usd"] = round(st["cost_usd"] + cost, 4)
        st["last_used"] = time.time()

        return cost

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
        existing_map = {a.id: a for a in self.accounts}
        existing_uid_map = {a.user_id: a for a in self.accounts if a.user_id}
        new_accounts = []
        for i, item in enumerate(raw_list):
            acc_id = str(item.get("id") or item.get("user_id") or f"acc-{i+1}")
            user_id = str(item.get("user_id", ""))
            raw_tok = item.get("access_token", "")
            tok_str = json.dumps(raw_tok) if isinstance(raw_tok, dict) else str(raw_tok)
            prev = existing_map.get(acc_id) or existing_uid_map.get(user_id)

            cur_token = item.get("current_llm_token")
            exp_at = item.get("expires_at")
            tot_req = 0
            succ_req = 0
            fail_req = 0
            tokens_used = int(item.get("tokens_used", 0))
            spend_used = float(item.get("spend_used", 0.00))
            plan = item.get("plan", "zed_vip" if item.get("organization_id") == "org_01m24akygwy207azwerfw280fe" else "zed_pro")
            spend_limit = float(item.get("spend_limit", PLAN_DEFAULT_LIMITS.get(plan, 10.00)))
            balance_rem = float(item.get("balance_remaining", max(0.0, spend_limit - spend_used)))
            org_id = item.get("organization_id")
            org_name = item.get("org_name")
            session_cookie = item.get("session_cookie") or item.get("cookie")
            billing_url = item.get("billing_url") or build_billing_url(org_id)
            orb_portal_url = item.get("orb_portal_url")
            orb_token = item.get("orb_token")
            in_tok_tot = int(item.get("input_tokens_total", 0))
            out_tok_tot = int(item.get("output_tokens_total", 0))
            cw_tok_tot = int(item.get("cache_write_tokens_total", 0))
            cr_tok_tot = int(item.get("cache_read_tokens_total", 0))
            m_stats = dict(item.get("model_stats", {}))

            if prev and prev.access_token == tok_str:
                cur_token = cur_token or prev.current_llm_token
                exp_at = exp_at or prev.expires_at
                tot_req = prev.total_requests
                succ_req = prev.successful_requests
                fail_req = prev.failed_requests
                tokens_used = prev.tokens_used
                spend_used = prev.spend_used
                spend_limit = prev.spend_limit
                balance_rem = prev.balance_remaining
                org_name = org_name or prev.org_name
                billing_url = billing_url or prev.billing_url
                orb_portal_url = orb_portal_url or getattr(prev, "orb_portal_url", None)
                orb_token = orb_token or getattr(prev, "orb_token", None)
                in_tok_tot = in_tok_tot or getattr(prev, "input_tokens_total", 0)
                out_tok_tot = out_tok_tot or getattr(prev, "output_tokens_total", 0)
                cw_tok_tot = cw_tok_tot or getattr(prev, "cache_write_tokens_total", 0)
                cr_tok_tot = cr_tok_tot or getattr(prev, "cache_read_tokens_total", 0)
                m_stats = m_stats or getattr(prev, "model_stats", {})

            account = Account(
                id=acc_id,
                name=item.get("name", f"Account-{acc_id}"),
                user_id=user_id,
                access_token=tok_str,
                organization_id=org_id,
                org_name=org_name,
                plan=plan,
                billing_url=billing_url,
                orb_portal_url=orb_portal_url,
                orb_token=orb_token,
                session_cookie=session_cookie,
                spend_limit=spend_limit,
                spend_used=spend_used,
                balance_remaining=balance_rem,
                tokens_used=tokens_used,
                input_tokens_total=in_tok_tot,
                output_tokens_total=out_tok_tot,
                cache_write_tokens_total=cw_tok_tot,
                cache_read_tokens_total=cr_tok_tot,
                model_stats=m_stats,
                current_llm_token=cur_token,
                expires_at=exp_at,
                status=item.get("status", "active"),
                total_requests=tot_req,
                successful_requests=succ_req,
                failed_requests=fail_req,
            )
            new_accounts.append(account)
        self.accounts = new_accounts

    def _enrich_account_billing_sync(self, account: Account) -> None:
        try:
            details = fetch_zed_account_details(account.user_id, account.access_token, settings.zed_api_url)
            if details:
                if not account.organization_id and details.get("organization_id"):
                    account.organization_id = details["organization_id"]
                if not account.org_name and details.get("org_name"):
                    account.org_name = details["org_name"]
                if details.get("plan"):
                    account.plan = details["plan"]
                    if account.spend_limit == 10.0 and details["plan"] == "zed_vip":
                        account.spend_limit = 100.00
                        account.balance_remaining = max(0.0, account.spend_limit - account.spend_used)
                account.billing_url = build_billing_url(account.organization_id)
                if details.get("model_requests_used") is not None:
                    account.model_requests_used = details["model_requests_used"]
                if details.get("model_requests_limit") is not None:
                    account.model_requests_limit = details["model_requests_limit"]

            if account.session_cookie and account.organization_id:
                live = fetch_live_orb_billing(account.organization_id, account.session_cookie, settings.zed_api_url)
                if live:
                    if live.get("spend_used") is not None:
                        account.spend_used = live["spend_used"]
                    if live.get("spend_limit") is not None:
                        account.spend_limit = live["spend_limit"]
                    if live.get("balance_remaining") is not None:
                        account.balance_remaining = live["balance_remaining"]
                    account.last_billing_sync = time.time()

            if account.orb_portal_url or account.orb_token:
                orb_info = fetch_orb_portal_billing(account.orb_portal_url or account.orb_token)
                if orb_info:
                    if orb_info.get("balance_remaining") is not None:
                        account.balance_remaining = orb_info["balance_remaining"]
                    if orb_info.get("spend_limit") is not None:
                        account.spend_limit = orb_info["spend_limit"]
                    if orb_info.get("spend_used") is not None:
                        account.spend_used = orb_info["spend_used"]
                    if orb_info.get("customer_name") and not account.org_name:
                        account.org_name = orb_info["customer_name"]
                    account.last_billing_sync = time.time()
        except Exception as e:
            logger.debug(f"Enrich account billing failed for {account.id}: {e}")

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
                # Auto-enrich account metadata and live billing if needed
                if not account.organization_id or not account.org_name or account.session_cookie:
                    self._enrich_account_billing_sync(account)

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
                    "organization_id": a.organization_id,
                    "org_name": a.org_name,
                    "plan": a.plan,
                    "billing_url": a.billing_url,
                    "orb_portal_url": a.orb_portal_url,
                    "spend_limit": round(a.spend_limit, 2),
                    "spend_used": round(a.spend_used, 4),
                    "balance_remaining": round(a.balance_remaining, 2),
                    "tokens_used": a.tokens_used,
                    "input_tokens_total": a.input_tokens_total,
                    "output_tokens_total": a.output_tokens_total,
                    "cache_write_tokens_total": a.cache_write_tokens_total,
                    "cache_read_tokens_total": a.cache_read_tokens_total,
                    "model_stats": a.model_stats,
                    "model_requests_used": a.model_requests_used,
                    "model_requests_limit": a.model_requests_limit,
                }
            )

        available_count = sum(1 for a in self.accounts if a.is_available())
        cooldown_count = sum(1 for a in self.accounts if a.status == "cooldown")
        return {
            "total_accounts": len(self.accounts),
            "available_accounts": available_count,
            "active_accounts": available_count,
            "cooldown_accounts": cooldown_count,
            "total_balance_remaining": round(self.get_total_balance_remaining(), 2),
            "total_spend_limit": round(self.get_total_spend_limit(), 2),
            "total_spend_used": round(self.get_total_spend_used(), 4),
            "total_tokens_used": self.get_total_tokens_used(),
            "total_model_stats": self.get_total_model_stats(),
            "accounts": accounts_info,
        }

    def add_account(self, account: Account) -> None:
        """Add an account to the pool."""
        self.accounts.append(account)

    def get_account(self, account_id: str) -> Optional[Account]:
        for a in self.accounts:
            if a.id == account_id or str(a.user_id) == str(account_id):
                return a
        return None

    def get_total_model_stats(self) -> Dict[str, Dict[str, Any]]:
        totals: Dict[str, Dict[str, Any]] = {}
        for a in self.accounts:
            for m_name, st in a.model_stats.items():
                if m_name not in totals:
                    totals[m_name] = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_write_tokens": 0,
                        "cache_read_tokens": 0,
                        "total_tokens": 0,
                        "requests_count": 0,
                        "cost_usd": 0.0,
                    }
                t = totals[m_name]
                t["input_tokens"] += st.get("input_tokens", 0)
                t["output_tokens"] += st.get("output_tokens", 0)
                t["cache_write_tokens"] += st.get("cache_write_tokens", 0)
                t["cache_read_tokens"] += st.get("cache_read_tokens", 0)
                t["total_tokens"] += st.get("total_tokens", 0)
                t["requests_count"] += st.get("requests_count", 0)
                t["cost_usd"] = round(t["cost_usd"] + st.get("cost_usd", 0.0), 4)
        return totals

    def get_total_balance_remaining(self) -> float:
        return sum(a.balance_remaining for a in self.accounts if a.is_available())

    def get_total_spend_limit(self) -> float:
        return sum(a.spend_limit for a in self.accounts if a.is_available())

    def get_total_spend_used(self) -> float:
        return sum(a.spend_used for a in self.accounts)

    def get_total_tokens_used(self) -> int:
        return sum(a.tokens_used for a in self.accounts)
