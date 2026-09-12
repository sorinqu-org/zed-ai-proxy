import html
import time
from typing import Dict, Any, List, Optional
from app.core.billing import format_currency, format_tokens
from app.core.account_pool import Account


def render_model_stats_table(model_stats: Dict[str, Dict[str, Any]]) -> str:
    """Renders a breakdown table of tokens per model (input, output, cache write, cache read, cost)."""
    if not model_stats:
        return """
        <div style="padding: 24px; text-align: center; color: #94a3b8; background: #1e293b; border-radius: 8px; border: 1px solid #334155;">
            No model request telemetry recorded yet. Make a request via Claude Code, Codex, or API to populate data.
        </div>
        """

    rows = ""
    for model_name, st in sorted(model_stats.items(), key=lambda x: x[1].get("total_tokens", 0), reverse=True):
        safe_model = html.escape(str(model_name))
        reqs = st.get("requests_count", 0)
        in_tok = st.get("input_tokens", 0)
        out_tok = st.get("output_tokens", 0)
        cw_tok = st.get("cache_write_tokens", 0)
        cr_tok = st.get("cache_read_tokens", 0)
        tot_tok = st.get("total_tokens", 0) or (in_tok + out_tok + cw_tok + cr_tok)
        cost = st.get("cost_usd", 0.0)

        # Cache hit ratio
        cache_denom = in_tok + cw_tok + cr_tok
        hit_ratio = (cr_tok / cache_denom * 100.0) if cache_denom > 0 else 0.0

        # Bar proportions
        p_in = (in_tok / tot_tok * 100.0) if tot_tok > 0 else 0.0
        p_out = (out_tok / tot_tok * 100.0) if tot_tok > 0 else 0.0
        p_cw = (cw_tok / tot_tok * 100.0) if tot_tok > 0 else 0.0
        p_cr = (cr_tok / tot_tok * 100.0) if tot_tok > 0 else 0.0

        bar_html = f"""
        <div style="display:flex; height:6px; width:100%; min-width:100px; background:#0f172a; border-radius:3px; overflow:hidden; margin-top:6px;">
            <div style="width:{p_in:.1f}%; background:#38bdf8;" title="Input: {format_tokens(in_tok)}"></div>
            <div style="width:{p_out:.1f}%; background:#818cf8;" title="Output: {format_tokens(out_tok)}"></div>
            <div style="width:{p_cw:.1f}%; background:#f59e0b;" title="Cache Write: {format_tokens(cw_tok)}"></div>
            <div style="width:{p_cr:.1f}%; background:#10b981;" title="Cache Read: {format_tokens(cr_tok)}"></div>
        </div>
        """

        req_display = f"{reqs:,}" if reqs > 0 else "Metered"

        rows += f"""
        <tr>
            <td>
                <div style="font-weight: 600; color: #f8fafc; font-family: ui-monospace, monospace;">{safe_model}</div>
                {bar_html}
            </td>
            <td style="font-family: ui-monospace, monospace; color: {'#f8fafc' if reqs > 0 else '#94a3b8'};">{req_display}</td>
            <td style="font-family: ui-monospace, monospace; color: #38bdf8;">{in_tok:,}</td>
            <td style="font-family: ui-monospace, monospace; color: #818cf8;">{out_tok:,}</td>
            <td style="font-family: ui-monospace, monospace; color: #f59e0b;">{cw_tok:,}</td>
            <td style="font-family: ui-monospace, monospace; color: #10b981;">{cr_tok:,}</td>
            <td style="font-family: ui-monospace, monospace; font-weight: 700;">{tot_tok:,}</td>
            <td style="font-family: ui-monospace, monospace;">
                <span class="badge" style="background: {'#065f46' if hit_ratio > 20 else '#334155'}; color: {'#34d399' if hit_ratio > 20 else '#94a3b8'};">
                    {hit_ratio:.1f}%
                </span>
            </td>
            <td style="font-family: ui-monospace, monospace; color: #10b981; font-weight: 600;">${cost:.4f}</td>
        </tr>
        """

    return f"""
    <table style="width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; border: 1px solid #334155;">
        <thead>
            <tr style="background: #0f172a; text-align: left;">
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Model</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Requests</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Input (Prompt)</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Output (Gen)</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Cache Write</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Cache Read</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Total Tokens</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Cache Hit</th>
                <th style="padding: 12px 16px; font-size: 11px; text-transform: uppercase; color: #94a3b8; border-bottom: 1px solid #334155;">Est. Spend</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    <div style="display: flex; gap: 16px; align-items: center; margin-top: 10px; font-size: 12px; color: #94a3b8;">
        <span style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #38bdf8; border-radius: 2px;"></span> Input Tokens</span>
        <span style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #818cf8; border-radius: 2px;"></span> Output Tokens</span>
        <span style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #f59e0b; border-radius: 2px;"></span> Cache Write (Creation)</span>
        <span style="display: flex; align-items: center; gap: 4px;"><span style="width: 8px; height: 8px; background: #10b981; border-radius: 2px;"></span> Cache Read (Hits)</span>
    </div>
    """


def render_account_detail_page(account: Account, all_accounts: List[Account]) -> str:
    """Renders the comprehensive dedicated HTML dashboard for an account."""
    now = time.time()
    safe_id = html.escape(str(account.id))
    safe_name = html.escape(str(account.name or f"Account {account.id}"))
    safe_uid = html.escape(str(account.user_id))
    safe_plan = html.escape(str(account.plan or "zed_pro"))
    safe_org = html.escape(str(account.org_name or ""))
    safe_status = html.escape(str(account.status))

    status_bg = "#10b981" if account.status == "active" else ("#f59e0b" if account.status == "cooldown" else "#ef4444")
    exp_rem = int(account.expires_at - now) if account.expires_at and account.expires_at > now else None
    exp_str = f"{exp_rem}s" if exp_rem is not None else "Expired"

    bal = account.balance_remaining
    lim = account.spend_limit
    used = account.spend_used
    pct = (bal / lim * 100.0) if lim > 0 else 0.0

    billing_url = account.billing_url or "https://dashboard.zed.dev/billing/usage"
    safe_burl = html.escape(billing_url)
    orb_url = account.orb_portal_url
    safe_orb = html.escape(orb_url) if orb_url else None

    # Switcher tabs for pool accounts
    switcher_html = ""
    for a in all_accounts:
        a_id = html.escape(str(a.id))
        is_curr = (a.id == account.id)
        pill_style = "background: #38bdf8; color: #0f172a; font-weight: 600;" if is_curr else "background: #334155; color: #f8fafc;"
        switcher_html += f'<a href="/dashboard/accounts/{a_id}" style="padding: 6px 14px; border-radius: 6px; font-size: 13px; text-decoration: none; {pill_style}">{a_id}</a> '

    model_table_html = render_model_stats_table(account.model_stats)

    # Portal Action Buttons
    portal_btns = f'<a href="{safe_burl}" target="_blank" class="btn" style="background:#0284c7; text-decoration:none;">Zed Usage Portal &rarr;</a>'
    if safe_orb:
        portal_btns += f' <a href="{safe_orb}" target="_blank" class="btn" style="background:#059669; text-decoration:none;">WithOrb Billing Portal &rarr;</a>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="10">
    <title>{safe_name} ({safe_id}) | Zed Proxy Account Details</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #090d16;
            color: #f8fafc;
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1180px;
            margin: 0 auto;
        }}
        .nav {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid #1f2937;
        }}
        .breadcrumb {{
            font-size: 14px;
            color: #94a3b8;
        }}
        .breadcrumb a {{
            color: #38bdf8;
            text-decoration: none;
        }}
        .hero {{
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 24px;
        }}
        .badge {{
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 28px;
        }}
        .card {{
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 10px;
            padding: 20px;
        }}
        .card-label {{
            font-size: 12px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        .card-val {{
            font-size: 26px;
            font-weight: 700;
            font-family: ui-monospace, monospace;
        }}
        .btn {{
            color: white;
            padding: 8px 16px;
            border-radius: 6px;
            border: none;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        th, td {{
            padding: 12px 14px;
            font-size: 13px;
            border-bottom: 1px solid #334155;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <div class="breadcrumb">
                <a href="/dashboard">&larr; Back to Proxy Dashboard</a> / <span style="color: #f8fafc;">Account: {safe_id}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 12px; color: #94a3b8;">Switch Account:</span>
                {switcher_html}
            </div>
        </div>

        <div class="hero">
            <div>
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                    <h1 style="font-size: 24px; font-weight: 700; margin: 0;">{safe_name}</h1>
                    <span class="badge" style="background: {status_bg}; color: white;">{safe_status}</span>
                    <span class="badge" style="background: #334155; color: #94a3b8;">{safe_plan}</span>
                </div>
                <div style="color: #94a3b8; font-size: 13px; margin-bottom: 16px;">
                    Account ID: <code style="color: #38bdf8;">{safe_id}</code> &bull; User ID: <code>{safe_uid}</code> &bull; Organization: <strong>{safe_org or 'Personal'}</strong>
                </div>
                <div style="display: flex; gap: 10px;">
                    {portal_btns}
                </div>
            </div>

            <div style="background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 18px;">
                <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px;">
                    <span style="font-size: 13px; color: #94a3b8;">Available Balance</span>
                    <span style="font-size: 28px; font-weight: 800; color: #10b981; font-family: ui-monospace, monospace;">${bal:.2f}</span>
                </div>
                <div style="height: 8px; background: #1e293b; border-radius: 4px; overflow: hidden; margin-bottom: 10px;">
                    <div style="height: 100%; width: {pct:.1f}%; background: #10b981; border-radius: 4px;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 12px; color: #94a3b8;">
                    <span>Spent: <strong style="color: #f8fafc;">${used:.4f}</strong></span>
                    <span>Limit: <strong style="color: #f8fafc;">${lim:.2f}</strong> ({pct:.1f}% rem.)</span>
                </div>
            </div>
        </div>

        <div class="section-title">
            Token Telemetry Breakdown
        </div>

        <div class="cards">
            <div class="card">
                <div class="card-label">Input Tokens (Prompt)</div>
                <div class="card-val" style="color: #38bdf8;">{account.input_tokens_total:,}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Standard context tokens</div>
            </div>
            <div class="card">
                <div class="card-label">Output Tokens (Gen)</div>
                <div class="card-val" style="color: #818cf8;">{account.output_tokens_total:,}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">Completions & tool results</div>
            </div>
            <div class="card">
                <div class="card-label">Cache Write (Creation)</div>
                <div class="card-val" style="color: #f59e0b;">{account.cache_write_tokens_total:,}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">1-hour/5m prompt cache</div>
            </div>
            <div class="card">
                <div class="card-label">Cache Read (Hits)</div>
                <div class="card-val" style="color: #10b981;">{account.cache_read_tokens_total:,}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">90% discounted context reads</div>
            </div>
        </div>

        <div class="section-title" style="display: flex; align-items: center; justify-content: space-between;">
            <span>WithOrb Official Token Analytics</span>
            <span class="badge" style="background: #065f46; color: #34d399; font-weight: 500; font-size: 11px;">WithOrb Metering Sync</span>
        </div>
        {model_table_html}

        <div style="margin-top: 28px; background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 18px;">
            <div class="section-title" style="font-size: 15px; margin-bottom: 12px;">Session & Security Metadata</div>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; font-size: 13px;">
                <div><span style="color: #94a3b8;">Requests OK/Total:</span> <strong>{account.successful_requests} / {account.total_requests}</strong></div>
                <div><span style="color: #94a3b8;">JWT Token Valid:</span> <strong style="color: #38bdf8;">{exp_str}</strong></div>
                <div><span style="color: #94a3b8;">Total Tokens:</span> <strong>{account.tokens_used:,}</strong></div>
                <div><span style="color: #94a3b8;">Last Error:</span> <span style="color: {'#ef4444' if account.last_error else '#94a3b8'};">{html.escape(str(account.last_error or 'None'))}</span></div>
            </div>
        </div>
    </div>
</body>
</html>
"""
