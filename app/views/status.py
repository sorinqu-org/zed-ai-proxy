import html
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any
from app.config import settings
from app.core.security import security_policy
from app.core.billing import format_currency, format_tokens

router = APIRouter()


@router.get("/status", response_class=JSONResponse)
async def get_status(request: Request) -> Dict[str, Any]:
    security_policy.verify_admin_auth(request)
    pool = request.app.state.pool
    summary = pool.get_status_summary()
    return summary


@router.post("/api/refresh", response_class=JSONResponse)
async def trigger_refresh(request: Request) -> Dict[str, Any]:
    security_policy.verify_admin_auth(request)
    pool = request.app.state.pool
    pool.load_from_file(settings.accounts_file)
    await pool.refresh_all_tokens()
    return {"status": "ok", "summary": pool.get_status_summary()}


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    security_policy.verify_admin_auth(request)
    pool = request.app.state.pool
    summary = pool.get_status_summary()

    rows_html = ""
    for a in summary["accounts"]:
        status_color = "#10b981" if a["status"] == "active" else "#ef4444"
        if a["status"] == "cooldown":
            status_color = "#f59e0b"

        safe_id = html.escape(str(a["id"]))
        safe_name = html.escape(str(a["name"]))
        safe_uid = html.escape(str(a["user_id"]))
        safe_status = html.escape(str(a["status"]))
        safe_plan = html.escape(str(a.get("plan") or "zed_pro"))
        safe_org = html.escape(str(a.get("org_name") or ""))
        plan_display = f"{safe_plan} ({safe_org})" if safe_org else safe_plan
        exp_str = f"{a['token_expires_in_seconds']}s" if a["token_expires_in_seconds"] else "None"
        last_err = html.escape(str(a["last_error"])) if a["last_error"] else "-"

        bal_rem = a.get("balance_remaining", 0.0)
        spend_lim = a.get("spend_limit", 10.0)
        toks = format_tokens(a.get("tokens_used", 0))
        billing_url = a.get("billing_url") or "https://dashboard.zed.dev/billing/usage"
        safe_burl = html.escape(billing_url)

        rows_html += f"""
        <tr>
            <td><strong>{safe_id}</strong></td>
            <td>{safe_name}</td>
            <td><span class="badge" style="background:#334155;">{plan_display}</span></td>
            <td><strong style="color:#10b981;">${bal_rem:.2f}</strong> <span style="color:#94a3b8; font-size:12px;">/ ${spend_lim:.2f}</span></td>
            <td>{toks}</td>
            <td><a href="{safe_burl}" target="_blank" style="color:#38bdf8; text-decoration:none; font-size:12px;">View Usage &rarr;</a></td>
            <td><span class="badge" style="background:{status_color};">{safe_status}</span></td>
            <td>{exp_str}</td>
            <td>{a['successful_requests']} / {a['total_requests']}</td>
            <td style="color:#ef4444; font-size:12px;">{last_err}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="10">
    <title>Zed AI Proxy Dashboard</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            margin: 0;
            padding: 30px;
        }}
        .container {{
            max-width: 1100px;
            margin: 0 auto;
        }}
        h1 {{
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 30px;
        }}
        .card {{
            background: #1e293b;
            padding: 20px;
            border-radius: 8px;
            border: 1px solid #334155;
        }}
        .card-label {{
            font-size: 13px;
            color: #94a3b8;
            margin-bottom: 6px;
        }}
        .card-val {{
            font-size: 28px;
            font-weight: 700;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: #1e293b;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #334155;
        }}
        th, td {{
            padding: 12px 16px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}
        th {{
            background: #0f172a;
            font-size: 12px;
            text-transform: uppercase;
            color: #94a3b8;
        }}
        .badge {{
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            color: #ffffff;
        }}
        .btn {{
            background: #3b82f6;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
        }}
        .btn:hover {{
            background: #2563eb;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>
            <span>Zed AI Proxy Dashboard</span>
            <form action="/api/refresh" method="post" style="display:inline; margin-left:auto;">
                <button type="submit" class="btn">Refresh Tokens Now</button>
            </form>
        </h1>

        <div class="cards">
            <div class="card">
                <div class="card-label">Total Remaining Balance</div>
                <div class="card-val" style="color: #10b981;">${summary.get('total_balance_remaining', 0.0):.2f}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">of ${summary.get('total_spend_limit', 0.0):.2f} limit</div>
            </div>
            <div class="card">
                <div class="card-label">Total Tokens Used</div>
                <div class="card-val">{format_tokens(summary.get('total_tokens_used', 0))}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">spent: ${summary.get('total_spend_used', 0.0):.4f}</div>
            </div>
            <div class="card">
                <div class="card-label">Active / Total Accounts</div>
                <div class="card-val">{summary['available_accounts']} / {summary['total_accounts']}</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">in cooldown: {summary.get('cooldown_accounts', 0)}</div>
            </div>
            <div class="card">
                <div class="card-label">Isolation & Policy</div>
                <div class="card-val" style="font-size: 20px; color: #38bdf8; margin-top: 4px;">Sandboxed</div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 4px;">tools: {settings.tool_policy}</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Plan / Org</th>
                    <th>Balance</th>
                    <th>Tokens</th>
                    <th>Zed Dashboard</th>
                    <th>Status</th>
                    <th>Token Exp</th>
                    <th>Success / Total</th>
                    <th>Last Error</th>
                </tr>
            </thead>
            <tbody>
                {rows_html if rows_html else '<tr><td colspan="10" style="text-align:center; color:#94a3b8;">No accounts configured. Check accounts.json</td></tr>'}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    return html_content
