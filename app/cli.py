#!/usr/bin/env python3
"""
CLI entrypoint for zed-ai-proxy (command: zed-proxy).
Provides subcommands: start, stop, restart, status, sync, refresh, models, logs, completion.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from app.config import settings


def _get_pid_file() -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "proxy.pid"


def _get_log_file() -> Path:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    return settings.state_dir / "proxy.log"


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid() -> Optional[int]:
    pid_file = _get_pid_file()
    if pid_file.exists():
        try:
            val = int(pid_file.read_text().strip())
            if _is_pid_running(val):
                return val
        except Exception:
            pass
    return None


def cmd_start(args: argparse.Namespace) -> None:
    host = args.host or settings.host
    port = args.port or settings.port
    accounts_file = Path(args.accounts).resolve() if args.accounts else settings.accounts_file

    os.environ["HOST"] = str(host)
    os.environ["PORT"] = str(port)
    os.environ["ACCOUNTS_FILE"] = str(accounts_file)

    existing_pid = _read_pid()
    if existing_pid:
        print(f"[ERROR] Proxy is already running in background (PID: {existing_pid}).")
        print(f"Use 'zed-proxy status' to inspect or 'zed-proxy stop' to terminate.")
        sys.exit(1)

    if args.daemon:
        log_path = _get_log_file()
        pid_file = _get_pid_file()

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ]
        if args.reload:
            cmd.append("--reload")

        env = os.environ.copy()
        log_fp = open(log_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        pid_file.write_text(str(proc.pid))

        # Wait briefly to confirm it didn't exit on startup error
        time.sleep(0.5)
        if proc.poll() is not None:
            print(f"[ERROR] Proxy failed to start. Check logs at: {log_path}")
            sys.exit(1)

        print(f"[SUCCESS] Proxy started in background:")
        print(f"  PID:       {proc.pid}")
        print(f"  Endpoint:  http://{host}:{port}")
        print(f"  Dashboard: http://{host}:{port}/dashboard")
        print(f"  Accounts:  {accounts_file}")
        print(f"  Log file:  {log_path}")
    else:
        import uvicorn
        print(f"Starting zed-ai-proxy on http://{host}:{port} (Accounts: {accounts_file})...")
        uvicorn.run("app.main:app", host=host, port=port, reload=args.reload)


def cmd_stop(args: argparse.Namespace) -> None:
    pid = _read_pid()
    pid_file = _get_pid_file()

    if not pid:
        print("[*] No running proxy instance found.")
        if pid_file.exists():
            pid_file.unlink(missing_ok=True)
        return

    print(f"[*] Stopping proxy process (PID: {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.1)
            if not _is_pid_running(pid):
                break
        else:
            print("[!] Process did not terminate; sending SIGKILL...")
            os.kill(pid, signal.SIGKILL)
    except Exception as e:
        print(f"[!] Error while terminating process {pid}: {e}")

    pid_file.unlink(missing_ok=True)
    print("[SUCCESS] Proxy stopped.")


def cmd_restart(args: argparse.Namespace) -> None:
    cmd_stop(args)
    time.sleep(0.5)
    args.daemon = True
    args.reload = getattr(args, "reload", False)
    cmd_start(args)


def cmd_status(args: argparse.Namespace) -> None:
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}/status"

    pid = _read_pid()
    if pid:
        print(f"Daemon: RUNNING (PID: {pid})")
    else:
        print("Daemon: NOT RUNNING as background daemon")

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[!] Proxy HTTP server is offline or unreachable at {url}: {e}")
        return

    print(f"Proxy Endpoint: http://{host}:{port}")
    print(f"Total Accounts: {data.get('total_accounts', 0)}")
    print(f"Active:         {data.get('active_accounts', 0)}")
    print(f"In Cooldown:    {data.get('cooldown_accounts', 0)}")
    print("")

    accounts = data.get("accounts", [])
    if not accounts:
        print("No accounts loaded. Run 'zed-proxy sync' to import your credentials.")
        return

    # Table format
    header = f"{'ID':<8} {'Name':<20} {'User ID':<12} {'Status':<10} {'Expires':<10} {'Cooldown':<10} {'Reqs (OK/Tot)':<14} {'Last Error'}"
    print(header)
    print("-" * 105)
    for a in accounts:
        aid = str(a.get("id", ""))[:7]
        name = str(a.get("name", ""))[:19]
        uid = str(a.get("user_id", ""))[:11]
        stat = str(a.get("status", ""))[:9]
        exp_sec = a.get("token_expires_in_seconds")
        exp = f"{exp_sec}s" if exp_sec is not None else "None"
        cd_sec = a.get("cooldown_remaining_seconds")
        cd = f"{cd_sec}s" if cd_sec else "-"
        reqs = f"{a.get('successful_requests', 0)}/{a.get('total_requests', 0)}"
        err = str(a.get("last_error") or "-")[:25]
        print(f"{aid:<8} {name:<20} {uid:<12} {stat:<10} {exp:<10} {cd:<10} {reqs:<14} {err}")


def cmd_sync(args: argparse.Namespace) -> None:
    from scripts.sync_account import main as sync_main
    sync_args = []
    if args.name:
        sync_args.extend(["--name", args.name])
    accounts_file = Path(args.accounts).resolve() if args.accounts else settings.accounts_file
    sync_args.extend(["--file", str(accounts_file)])
    if args.no_validate:
        sync_args.append("--no-validate")
    if args.dry_run:
        sync_args.append("--dry-run")
    if args.user_id:
        sync_args.extend(["--user-id", args.user_id])
    if args.access_token:
        sync_args.extend(["--access-token", args.access_token])

    # Forward proxy url
    host = args.host or settings.host
    port = args.port or settings.port
    sync_args.extend(["--proxy-url", f"http://{host}:{port}"])

    sys.argv = ["sync_account.py"] + sync_args
    sync_main()


def cmd_refresh(args: argparse.Namespace) -> None:
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}/api/refresh"
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())
            print(f"[SUCCESS] Proxy pool refreshed successfully.")
            print(f"Active accounts: {data.get('summary', {}).get('active_accounts', 0)}")
    except Exception as e:
        print(f"[ERROR] Failed to refresh proxy pool at {url}: {e}")
        sys.exit(1)


def cmd_models(args: argparse.Namespace) -> None:
    host = args.host or settings.host
    port = args.port or settings.port
    url = f"http://{host}:{port}/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            data = json.loads(resp.read().decode())
            models = data.get("data", [])
            print(f"Available models ({len(models)}):")
            for m in models:
                mid = m.get("id")
                owned = m.get("owned_by", "")
                print(f"  - {mid:<30} (provider: {owned})")
    except Exception as e:
        print(f"[ERROR] Failed to fetch models from {url}: {e}")
        sys.exit(1)


def cmd_logs(args: argparse.Namespace) -> None:
    log_path = _get_log_file()
    if not log_path.exists():
        print(f"No log file found at {log_path}")
        return

    n_lines = args.lines or 50
    if args.follow:
        cmd = ["tail", f"-n{n_lines}", "-f", str(log_path)]
        try:
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass
    else:
        cmd = ["tail", f"-n{n_lines}", str(log_path)]
        subprocess.run(cmd)


def cmd_completion(args: argparse.Namespace) -> None:
    shell = args.shell.lower()
    if shell == "bash":
        print(r"""_zed_proxy_completions() {
    local cur prev subcommands
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    subcommands="start stop restart status sync refresh models logs completion"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$subcommands" -- "$cur") )
        return 0
    fi

    case "$prev" in
        --host|--port|--accounts|--lines)
            return 0
            ;;
    esac
}
complete -F _zed_proxy_completions zed-proxy
complete -F _zed_proxy_completions zed-ai-proxy""")
    elif shell == "zsh":
        print(r"""#compdef zed-proxy zed-ai-proxy

_zed_proxy() {
    local -a subcommands
    subcommands=(
        'start:Start proxy server'
        'stop:Stop background daemon'
        'restart:Restart background daemon'
        'status:Display pool health and telemetry'
        'sync:Import or update Zed credentials from OS keychain'
        'refresh:Trigger hot-reload of accounts pool'
        'models:List available upstream models'
        'logs:Show proxy daemon logs'
        'completion:Generate shell completion script'
    )

    if (( CURRENT == 2 )); then
        _describe -t commands 'subcommands' subcommands
    fi
_zed_proxy "$@"
""")
    elif shell == "fish":
        print(r"""complete -c zed-proxy -f
complete -c zed-proxy -n "__fish_use_subcommand" -a "start" -d "Start proxy server"
complete -c zed-proxy -n "__fish_use_subcommand" -a "stop" -d "Stop background daemon"
complete -c zed-proxy -n "__fish_use_subcommand" -a "restart" -d "Restart background daemon"
complete -c zed-proxy -n "__fish_use_subcommand" -a "status" -d "Display pool health and telemetry"
complete -c zed-proxy -n "__fish_use_subcommand" -a "sync" -d "Import or update Zed credentials from OS keychain"
complete -c zed-proxy -n "__fish_use_subcommand" -a "refresh" -d "Trigger hot-reload of accounts pool"
complete -c zed-proxy -n "__fish_use_subcommand" -a "models" -d "List available upstream models"
complete -c zed-proxy -n "__fish_use_subcommand" -a "logs" -d "Show proxy daemon logs"
complete -c zed-proxy -n "__fish_use_subcommand" -a "completion" -d "Generate shell completion script"

complete -c zed-ai-proxy -w zed-proxy""")
    else:
        print(f"Unsupported shell: {shell}. Supported: bash, zsh, fish", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zed-proxy",
        description="CLI tool to run and manage zed-ai-proxy (OpenAI / Anthropic gateway to Zed AI models).",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # start
    p_start = subparsers.add_parser("start", aliases=["run"], help="Start the proxy server")
    p_start.add_argument("--host", type=str, default=None, help="Host to bind (default: 0.0.0.0)")
    p_start.add_argument("--port", type=int, default=None, help="Port to bind (default: 8080)")
    p_start.add_argument("-d", "--daemon", action="store_true", help="Run server in the background as daemon")
    p_start.add_argument("--reload", action="store_true", help="Enable automatic code reload")
    p_start.add_argument("--accounts", type=str, default=None, help="Path to accounts.json file")

    # stop
    subparsers.add_parser("stop", help="Stop background daemon")

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart background daemon")
    p_restart.add_argument("--host", type=str, default=None)
    p_restart.add_argument("--port", type=int, default=None)
    p_restart.add_argument("--accounts", type=str, default=None)
    p_restart.add_argument("--reload", action="store_true", help="Enable automatic code reload")

    # status
    p_status = subparsers.add_parser("status", help="Inspect pool status and account health")
    p_status.add_argument("--host", type=str, default="localhost")
    p_status.add_argument("--port", type=int, default=None)

    # sync
    p_sync = subparsers.add_parser("sync", aliases=["add"], help="Extract and sync credentials from OS keychain")
    p_sync.add_argument("--name", type=str, default=None, help="Friendly name for account")
    p_sync.add_argument("--accounts", type=str, default=None, help="Path to accounts.json")
    p_sync.add_argument("--no-validate", action="store_true", help="Skip cloud validation")
    p_sync.add_argument("--dry-run", action="store_true", help="Extract without saving")
    p_sync.add_argument("--user-id", type=str, default=None)
    p_sync.add_argument("--access-token", type=str, default=None)
    p_sync.add_argument("--host", type=str, default="localhost")
    p_sync.add_argument("--port", type=int, default=None)

    # refresh
    p_refresh = subparsers.add_parser("refresh", help="Trigger hot-reload of accounts pool")
    p_refresh.add_argument("--host", type=str, default="localhost")
    p_refresh.add_argument("--port", type=int, default=None)

    # models
    p_models = subparsers.add_parser("models", help="List available models")
    p_models.add_argument("--host", type=str, default="localhost")
    p_models.add_argument("--port", type=int, default=None)

    # logs
    p_logs = subparsers.add_parser("logs", help="View daemon logs")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow log output")
    p_logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines to show")

    # completion
    p_comp = subparsers.add_parser("completion", help="Generate shell completions")
    p_comp.add_argument("shell", choices=["bash", "zsh", "fish"], help="Target shell")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if args.subcommand in ("start", "run"):
        cmd_start(args)
    elif args.subcommand == "stop":
        cmd_stop(args)
    elif args.subcommand == "restart":
        cmd_restart(args)
    elif args.subcommand == "status":
        cmd_status(args)
    elif args.subcommand in ("sync", "add"):
        cmd_sync(args)
    elif args.subcommand == "refresh":
        cmd_refresh(args)
    elif args.subcommand == "models":
        cmd_models(args)
    elif args.subcommand == "logs":
        cmd_logs(args)
    elif args.subcommand == "completion":
        cmd_completion(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
