# zed-ai-proxy

High-performance asynchronous reverse proxy that unlocks Zed AI subscription models (Claude Opus 5, Claude Sonnet 5, Claude Haiku 4.5, GPT-5.6, Gemini 3) for external tools and environments via standard OpenAI (`/v1/chat/completions`) and Anthropic (`/v1/messages`) APIs.

Built with FastAPI, HTTPX, and native token pooling.

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
1. `POST https://api.zed.dev/client/llm_tokens` with header `Authorization: <user_id> <access_token>` returns an ephemeral JWT LLM token.
2. `POST https://api.zed.dev/completions` with header `Authorization: Bearer <llm_token>` streams model completions.

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
    [ api.zed.dev Gateway ]
            |
            v
 [ Claude Opus 5 / Sonnet 5 / GPT-5.6 ]
```

---

## Installation & Quickstart

### 1. Clone & Install Dependencies

```bash
git clone https://github.com/sorinqu-org/zed-ai-proxy.git
cd zed-ai-proxy
pip install -r requirements.txt
```

### 2. Configure Accounts

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
    "user_id": "12345",
    "access_token": "your_zed_access_token_1",
    "organization_id": null
  },
  {
    "id": "acc-2",
    "name": "Zed Backup",
    "user_id": "67890",
    "access_token": "your_zed_access_token_2",
    "organization_id": null
  }
]
```

### 3. Run the Proxy

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
