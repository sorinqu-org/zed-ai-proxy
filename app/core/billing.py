import json
import logging
import re
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, Tuple, List

logger = logging.getLogger("zed_proxy.billing")

# Standard token pricing per 1M tokens in USD ($)
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Anthropic models
    "claude-opus-5": {"input": 15.0, "output": 75.0},
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0},
    "claude-3-7-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-haiku-5": {"input": 0.80, "output": 4.0},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.0},
    # OpenAI models
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o1": {"input": 15.0, "output": 60.0},
    "o3-mini": {"input": 1.10, "output": 4.40},
}

DEFAULT_PRICING = {"input": 3.0, "output": 15.0}

PLAN_DEFAULT_LIMITS: Dict[str, float] = {
    "zed_vip": 100.00,
    "zed_business": 50.00,
    "zed_pro": 10.00,
    "zed_free": 0.00,
}


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Calculates estimated cost in USD based on model pricing per 1M tokens, including cache creation and read."""
    pricing = DEFAULT_PRICING
    model_lower = (model or "").lower()
    for key, p in MODEL_PRICING.items():
        if key in model_lower:
            pricing = p
            break
    base_in = pricing["input"]
    cost_in = (input_tokens / 1_000_000.0) * base_in
    cost_out = (output_tokens / 1_000_000.0) * pricing["output"]
    cost_cache_write = (cache_write_tokens / 1_000_000.0) * (base_in * 1.25)
    cost_cache_read = (cache_read_tokens / 1_000_000.0) * (base_in * 0.10)
    return cost_in + cost_out + cost_cache_write + cost_cache_read


def format_currency(amount: Optional[float]) -> str:
    """Formats amount as $XX.XX."""
    if amount is None:
        return "$0.00"
    return f"${amount:.2f}"


def format_tokens(count: Optional[int]) -> str:
    """Formats token count with thousands separator or shorthand."""
    if not count:
        return "0"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def build_billing_url(org_id: Optional[str]) -> str:
    """Constructs dashboard billing usage URL."""
    if org_id:
        return f"https://dashboard.zed.dev/{org_id}/billing/usage"
    return "https://dashboard.zed.dev/billing/usage"


def fetch_zed_account_details(user_id: str, access_token: str, zed_api_url: str = "https://cloud.zed.dev") -> Dict[str, Any]:
    """
    Fetches user profile, organizations, and plans from GET /client/users/me.
    Returns normalized metadata including chosen organization, plan tier, and org name.
    """
    url = f"{zed_api_url.rstrip('/')}/client/users/me"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"{user_id} {access_token}",
            "User-Agent": "Zed/0.176.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Could not fetch account details for user {user_id}: {e}")
        return {}

    orgs = data.get("organizations") or []
    plans_by_org = data.get("plans_by_organization") or {}
    default_org_id = data.get("default_organization_id")
    personal_plan = data.get("plan") or {}

    # Rank orgs by best plan (zed_vip > zed_business > zed_pro > zed_free)
    tier_ranks = {"zed_vip": 4, "zed_business": 3, "zed_pro": 2, "zed_free": 1}

    best_org_id = default_org_id
    best_rank = 0
    org_name_map = {}

    for org in orgs:
        oid = org.get("id")
        oname = org.get("name", "")
        if oid:
            org_name_map[oid] = oname
            tier = plans_by_org.get(oid, "zed_free")
            rank = tier_ranks.get(tier, 0)
            if rank > best_rank or (rank == best_rank and oid == default_org_id):
                best_rank = rank
                best_org_id = oid

    if not best_org_id and orgs:
        best_org_id = orgs[0].get("id")

    chosen_plan = plans_by_org.get(best_org_id) if best_org_id else personal_plan.get("plan", "zed_free")
    chosen_org_name = org_name_map.get(best_org_id, "")

    model_reqs = personal_plan.get("usage", {}).get("model_requests", {})
    req_used = model_reqs.get("used", 0)
    req_limit = model_reqs.get("limit", {}).get("limited")

    return {
        "organization_id": best_org_id,
        "org_name": chosen_org_name,
        "plan": chosen_plan or "zed_free",
        "billing_url": build_billing_url(best_org_id),
        "organizations": orgs,
        "plans_by_organization": plans_by_org,
        "model_requests_used": req_used,
        "model_requests_limit": req_limit,
    }


def fetch_live_orb_billing(
    org_id: str, session_cookie: str, zed_api_url: str = "https://cloud.zed.dev"
) -> Optional[Dict[str, Any]]:
    """
    Fetches live token usage and spending from Zed's frontend billing API if a session cookie is present.
    Endpoint: GET /frontend/organizations/{org_id}/billing/usage
    """
    if not session_cookie or not org_id:
        return None

    url = f"{zed_api_url.rstrip('/')}/frontend/organizations/{org_id}/billing/usage"
    cookie_str = f"session={session_cookie}; _session={session_cookie}" if "=" not in session_cookie else session_cookie
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie_str,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Zed AI Proxy)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            current_usage = data.get("current_usage") or {}
            token_spend = current_usage.get("token_spend") or {}
            spend_in_cents = token_spend.get("spend_in_cents", 0)
            limit_in_cents = token_spend.get("limit_in_cents")

            spend_usd = spend_in_cents / 100.0
            limit_usd = (limit_in_cents / 100.0) if limit_in_cents is not None else None

            return {
                "spend_used": spend_usd,
                "spend_limit": limit_usd,
                "balance_remaining": (limit_usd - spend_usd) if limit_usd is not None else None,
                "plan": data.get("plan"),
            }
    except Exception as e:
        logger.debug(f"Live Orb billing query failed for org {org_id}: {e}")
        return None


_ORB_USAGE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def fetch_orb_portal_token_usage(
    token: str,
    headers: Dict[str, str],
    force_refresh: bool = False,
    cache_ttl_seconds: float = 45.0,
) -> Dict[str, Any]:
    """
    Fetches exact per-model token consumption directly from WithOrb subscription usage APIs.
    Returns per-model breakdown (input, output, cache write, cache read, cost) and totals.
    """
    now = time.time()
    if not force_refresh and token in _ORB_USAGE_CACHE:
        cached_time, cached_val = _ORB_USAGE_CACHE[token]
        if now - cached_time < cache_ttl_seconds:
            return cached_val

    url_sub = f"https://portal.withorb.com/api/v1/subscriptions_from_link?token={token}"
    req_sub = urllib.request.Request(url_sub, headers=headers)
    with urllib.request.urlopen(req_sub, timeout=15.0) as resp:
        sub_data = json.loads(resp.read().decode("utf-8"))

    subs = sub_data.get("data") or []
    if not subs:
        return {}

    sub = subs[0]
    sub_id = sub.get("id")
    price_intervals = sub.get("price_intervals") or []

    prices: List[Tuple[str, str, str, float]] = []
    for pi in price_intervals:
        p_info = pi.get("price", {})
        pid = p_info.get("id")
        p_details = p_info.get("price", {})
        pname = p_details.get("name", "")
        rate_str = p_details.get("unit_config", {}).get("unit_amount")
        if pid and ":" in pname:
            model_name, metric = [x.strip() for x in pname.split(":", 1)]
            try:
                rate = float(rate_str) if rate_str else 0.0
            except (ValueError, TypeError):
                rate = 0.0
            prices.append((pid, model_name, metric, rate))

    if not prices:
        return {}

    def _fetch_single_price_usage(item: Tuple[str, str, str, float]) -> Tuple[str, str, float, float]:
        pid, model, metric, rate = item
        u_url = f"https://portal.withorb.com/api/v1/subscriptions/{sub_id}/usage?price_id={pid}&token={token}"
        r = urllib.request.Request(u_url, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=10.0) as resp_u:
                d = json.loads(resp_u.read().decode("utf-8"))
                raw_tot = d.get("current_total")
                tot = float(raw_tot) if raw_tot is not None else 0.0
                return (model, metric, rate, tot)
        except Exception:
            return (model, metric, rate, 0.0)

    with ThreadPoolExecutor(max_workers=15) as executor:
        results = list(executor.map(_fetch_single_price_usage, prices))

    model_stats: Dict[str, Dict[str, Any]] = {}
    tot_in, tot_out, tot_cw, tot_cr = 0, 0, 0, 0
    tot_cost = 0.0

    for model, metric, rate, tot in results:
        if model not in model_stats:
            model_stats[model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
                "requests_count": 0,
                "cost_usd": 0.0,
            }

        m_data = model_stats[model]
        cost = tot * rate
        m_data["cost_usd"] += cost
        tot_cost += cost

        if "Write" in metric:
            m_data["cache_write_tokens"] += int(tot)
            tot_cw += int(tot)
        elif "Read" in metric or "Cached" in metric:
            m_data["cache_read_tokens"] += int(tot)
            tot_cr += int(tot)
        elif "Output" in metric:
            m_data["output_tokens"] += int(tot)
            tot_out += int(tot)
        elif "Input" in metric:
            m_data["input_tokens"] += int(tot)
            tot_in += int(tot)

        m_data["total_tokens"] = (
            m_data["input_tokens"]
            + m_data["output_tokens"]
            + m_data["cache_write_tokens"]
            + m_data["cache_read_tokens"]
        )
        m_data["cost_usd"] = round(m_data["cost_usd"], 4)

    active_models = {k: v for k, v in model_stats.items() if v["total_tokens"] > 0}
    total_tokens_all = tot_in + tot_out + tot_cw + tot_cr

    res = {
        "model_stats": active_models,
        "input_tokens_total": tot_in,
        "output_tokens_total": tot_out,
        "cache_write_tokens_total": tot_cw,
        "cache_read_tokens_total": tot_cr,
        "total_tokens": total_tokens_all,
        "estimated_cost_total": round(tot_cost, 4),
    }
    _ORB_USAGE_CACHE[token] = (now, res)
    return res


def fetch_orb_portal_billing(
    portal_url_or_token: str,
    fetch_model_usage: bool = True,
    force_refresh: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Fetches live token credits balance from WithOrb billing portal (https://portal.withorb.com).
    Zed Cloud delegates token credit ledger to WithOrb.

    Accepts either full portal URL:
        https://portal.withorb.com/view?token=...
    Or raw token string:
        ImdidERNUWRoUFoyVTc2SkQi.79VapjMJTXCnR2dfDKqoyp5QRlc
    """
    if not portal_url_or_token:
        return None

    match = re.search(r"token=([A-Za-z0-9_\-\.]+)", portal_url_or_token)
    token = match.group(1) if match else portal_url_or_token.strip()
    if not token:
        return None

    headers = {
        "Referer": f"https://portal.withorb.com/view?token={token}",
        "Origin": "https://portal.withorb.com",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    try:
        # Step 1: Query customer metadata and active ledger currency
        url1 = f"https://portal.withorb.com/api/v1/customer_from_link?token={token}"
        req1 = urllib.request.Request(url1, headers=headers)
        with urllib.request.urlopen(req1, timeout=15.0) as resp1:
            data1 = json.loads(resp1.read().decode("utf-8"))

        customer = data1.get("customer") or {}
        customer_id = customer.get("id")
        customer_name = customer.get("name", "")
        currencies = data1.get("account", {}).get("currencies", [])
        pricing_unit_id = currencies[0].get("id") if currencies else (customer.get("pricing_unit") or {}).get("id")

        if not customer_id:
            logger.warning("Orb portal customer_from_link returned no customer ID")
            return None

        # Step 2: Query ledger summary
        puid_param = f"&pricing_unit_id={pricing_unit_id}" if pricing_unit_id else ""
        url2 = f"https://portal.withorb.com/api/v1/customers/{customer_id}/ledger_summary?token={token}{puid_param}"
        req2 = urllib.request.Request(url2, headers=headers)
        with urllib.request.urlopen(req2, timeout=15.0) as resp2:
            data2 = json.loads(resp2.read().decode("utf-8"))

        credits_bal_str = data2.get("credits_balance")
        credit_blocks = data2.get("credit_blocks") or []

        # Find maximum initial balance across credit blocks
        max_init_bal = 0.0
        for block in credit_blocks:
            init_str = block.get("maximum_initial_balance")
            if init_str:
                try:
                    max_init_bal = max(max_init_bal, float(init_str))
                except (ValueError, TypeError):
                    pass

        credits_bal = float(credits_bal_str) if credits_bal_str is not None else 0.0
        if max_init_bal == 0.0:
            max_init_bal = credits_bal

        spend_used = max(0.0, max_init_bal - credits_bal)

        # Step 3: Query per-model token analytics from WithOrb
        token_usage: Dict[str, Any] = {}
        if fetch_model_usage:
            try:
                token_usage = fetch_orb_portal_token_usage(token, headers, force_refresh=force_refresh)
            except Exception as e:
                logger.debug(f"Failed to query Orb token usage: {e}")

        result: Dict[str, Any] = {
            "balance_remaining": round(credits_bal, 2),
            "spend_limit": round(max_init_bal, 2),
            "spend_used": round(spend_used, 2),
            "customer_id": customer_id,
            "customer_name": customer_name,
            "pricing_unit_id": pricing_unit_id,
            "portal_token": token,
            "portal_url": f"https://portal.withorb.com/view?token={token}",
        }
        if token_usage:
            result.update(token_usage)

        return result
    except Exception as e:
        logger.warning(f"Failed to query Orb portal billing: {e}")
        return None

