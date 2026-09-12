import json
import logging
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple

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


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculates estimated cost in USD based on model pricing per 1M tokens."""
    pricing = DEFAULT_PRICING
    model_lower = (model or "").lower()
    for key, p in MODEL_PRICING.items():
        if key in model_lower:
            pricing = p
            break
    cost_in = (input_tokens / 1_000_000.0) * pricing["input"]
    cost_out = (output_tokens / 1_000_000.0) * pricing["output"]
    return cost_in + cost_out


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
