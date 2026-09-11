from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any

router = APIRouter()


@router.get("/status", response_class=JSONResponse)
async def get_status(request: Request) -> Dict[str, Any]:
    pool = request.app.state.pool
    summary = pool.get_status_summary()
    return summary


@router.post("/api/refresh", response_class=JSONResponse)
async def trigger_refresh(request: Request) -> Dict[str, Any]:
    pool = request.app.state.pool
    await pool.refresh_all_tokens()
    return {"status": "ok", "summary": pool.get_status_summary()}


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    pool = request.app.state.pool
    summary = pool.get_status_summary()

    rows_html = ""
    for a in summary["accounts"]:
        status_color = "#10b981" if a["status"] == "active" else "#ef4444"
        if a["status"] == "cooldown":
            status_color = "#f59e0b"

        exp_str = f"{a['token_expires_in_seconds']}s" if a["token_expires_in_seconds"] else "None"
        cd_str = f"{a['cooldown_remaining_seconds']}s" if a["cooldown_remaining_seconds"] else "-"
        last_err = a["last_error"] or "-"

        rows_html += f"""
        <tr>
            <td>{a['id']}</td>
            <td>{a['name']}</td>
            <td>{a['user_id']}</td>
            <td><span class="badge" style="background:{status_color};">{a['status']}</span></td>
            <td>{'Yes' if a['has_token'] else 'No'}</td>
            <td>{exp_str}</td>
            <td>{cd_str}</td>
            <td>{a['successful_requests']} / {a['total_requests']}</td>
            <td style="color:#ef4444; font-size:12px;">{last_err}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
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
                <div class="card-label">Total Accounts</div>
                <div class="card-val">{summary['total_accounts']}</div>
            </div>
            <div class="card">
                <div class="card-label">Available Accounts</div>
                <div class="card-val" style="color: #10b981;">{summary['available_accounts']}</div>
            </div>
            <div class="card">
                <div class="card-label">Auto-Refresh Interval</div>
                <div class="card-val">60s</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>User ID</th>
                    <th>Status</th>
                    <th>Has Token</th>
                    <th>Token Exp</th>
                    <th>Cooldown</th>
                    <th>Success / Total</th>
                    <th>Last Error</th>
                </tr>
            </thead>
            <tbody>
                {rows_html if rows_html else '<tr><td colspan="9" style="text-align:center; color:#94a3b8;">No accounts configured. Check accounts.json</td></tr>'}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
    return html
