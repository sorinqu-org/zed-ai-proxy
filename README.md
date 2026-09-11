# zed-ai-proxy

High-performance asynchronous reverse proxy that unlocks Zed AI subscription models (Claude Opus 5, Claude Sonnet 5, Claude Haiku 4.5, GPT-5.6, Gemini 3) for external tools and environments via standard OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`) APIs.

Built with FastAPI, HTTPX, and native token pooling.

[English](README.md) | [Русский](README.ru.md)

---

## Features

- **Multi-Account Pooling & Rotation:** Manage multiple Zed Pro accounts with automatic Round-Robin distribution.
- **Failover & Cooldown:** Automatically marks accounts hitting 429 (Rate Limit) on cooldown and retries the request across healthy accounts.
- **Auto-Refreshing Tokens:** Background task pre-emptively refreshes ephemeral LLM tokens before expiration using Zed's internal `/client/llm_tokens` protocol.
- **Dual API Support:** Exposes standard OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`) endpoints with full Server-Sent Events (SSE) streaming.
- **Payload Sanitizer:** Strips oversized base64 image blobs to prevent context window explosion and HTTP 413 Payload Too Large errors.
- **Status Dashboard:** Web UI at `/dashboard` and JSON API at `/status` for monitoring account health, token expiration, and request telemetry.

---

## Architecture

Zed AI client communication protocol:
1. `POST https://cloud.zed.dev/client/llm_tokens` with header `Authorization: <user_id> <access_token>` returns an ephemeral JWT LLM token.
2. `POST https://cloud.zed.dev/models/...` with header `Authorization: Bearer <llm_token>` streams model completions.

`zed-ai-proxy` manages this lifecycle transparently:

```
[ Your Client / IDE / Scripts ]
            |
            v  (OpenAI / Anthropic REST & SSE)
    [ zed-ai-proxy :8080 ]
      |-- AccountPool (Round-Robin & Healthcheck)
      |-- TokenRefresher (Background loop)
      |-- PayloadFilter (Image & size defense)
            |
            v  (Zed Cloud Protocol)
    [ cloud.zed.dev Gateway ]
            |
            v
 [ Claude Opus 5 / Sonnet 5 / GPT-5.6 ]
```

---

## Installation & Quickstart

### 1. Global CLI Installation (Bash, Zsh, Fish)

Install `zed-proxy` globally to your system with automatic shell integration:

```bash
git clone https://github.com/sorinqu-org/zed-ai-proxy.git
cd zed-ai-proxy
./install.sh
```

This installer automatically:
- Creates an isolated virtual environment in `~/.local/share/zed-ai-proxy/venv`.
- Places executable binary `zed-proxy` (and alias `zed-ai-proxy`) in `~/.local/bin`.
- Ensures `~/.local/bin` is in `PATH` across **bash**, **zsh**, and **fish**.
- Installs tab completion scripts for all three shells.

Once installed, use `zed-proxy` from anywhere:
```bash
zed-proxy sync --name "Work Zed"   # Import/update credentials from OS keychain
zed-proxy start -d                 # Start proxy in background as daemon
zed-proxy status                   # View pool table, accounts, and telemetry
zed-proxy stop                     # Stop background daemon
zed-proxy logs -f                  # Follow live daemon logs
```

---

### 2. Extract Account Credentials

Zed signs in via GitHub OAuth and stores credentials in your operating system's native keychain:

- **Linux (`secret-tool`):**
  ```bash
  secret-tool search url "https://zed.dev"
  ```
  Look for:
  - `attribute.username = 1059832` -> this is your `user_id`
  - `secret = {"version":2,...}` -> this is your `access_token`
  (Or check **Passwords and Keys / Seahorse** under `zed-github-account`).

- **macOS (`security`):**
  ```bash
  security find-internet-password -s "https://zed.dev" -g
  ```
  Look for `acct` (`user_id`) and `password` (`access_token`), or check **Keychain Access.app**.

- **Windows (Credential Manager):**
  Open `control keymgr.dll` -> **Windows Credentials** -> **Generic Credentials** -> `zed:url=https://zed.dev`.
  User name is `user_id`, and clicking **Show** next to password gives your `access_token`.

### 3. Automatic Account Import / Sync

Instead of manually editing `accounts.json`, you can run the automatic sync utility:

```bash
python3 scripts/sync_account.py --name "My Zed Pro"
```

- Automatically extracts `user_id` and `access_token` from your OS keychain.
- Validates the token against `cloud.zed.dev`.
- Upserts the credentials into `accounts.json` (updates existing or adds new).
- Sends a hot-reload notification to the proxy via `/api/refresh` if it is currently running.

### 4. Manual Configuration (Alternative)

Copy the template:
```bash
cp accounts.example.json accounts.json
```

Add your Zed account credentials:
```json
[
  {
    "id": "acc-1",
    "name": "Zed Main",
    "user_id": "1059832",
    "access_token": "{\"version\":2,\"id\":\"client_token_...\",\"token\":\"...\"}",
    "organization_id": null
  }
]
```

### 5. Run the Proxy

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080/dashboard` in your browser to view the active pool status.

---

## Usage Examples

### OpenAI SDK / Compatible Clients

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="dummy-key",
)

response = client.chat.completions.create(
    model="claude-opus-5",
    messages=[{"role": "user", "content": "Explain Rust async runtime in 2 sentences."}],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content
    if content:
        print(content, end="", flush=True)
```

### Anthropic SDK

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8080",
    api_key="dummy-key",
)

with client.messages.stream(
    model="claude-sonnet-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### Curl (Streaming)

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-opus-5",
    "messages": [{"role": "user", "content": "Ping"}],
    "stream": true
  }'
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## License

MIT License.
