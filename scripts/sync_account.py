#!/usr/bin/env python3
"""
Zed Account Sync Utility.
Automatically extracts Zed credentials from your OS keychain (Linux, macOS, Windows),
validates them against cloud.zed.dev, and upserts them into accounts.json.
If zed-ai-proxy is currently running, it triggers a live hot-reload via /api/refresh.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Ensure package root is importable
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from app.config import settings
except ImportError:
    settings = None

ZED_CLOUD_URL = os.getenv("ZED_API_URL", "https://cloud.zed.dev").rstrip("/")
ZED_VERSION = os.getenv("ZED_VERSION", "0.176.0")


def extract_credentials_linux() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extracts credentials from FreeDesktop Secret Service via secret-tool."""
    cmd = ["secret-tool", "search", "url", "https://zed.dev"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None, None, "secret-tool is not installed. Install it via: sudo apt install libsecret-tools"
    except Exception as e:
        return None, None, f"Error running secret-tool: {e}"

    combined = (res.stdout or "") + "\n" + (res.stderr or "")
    user_id = None
    token = None

    for line in combined.splitlines():
        line = line.strip()
        if line.startswith("attribute.username ="):
            user_id = line.split("=", 1)[1].strip()
        elif line.startswith("secret ="):
            token = line.split("=", 1)[1].strip()

    if not user_id or not token:
        # Fallback: try search without attributes constraint
        alt_cmd = ["secret-tool", "lookup", "url", "https://zed.dev"]
        try:
            res_lookup = subprocess.run(alt_cmd, capture_output=True, text=True)
            if res_lookup.stdout.strip():
                token = res_lookup.stdout.strip()
        except Exception:
            pass

    if not user_id or not token:
        return None, None, "Could not locate Zed credentials in Linux Secret Service (label: zed-github-account)."

    return user_id, token, None


def extract_credentials_macos() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extracts credentials from macOS Keychain via security CLI."""
    cmd = ["security", "find-internet-password", "-s", "https://zed.dev", "-g"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None, None, "security command not found on macOS."
    except Exception as e:
        return None, None, f"Error querying macOS keychain: {e}"

    combined = (res.stdout or "") + "\n" + (res.stderr or "")

    user_id = None
    token = None

    acct_match = re.search(r'"acct"<blob>="([^"]+)"', combined)
    if acct_match:
        user_id = acct_match.group(1)

    pwd_match = re.search(r'password:\s*"(.*)"', combined)
    if pwd_match:
        token = pwd_match.group(1)

    if not user_id or not token:
        # Retry with zed.dev server name
        alt_cmd = ["security", "find-internet-password", "-s", "zed.dev", "-g"]
        try:
            res_alt = subprocess.run(alt_cmd, capture_output=True, text=True)
            combined_alt = (res_alt.stdout or "") + "\n" + (res_alt.stderr or "")
            if not user_id:
                m = re.search(r'"acct"<blob>="([^"]+)"', combined_alt)
                if m:
                    user_id = m.group(1)
            if not token:
                m = re.search(r'password:\s*"(.*)"', combined_alt)
                if m:
                    token = m.group(1)
        except Exception:
            pass

    if not user_id or not token:
        return None, None, "Could not locate Zed credentials in macOS Keychain."

    return user_id, token, None


def extract_credentials_windows() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extracts credentials from Windows Credential Manager."""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinCredReader {
    [DllImport("Advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CredRead(string target, int type, int reservedFlag, out IntPtr credentialPtr);
    [DllImport("Advapi32.dll", EntryPoint = "CredFree", SetLastError = true)]
    public static extern void CredFree(IntPtr cred);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CREDENTIAL {
        public int Flags;
        public int Type;
        public string TargetName;
        public string Comment;
        public long LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    public static string[] Read(string target) {
        IntPtr credPtr;
        if (CredRead(target, 1, 0, out credPtr)) {
            try {
                CREDENTIAL cred = (CREDENTIAL)Marshal.PtrToStructure(credPtr, typeof(CREDENTIAL));
                string secret = Marshal.PtrToStringUni(cred.CredentialBlob, cred.CredentialBlobSize / 2);
                return new string[] { cred.UserName, secret };
            } finally {
                CredFree(credPtr);
            }
        }
        return null;
    }
}
"@
$res = [WinCredReader]::Read('zed:url=https://zed.dev')
if ($res -ne $null) {
    Write-Output "USERNAME:$($res[0])"
    Write-Output "SECRET:$($res[1])"
}
"""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None, None, "PowerShell is not accessible on this system."
    except Exception as e:
        return None, None, f"Error querying Windows Credential Manager: {e}"

    user_id = None
    token = None
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("USERNAME:"):
            user_id = line.split(":", 1)[1].strip()
        elif line.startswith("SECRET:"):
            token = line.split(":", 1)[1].strip()

    if not user_id or not token:
        return None, None, "Could not locate 'zed:url=https://zed.dev' in Windows Credential Manager."

    return user_id, token, None


def extract_credentials() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Dispatches extraction based on host operating system."""
    if sys.platform.startswith("linux"):
        return extract_credentials_linux()
    elif sys.platform == "darwin":
        return extract_credentials_macos()
    elif sys.platform in ("win32", "cygwin"):
        return extract_credentials_windows()
    else:
        return None, None, f"Unsupported operating system: {sys.platform}"


def validate_zed_credentials(user_id: str, access_token: str) -> Tuple[bool, str, Optional[int]]:
    """Validates credentials by requesting a temporary LLM token from cloud.zed.dev."""
    url = f"{ZED_CLOUD_URL}/client/llm_tokens"
    body = json.dumps({"organization_id": None}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"{user_id} {access_token}",
            "Content-Type": "application/json",
            "User-Agent": f"Zed/{ZED_VERSION}",
            "x-zed-version": ZED_VERSION,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            jwt_token = data.get("token")
            exp = None
            if jwt_token:
                parts = jwt_token.split(".")
                if len(parts) == 3:
                    padding = "=" * ((4 - len(parts[1]) % 4) % 4)
                    payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding).decode("utf-8"))
                    exp = payload.get("exp")
            return True, "Valid credentials confirmed by Zed Cloud", exp
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"Zed Cloud HTTP {e.code}: {err_msg}", None
    except Exception as e:
        return False, f"Connection failed to {ZED_CLOUD_URL}: {e}", None


def notify_running_proxy(proxy_url: str) -> Tuple[bool, str]:
    """Notifies a running proxy instance to hot-reload accounts via /api/refresh."""
    url = f"{proxy_url.rstrip('/')}/api/refresh"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode())
            active = data.get("summary", {}).get("active_accounts", 0)
            return True, f"Proxy pool reloaded ({active} active account(s))."
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:100]
        return False, f"Proxy error HTTP {e.code}: {body}"
    except urllib.error.URLError:
        return False, "Proxy is not currently running (offline)."
    except Exception as e:
        return False, f"Could not contact proxy: {e}"


def upsert_account(
    accounts_file: Path,
    user_id: str,
    access_token: str,
    account_name: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Inserts or updates an account in accounts.json atomically."""
    accounts: List[Dict[str, Any]] = []
    if accounts_file.exists():
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                accounts = loaded if isinstance(loaded, list) else loaded.get("accounts", [])
        except Exception as e:
            print(f"Warning: Failed to parse existing accounts file {accounts_file}: {e}. Creating new list.")
            accounts = []

    # Check for existing account by user_id
    matched_idx = -1
    for i, a in enumerate(accounts):
        if str(a.get("user_id")) == str(user_id):
            matched_idx = i
            break

    if matched_idx >= 0:
        # Update existing
        target = accounts[matched_idx]
        target["access_token"] = access_token
        target["status"] = "active"
        target["last_error"] = None
        if account_name:
            target["name"] = account_name
        if organization_id is not None:
            target["organization_id"] = organization_id
        action = "updated"
        result_account = target
    else:
        # Create new entry
        existing_ids = {str(a.get("id")) for a in accounts}
        seq = len(accounts) + 1
        new_id = f"acc-{seq}"
        while new_id in existing_ids:
            seq += 1
            new_id = f"acc-{seq}"

        new_account = {
            "id": new_id,
            "name": account_name or f"Zed Account {user_id}",
            "user_id": str(user_id),
            "access_token": access_token,
            "organization_id": organization_id,
            "status": "active",
        }
        accounts.append(new_account)
        action = "created"
        result_account = new_account

    # Write atomically
    tmp_path = accounts_file.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, accounts_file)

    return {"action": action, "account": result_account, "total_accounts": len(accounts)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract Zed IDE credentials from OS keychain and upsert into zed-ai-proxy accounts.json."
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Custom friendly display name for this account (e.g. 'Work Zed Pro')",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to accounts.json (defaults to project root accounts.json)",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip pre-flight authentication verification against cloud.zed.dev",
    )
    parser.add_argument(
        "--proxy-url",
        type=str,
        default="http://localhost:8080",
        help="URL of the running zed-ai-proxy to notify (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and validate credentials without saving to accounts.json",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Manually specify user_id (skips OS keychain search)",
    )
    parser.add_argument(
        "--access-token",
        type=str,
        default=None,
        help="Manually specify access_token (skips OS keychain search)",
    )

    args = parser.parse_args()

    if args.file:
        accounts_file = Path(args.file).resolve()
    elif settings is not None:
        accounts_file = settings.accounts_file
    elif "ACCOUNTS_FILE" in os.environ:
        accounts_file = Path(os.environ["ACCOUNTS_FILE"]).resolve()
    else:
        project_root = Path(__file__).resolve().parent.parent
        accounts_file = project_root / "accounts.json"

    user_id = args.user_id
    access_token = args.access_token

    if not user_id or not access_token:
        print(f"[*] Detecting Zed credentials from system keychain ({sys.platform})...")
        extracted_uid, extracted_tok, err = extract_credentials()
        if err:
            print(f"[!] Extraction warning: {err}")
            if not user_id:
                user_id = input("[?] Enter Zed user_id manually: ").strip()
            if not access_token:
                access_token = input("[?] Enter Zed access_token manually: ").strip()
        else:
            user_id = user_id or extracted_uid
            access_token = access_token or extracted_tok

    if not user_id or not access_token:
        print("[ERROR] user_id and access_token are required. Aborting.")
        sys.exit(1)

    print(f"[+] Found Zed user_id: {user_id}")
    token_preview = access_token[:30] + "..." if len(access_token) > 30 else access_token
    print(f"[+] Found Zed access_token: {token_preview}")

    # Validation
    if not args.no_validate:
        print(f"[*] Validating credentials against {ZED_CLOUD_URL}...")
        valid, msg, exp = validate_zed_credentials(user_id, access_token)
        if valid:
            exp_str = ""
            if exp:
                dt = datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                exp_str = f" (initial token expires: {dt})"
            print(f"[SUCCESS] Credentials verified: {msg}{exp_str}")
        else:
            print(f"[WARNING] Validation failed: {msg}")
            if not args.dry_run:
                confirm = input("[?] Do you still want to save these credentials to accounts.json? (y/N): ").strip().lower()
                if confirm not in ("y", "yes"):
                    print("Aborted by user.")
                    sys.exit(1)

    if args.dry_run:
        print("[*] Dry run complete. accounts.json was NOT modified.")
        return

    # Upsert
    res = upsert_account(accounts_file, user_id, access_token, account_name=args.name)
    act = res["action"]
    acc = res["account"]
    tot = res["total_accounts"]
    print(f"[SUCCESS] Account {act}: ID='{acc['id']}', Name='{acc['name']}', user_id={acc['user_id']}")
    print(f"[+] Total accounts in {accounts_file.name}: {tot}")

    # Proxy hot-reload notification
    print(f"[*] Notifying proxy at {args.proxy_url}...")
    success, notify_msg = notify_running_proxy(args.proxy_url)
    if success:
        print(f"[SUCCESS] {notify_msg}")
    else:
        print(f"[*] {notify_msg}")


if __name__ == "__main__":
    main()
