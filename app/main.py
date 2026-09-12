import asyncio
import json
import logging
import time
import uuid
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
    make_openai_chunk,
    openai_to_zed_body,
    parse_zed_chunk_to_openai_delta,
)
from app.adapters.responses_adapter import responses_to_zed_body
from app.config import settings
from app.core.account_pool import Account, AccountPool
from app.core.security import security_policy
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
    if not pool.accounts:
        example_path = settings.accounts_file.parent / "accounts.example.json"
        if example_path.exists():
            logger.info(f"Loading fallback template accounts from {example_path}")
            pool.load_from_file(example_path)

    app.state.pool = pool
    app.state.client = httpx.AsyncClient(
        timeout=120.0,
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=200),
    )
    await pool.start_background_loop()
    yield
    # Shutdown
    logger.info("Tearing down Zed AI Proxy...")
    await pool.stop_background_loop()
    if hasattr(app.state, "client") and app.state.client:
        await app.state.client.aclose()


app = FastAPI(
    title="Zed AI Multi-Account Proxy",
    description="Reverse proxy for Zed AI models with account pooling and auto-refresh",
    version="0.1.0",
    lifespan=lifespan,
)

# Attach pool immediately for lightweight/test environments
app.state.pool = pool

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status_router)


@app.exception_handler(HTTPException)
async def standard_http_exception_handler(request: Request, exc: HTTPException):
    """Returns standard RFC-compliant error format expected by OpenAI and Anthropic SDKs."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": str(exc.detail),
                "type": "invalid_request_error" if exc.status_code < 500 else "api_error",
                "code": exc.status_code,
            }
        },
    )


MODELS_METADATA = [
    {"id": "claude-opus-5", "object": "model", "owned_by": "zed"},
    {"id": "claude-sonnet-5", "object": "model", "owned_by": "zed"},
    {"id": "claude-haiku-4-5", "object": "model", "owned_by": "zed"},
    {"id": "claude-fable-5", "object": "model", "owned_by": "zed"},
    {"id": "gpt-5.6-luna", "object": "model", "owned_by": "zed"},
    {"id": "gpt-5.6-sol", "object": "model", "owned_by": "zed"},
    {"id": "gemini-3.1-pro", "object": "model", "owned_by": "zed"},
    {"id": "gemini-3-flash", "object": "model", "owned_by": "zed"},
    # Common client aliases for Claude Code / Codex / Cursor
    {"id": "claude-3-7-sonnet-20250219", "object": "model", "owned_by": "zed"},
    {"id": "claude-3-5-sonnet-20241022", "object": "model", "owned_by": "zed"},
    {"id": "claude-3-5-sonnet-latest", "object": "model", "owned_by": "zed"},
    {"id": "claude-3-opus-20240229", "object": "model", "owned_by": "zed"},
    {"id": "claude-3-5-haiku-20241022", "object": "model", "owned_by": "zed"},
    {"id": "gpt-4o", "object": "model", "owned_by": "zed"},
    {"id": "gpt-4o-mini", "object": "model", "owned_by": "zed"},
    {"id": "o1", "object": "model", "owned_by": "zed"},
    {"id": "o3-mini", "object": "model", "owned_by": "zed"},
]


@app.get("/v1/models")
@app.get("/models")
async def list_models() -> Dict[str, Any]:
    return {"object": "list", "data": MODELS_METADATA}


@app.get("/v1/models/{model_id}")
@app.get("/models/{model_id}")
async def get_model(model_id: str) -> Dict[str, Any]:
    for m in MODELS_METADATA:
        if m["id"] == model_id:
            return m
    raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")


def extract_usage_from_sse_line(raw_line: str) -> Dict[str, int]:
    """Extracts input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens from SSE line."""
    line = raw_line.strip()
    if not line or not line.startswith("data:"):
        return {}
    line = line[5:].strip()
    if not line or line == "[DONE]":
        return {}
    try:
        data = json.loads(line)
        if not isinstance(data, dict):
            return {}

        event = data.get("event")
        if not isinstance(event, dict):
            event = data

        ev_type = event.get("type")
        res: Dict[str, int] = {}

        if ev_type == "message_start":
            u = event.get("message", {}).get("usage", {}) or event.get("usage", {})
            if isinstance(u, dict):
                if u.get("input_tokens") is not None:
                    res["input_tokens"] = int(u["input_tokens"])
                if u.get("cache_creation_input_tokens") is not None:
                    res["cache_write_tokens"] = int(u["cache_creation_input_tokens"])
                if u.get("cache_read_input_tokens") is not None:
                    res["cache_read_tokens"] = int(u["cache_read_input_tokens"])
                if u.get("output_tokens") is not None:
                    res["output_tokens"] = int(u["output_tokens"])
            return res

        if ev_type == "message_delta":
            u = event.get("usage", {})
            if isinstance(u, dict):
                if u.get("output_tokens") is not None:
                    res["output_tokens"] = int(u["output_tokens"])
                if u.get("input_tokens") is not None:
                    res["input_tokens"] = int(u["input_tokens"])
                if u.get("cache_creation_input_tokens") is not None:
                    res["cache_write_tokens"] = int(u["cache_creation_input_tokens"])
                if u.get("cache_read_input_tokens") is not None:
                    res["cache_read_tokens"] = int(u["cache_read_input_tokens"])
            return res

        # Fallback for OpenAI or root usage object
        usage = data.get("usage") or event.get("usage")
        if usage and isinstance(usage, dict):
            in_tok = usage.get("prompt_tokens") or usage.get("input_tokens")
            out_tok = usage.get("completion_tokens") or usage.get("output_tokens")
            c_read = usage.get("prompt_tokens_details", {}).get("cached_tokens") if isinstance(usage.get("prompt_tokens_details"), dict) else None
            if in_tok is not None:
                res["input_tokens"] = int(in_tok)
            if out_tok is not None:
                res["output_tokens"] = int(out_tok)
            if c_read is not None:
                res["cache_read_tokens"] = int(c_read)
            return res

        return {}
    except Exception:
        return {}


async def forward_to_zed_cloud(
    zed_body: Dict[str, Any],
    pool: AccountPool,
    client: Optional[httpx.AsyncClient] = None,
    max_retries: int = 3,
) -> Tuple[httpx.Response, Account]:
    """Forward completion request to Zed Cloud with automatic account failover."""
    http_client = client or httpx.AsyncClient(timeout=120.0)
    retries = 0

    while retries < max_retries:
        account = await pool.get_next_account()
        if not account:
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
            req = http_client.build_request("POST", url, headers=headers, json=zed_body)
            resp = await http_client.send(req, stream=True)

            # 429 Rate Limit
            if resp.status_code == 429:
                await resp.aclose()
                pool.mark_rate_limited(account.id)
                retries += 1
                continue

            # 401 Unauthorized or Token Expired
            if resp.status_code == 401 or "x-zed-expired-token" in resp.headers:
                await resp.aclose()
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

            # Upstream error (5xx or client 4xx)
            if resp.status_code >= 400:
                body_err = await resp.aread()
                await resp.aclose()
                # DO NOT penalize account on client-side errors (400, 404, 422)
                if resp.status_code >= 500:
                    pool.mark_rate_limited(account.id, cooldown_seconds=15)

                err_msg = body_err.decode("utf-8", errors="ignore")[:300]
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Zed Cloud error: {err_msg}",
                )

            pool.mark_success(account.id)
            return resp, account

        except httpx.RequestError as e:
            logger.error(f"Network error contacting Zed Cloud for account {account.id}: {e}")
            retries += 1
            if retries >= max_retries:
                raise HTTPException(status_code=502, detail=f"Bad Gateway: {str(e)}")

    raise HTTPException(status_code=504, detail="Exhausted retries across Zed accounts")


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    security_policy.verify_client_auth(request)
    body = security_policy.sanitize_request_tools(body)
    if settings.strict_isolation:
        body = security_policy.inject_isolation_prompt(body)

    stream = body.get("stream", False)
    model = body.get("model", "claude-sonnet-5")
    req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    zed_body = openai_to_zed_body(body)

    shared_client = getattr(request.app.state, "client", None)
    resp, account = await forward_to_zed_cloud(zed_body, pool, client=shared_client)

    sse_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    if stream:
        async def event_generator() -> AsyncGenerator[bytes, None]:
            role_sent = False
            done_sent = False
            full_text = ""
            in_tok, out_tok, c_write, c_read = 0, 0, 0, 0
            try:
                # 1. First chunk establishes role: assistant
                initial_chunk = make_openai_chunk(req_id, model, {"role": "assistant"})
                yield f"data: {initial_chunk}\n\n".encode("utf-8")
                role_sent = True

                async for line in resp.aiter_lines():
                    u = extract_usage_from_sse_line(line)
                    if "input_tokens" in u:
                        in_tok = u["input_tokens"]
                    if "output_tokens" in u:
                        out_tok = u["output_tokens"]
                    if "cache_write_tokens" in u:
                        c_write = u["cache_write_tokens"]
                    if "cache_read_tokens" in u:
                        c_read = u["cache_read_tokens"]

                    parsed = parse_zed_chunk_to_openai_delta(line, req_id=req_id, model=model)
                    if parsed:
                        delta_str, finish_reason = parsed
                        if delta_str == "[DONE]":
                            term_chunk = make_openai_chunk(req_id, model, {}, finish_reason="stop")
                            yield f"data: {term_chunk}\n\n".encode("utf-8")
                            yield b"data: [DONE]\n\n"
                            done_sent = True
                            break
                        try:
                            d_obj = json.loads(delta_str)
                            c = d_obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if c:
                                full_text += c
                        except Exception:
                            pass
                        yield f"data: {delta_str}\n\n".encode("utf-8")

                if not done_sent:
                    term_chunk = make_openai_chunk(req_id, model, {}, finish_reason="stop")
                    yield f"data: {term_chunk}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"

            finally:
                in_tok = in_tok or max(1, len(json.dumps(body)) // 4)
                out_tok = out_tok or max(1, len(full_text) // 4)
                account.record_usage(
                    in_tok,
                    out_tok,
                    model=model,
                    cache_write_tokens=c_write,
                    cache_read_tokens=c_read,
                )
                await resp.aclose()

        return StreamingResponse(event_generator(), headers=sse_headers)
    else:
        # Non-streaming collection
        full_text = ""
        tool_calls: List[Dict[str, Any]] = []
        finish_reason = "stop"
        in_tok, out_tok, c_write, c_read = 0, 0, 0, 0

        try:
            async for line in resp.aiter_lines():
                u = extract_usage_from_sse_line(line)
                if "input_tokens" in u:
                    in_tok = u["input_tokens"]
                if "output_tokens" in u:
                    out_tok = u["output_tokens"]
                if "cache_write_tokens" in u:
                    c_write = u["cache_write_tokens"]
                if "cache_read_tokens" in u:
                    c_read = u["cache_read_tokens"]

                parsed = parse_zed_chunk_to_openai_delta(line, req_id=req_id, model=model)
                if parsed:
                    delta_str, f_reason = parsed
                    if f_reason:
                        finish_reason = f_reason
                    if delta_str and delta_str != "[DONE]":
                        try:
                            d_obj = json.loads(delta_str)
                            choice = d_obj.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            if "content" in delta:
                                full_text += delta.get("content") or ""
                            if "tool_calls" in delta:
                                tool_calls.extend(delta.get("tool_calls") or [])
                        except Exception:
                            pass
        finally:
            await resp.aclose()

        in_tok = in_tok or max(1, len(json.dumps(body)) // 4)
        out_tok = out_tok or max(1, len(full_text) // 4)
        account.record_usage(
            in_tok,
            out_tok,
            model=model,
            cache_write_tokens=c_write,
            cache_read_tokens=c_read,
        )

        msg_payload: Dict[str, Any] = {"role": "assistant", "content": full_text}
        if tool_calls:
            msg_payload["tool_calls"] = tool_calls
            finish_reason = "tool_calls"

        return JSONResponse(
            {
                "id": req_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": msg_payload,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {
                    "prompt_tokens": in_tok,
                    "completion_tokens": out_tok,
                    "total_tokens": in_tok + out_tok,
                },
            }
        )


@app.post("/v1/messages")
@app.post("/messages")
async def anthropic_messages(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    security_policy.verify_client_auth(request)
    body = security_policy.sanitize_request_tools(body)
    if settings.strict_isolation:
        body = security_policy.inject_isolation_prompt(body)

    stream = body.get("stream", False)
    model = body.get("model", "claude-sonnet-5")
    zed_body = anthropic_to_zed_body(body)

    shared_client = getattr(request.app.state, "client", None)
    resp, account = await forward_to_zed_cloud(zed_body, pool, client=shared_client)

    sse_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    if stream:
        async def event_generator() -> AsyncGenerator[bytes, None]:
            done_sent = False
            total_chars = 0
            in_tok, out_tok, c_write, c_read = 0, 0, 0, 0
            try:
                async for line in resp.aiter_lines():
                    u = extract_usage_from_sse_line(line)
                    if "input_tokens" in u:
                        in_tok = u["input_tokens"]
                    if "output_tokens" in u:
                        out_tok = u["output_tokens"]
                    if "cache_write_tokens" in u:
                        c_write = u["cache_write_tokens"]
                    if "cache_read_tokens" in u:
                        c_read = u["cache_read_tokens"]

                    parsed = parse_zed_chunk_to_anthropic_event(line)
                    if parsed:
                        event_type, event_data = parsed
                        total_chars += len(event_data)
                        yield f"event: {event_type}\ndata: {event_data}\n\n".encode("utf-8")
                        if event_type == "message_stop":
                            done_sent = True
                            break

                if not done_sent:
                    yield b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
            finally:
                in_tok = in_tok or max(1, len(json.dumps(body)) // 4)
                out_tok = out_tok or max(1, total_chars // 8)
                account.record_usage(
                    in_tok,
                    out_tok,
                    model=model,
                    cache_write_tokens=c_write,
                    cache_read_tokens=c_read,
                )
                await resp.aclose()

        return StreamingResponse(event_generator(), headers=sse_headers)
    else:
        # Non-streaming collection
        full_text = ""
        stop_reason = "end_turn"
        tool_calls: List[Dict[str, Any]] = []
        msg_id = f"msg_{uuid.uuid4().hex[:16]}"
        input_tokens, output_tokens, c_write, c_read = 0, 0, 0, 0

        try:
            async for line in resp.aiter_lines():
                u = extract_usage_from_sse_line(line)
                if "input_tokens" in u:
                    input_tokens = u["input_tokens"]
                if "output_tokens" in u:
                    output_tokens = u["output_tokens"]
                if "cache_write_tokens" in u:
                    c_write = u["cache_write_tokens"]
                if "cache_read_tokens" in u:
                    c_read = u["cache_read_tokens"]

                parsed = parse_zed_chunk_to_anthropic_event(line)
                if parsed:
                    event_type, event_data = parsed
                    try:
                        d_obj = json.loads(event_data)
                    except Exception:
                        d_obj = {}

                    if event_type == "message_start":
                        msg_info = d_obj.get("message", {})
                        if "id" in msg_info:
                            msg_id = msg_info["id"]
                        usage = msg_info.get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)

                    elif event_type == "content_block_start":
                        cb = d_obj.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "type": "tool_use",
                                    "id": cb.get("id"),
                                    "name": cb.get("name"),
                                    "input": {},
                                }
                            )

                    elif event_type == "content_block_delta":
                        delta = d_obj.get("delta", {})
                        if delta.get("type") == "text_delta":
                            full_text += delta.get("text", "")
                        elif delta.get("type") == "input_json_delta":
                            # Partial json for tool calls
                            if tool_calls:
                                pass

                    elif event_type == "message_delta":
                        delta_info = d_obj.get("delta", {})
                        if "stop_reason" in delta_info:
                            stop_reason = delta_info["stop_reason"]
                        usage = d_obj.get("usage", {})
                        output_tokens = usage.get("output_tokens", 0)

        finally:
            await resp.aclose()

        in_tok = input_tokens or max(1, len(json.dumps(body)) // 4)
        out_tok = output_tokens or max(1, len(full_text) // 4)
        account.record_usage(
            in_tok,
            out_tok,
            model=model,
            cache_write_tokens=c_write,
            cache_read_tokens=c_read,
        )

        content_list: List[Dict[str, Any]] = []
        if full_text:
            content_list.append({"type": "text", "text": full_text})
        if tool_calls:
            content_list.extend(tool_calls)

        return JSONResponse(
            {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": content_list if content_list else [{"type": "text", "text": ""}],
                "model": model,
                "stop_reason": stop_reason,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            }
        )


@app.post("/v1/responses")
@app.post("/responses")
async def responses_endpoint(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body")

    security_policy.verify_client_auth(request)
    body = security_policy.sanitize_request_tools(body)
    if settings.strict_isolation:
        body = security_policy.inject_isolation_prompt(body)

    stream = body.get("stream", True)
    model = body.get("model", "claude-sonnet-5")
    resp_id = f"resp_{uuid.uuid4().hex[:16]}"
    item_id = f"item_{uuid.uuid4().hex[:16]}"
    zed_body = responses_to_zed_body(body)

    shared_client = getattr(request.app.state, "client", None)
    resp, account = await forward_to_zed_cloud(zed_body, pool, client=shared_client)

    sse_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    if stream:
        async def event_generator() -> AsyncGenerator[bytes, None]:
            full_text = ""
            try:
                # 1. response.created
                ev_created = {
                    "response": {
                        "id": resp_id,
                        "object": "response",
                        "status": "in_progress",
                        "model": model,
                    }
                }
                yield f"event: response.created\ndata: {json.dumps(ev_created)}\n\n".encode("utf-8")

                # 2. response.output_item.added
                ev_item = {
                    "response_id": resp_id,
                    "output_index": 0,
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                }
                yield f"event: response.output_item.added\ndata: {json.dumps(ev_item)}\n\n".encode("utf-8")

                # 3. response.content_part.added
                ev_part = {
                    "response_id": resp_id,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "text", "text": ""},
                }
                yield f"event: response.content_part.added\ndata: {json.dumps(ev_part)}\n\n".encode("utf-8")

                # 4. Stream tokens
                in_tok, out_tok, c_write, c_read = 0, 0, 0, 0
                async for line in resp.aiter_lines():
                    u = extract_usage_from_sse_line(line)
                    if "input_tokens" in u:
                        in_tok = u["input_tokens"]
                    if "output_tokens" in u:
                        out_tok = u["output_tokens"]
                    if "cache_write_tokens" in u:
                        c_write = u["cache_write_tokens"]
                    if "cache_read_tokens" in u:
                        c_read = u["cache_read_tokens"]

                    parsed = parse_zed_chunk_to_openai_delta(line, req_id=resp_id, model=model)
                    if parsed:
                        delta_str, finish_reason = parsed
                        if delta_str == "[DONE]":
                            break
                        try:
                            d_obj = json.loads(delta_str)
                            delta_c = d_obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta_c:
                                full_text += delta_c
                                ev_delta = {
                                    "response_id": resp_id,
                                    "item_id": item_id,
                                    "output_index": 0,
                                    "content_index": 0,
                                    "delta": delta_c,
                                }
                                yield f"event: response.output_text.delta\ndata: {json.dumps(ev_delta)}\n\n".encode("utf-8")
                        except Exception:
                            pass

                # 5. response.output_text.done
                ev_text_done = {
                    "response_id": resp_id,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "text": full_text,
                }
                yield f"event: response.output_text.done\ndata: {json.dumps(ev_text_done)}\n\n".encode("utf-8")

                # 6. response.content_part.done
                ev_part_done = {
                    "response_id": resp_id,
                    "item_id": item_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "text", "text": full_text},
                }
                yield f"event: response.content_part.done\ndata: {json.dumps(ev_part_done)}\n\n".encode("utf-8")

                # 7. response.output_item.done
                ev_item_done = {
                    "response_id": resp_id,
                    "output_index": 0,
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "text", "text": full_text}],
                    },
                }
                yield f"event: response.output_item.done\ndata: {json.dumps(ev_item_done)}\n\n".encode("utf-8")

                # 8. response.completed
                ev_completed = {
                    "response": {
                        "id": resp_id,
                        "object": "response",
                        "status": "completed",
                        "model": model,
                        "output": [ev_item_done["item"]],
                    }
                }
                yield f"event: response.completed\ndata: {json.dumps(ev_completed)}\n\n".encode("utf-8")

            finally:
                in_tok = in_tok or max(1, len(json.dumps(body)) // 4)
                out_tok = out_tok or max(1, len(full_text) // 4)
                account.record_usage(
                    in_tok,
                    out_tok,
                    model=model,
                    cache_write_tokens=c_write,
                    cache_read_tokens=c_read,
                )
                await resp.aclose()

        return StreamingResponse(event_generator(), headers=sse_headers)
    else:
        full_text = ""
        in_tok, out_tok, c_write, c_read = 0, 0, 0, 0
        try:
            async for line in resp.aiter_lines():
                u = extract_usage_from_sse_line(line)
                if "input_tokens" in u:
                    in_tok = u["input_tokens"]
                if "output_tokens" in u:
                    out_tok = u["output_tokens"]
                if "cache_write_tokens" in u:
                    c_write = u["cache_write_tokens"]
                if "cache_read_tokens" in u:
                    c_read = u["cache_read_tokens"]

                parsed = parse_zed_chunk_to_openai_delta(line, req_id=resp_id, model=model)
                if parsed:
                    delta_str, _ = parsed
                    if delta_str != "[DONE]":
                        try:
                            d_obj = json.loads(delta_str)
                            delta_c = d_obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta_c:
                                full_text += delta_c
                        except Exception:
                            pass
        finally:
            await resp.aclose()

        in_tok = in_tok or max(1, len(json.dumps(body)) // 4)
        out_tok = out_tok or max(1, len(full_text) // 4)
        account.record_usage(
            in_tok,
            out_tok,
            model=model,
            cache_write_tokens=c_write,
            cache_read_tokens=c_read,
        )

        return JSONResponse({
            "id": resp_id,
            "object": "response",
            "status": "completed",
            "model": model,
            "output": [
                {
                    "id": item_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "text", "text": full_text}],
                }
            ],
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
        })

