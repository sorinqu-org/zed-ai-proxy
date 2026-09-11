import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.adapters.anthropic_adapter import (
    anthropic_to_zed_body,
    parse_zed_chunk_to_anthropic_event,
)
from app.adapters.openai_adapter import (
    openai_to_zed_body,
    parse_zed_chunk_to_openai_delta,
)
from app.config import settings
from app.core.account_pool import Account, AccountPool
from app.views.status import router as status_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("zed_proxy")

pool = AccountPool()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing Zed AI Proxy...")
    pool.load_from_file(settings.accounts_file)
    # If no accounts loaded and example exists, log notice
    if not pool.accounts:
        example_path = settings.accounts_file.parent / "accounts.example.json"
        if example_path.exists():
            logger.info(f"Loading fallback template accounts from {example_path}")
            pool.load_from_file(example_path)

    app.state.pool = pool
    await pool.start_background_loop()
    yield
    # Shutdown
    logger.info("Stopping background token refresher...")
    pool.stop_background_loop()


app = FastAPI(
    title="Zed AI Multi-Account Proxy",
    description="Reverse proxy for Zed AI models with account pooling and auto-refresh",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.pool = pool

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status_router)


@app.get("/v1/models")
async def list_models() -> Dict[str, Any]:
    """Returns models supported by Zed AI."""
    models_list = [
        {"id": "claude-opus-5", "object": "model", "owned_by": "zed"},
        {"id": "claude-sonnet-5", "object": "model", "owned_by": "zed"},
        {"id": "claude-haiku-4-5", "object": "model", "owned_by": "zed"},
        {"id": "claude-fable-5", "object": "model", "owned_by": "zed"},
        {"id": "gpt-5.6-luna", "object": "model", "owned_by": "zed"},
        {"id": "gpt-5.6-sol", "object": "model", "owned_by": "zed"},
        {"id": "gemini-3.1-pro", "object": "model", "owned_by": "zed"},
        {"id": "gemini-3-flash", "object": "model", "owned_by": "zed"},
    ]
    return {"object": "list", "data": models_list}


async def forward_to_zed_cloud(
    zed_body: Dict[str, Any],
    pool: AccountPool,
    max_retries: int = 3,
) -> Tuple[httpx.Response, httpx.AsyncClient, Account]:
    """Handles sending completion request to Zed with auto-failover across accounts."""
    client = httpx.AsyncClient(timeout=120.0)
    retries = 0

    while retries < max_retries:
        account = await pool.get_next_account()
        if not account:
            await client.aclose()
            raise HTTPException(
                status_code=503,
                detail="No available Zed accounts in pool (all in cooldown or unauthorized)",
            )

        headers = {
            "Authorization": f"Bearer {account.current_llm_token}",
            "Content-Type": "application/json",
            "x-zed-version": settings.zed_version,
            "x-zed-client-supports-status-messages": "true",
            "x-zed-client-supports-stream-ended-request-completion-status": "true",
            "User-Agent": f"Zed/{settings.zed_version}",
        }

        url = f"{settings.zed_api_url}/completions"
        try:
            req = client.build_request("POST", url, headers=headers, json=zed_body)
            resp = await client.send(req, stream=True)

            # Check rate limiting
            if resp.status_code == 429:
                await resp.aclose()
                pool.mark_rate_limited(account.id)
                retries += 1
                continue

            # Check token expiration / unauthorized
            if resp.status_code == 401 or "x-zed-expired-token" in resp.headers:
                await resp.aclose()
                # Try refresh
                try:
                    res = await pool.refresher.fetch_llm_token(
                        account.user_id, account.access_token, account.organization_id
                    )
                    account.current_llm_token = res["token"]
                    account.expires_at = res["expires_at"]
                except Exception as e:
                    pool.mark_unauthorized(account.id, str(e))
                retries += 1
                continue

            if resp.status_code >= 400:
                body_err = await resp.aread()
                await resp.aclose()
                pool.mark_rate_limited(account.id, cooldown_seconds=10)
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Zed Cloud error: {body_err.decode('utf-8', errors='ignore')}",
                )

            pool.mark_success(account.id)
            return resp, client, account

        except httpx.RequestError as e:
            logger.error(f"Network error contacting Zed Cloud for account {account.id}: {e}")
            retries += 1
            if retries >= max_retries:
                await client.aclose()
                raise HTTPException(status_code=502, detail=f"Bad Gateway: {str(e)}")

    await client.aclose()
    raise HTTPException(status_code=504, detail="Exhausted retries across Zed accounts")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    stream = body.get("stream", False)
    zed_body = openai_to_zed_body(body)

    resp, client, account = await forward_to_zed_cloud(zed_body, pool)

    if stream:
        async def event_generator() -> AsyncGenerator[bytes, None]:
            try:
                async for line in resp.aiter_lines():
                    delta = parse_zed_chunk_to_openai_delta(line)
                    if delta:
                        if delta == "[DONE]":
                            yield b"data: [DONE]\n\n"
                            break
                        yield f"data: {delta}\n\n".encode("utf-8")
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        # Non-streaming collection
        full_text = ""
        try:
            async for line in resp.aiter_lines():
                delta = parse_zed_chunk_to_openai_delta(line)
                if delta and delta != "[DONE]":
                    try:
                        d_obj = json.loads(delta)
                        choice = d_obj.get("choices", [{}])[0]
                        full_text += choice.get("delta", {}).get("content", "")
                    except Exception:
                        pass
        finally:
            await resp.aclose()
            await client.aclose()

        return JSONResponse(
            {
                "id": "chatcmpl-zed",
                "object": "chat.completion",
                "created": 1700000000,
                "model": body.get("model", "zed-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": full_text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )


@app.post("/v1/messages")
async def anthropic_messages(request: Request) -> Response:
    body = await request.json()
    stream = body.get("stream", False)
    zed_body = anthropic_to_zed_body(body)

    resp, client, account = await forward_to_zed_cloud(zed_body, pool)

    if stream:
        async def event_generator() -> AsyncGenerator[bytes, None]:
            try:
                async for line in resp.aiter_lines():
                    parsed = parse_zed_chunk_to_anthropic_event(line)
                    if parsed:
                        event_type, event_data = parsed
                        yield f"event: {event_type}\ndata: {event_data}\n\n".encode("utf-8")
                        if event_type == "message_stop":
                            break
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        # Non-streaming collection
        full_text = ""
        try:
            async for line in resp.aiter_lines():
                parsed = parse_zed_chunk_to_anthropic_event(line)
                if parsed:
                    event_type, event_data = parsed
                    if event_type == "content_block_delta":
                        try:
                            d_obj = json.loads(event_data)
                            full_text += d_obj.get("delta", {}).get("text", "")
                        except Exception:
                            pass
        finally:
            await resp.aclose()
            await client.aclose()

        return JSONResponse(
            {
                "id": "msg_zed",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": full_text}],
                "model": body.get("model", "claude-sonnet-5"),
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        )
