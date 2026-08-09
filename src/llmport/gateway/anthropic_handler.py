"""Anthropic Messages API forwarding (thin wrapper over ``handler_base``)."""

import time

import httpx

from llmport.models.provider import ProviderConfig
from llmport.gateway.handler_base import (
    forward as _forward,
    open_stream as _open_stream,
    UpstreamResult,
    OpenedStream,
)


def _build_headers(provider: ProviderConfig) -> dict:
    return {
        "x-api-key": provider.api_key,
        "anthropic-version": "2023-06-01",
    }


async def forward(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/messages",
) -> UpstreamResult:
    """Forward a non-streaming Messages request."""
    headers = _build_headers(provider)
    return await _forward(request_body, provider, model_name, path, headers)


async def open_stream(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/messages",
) -> OpenedStream | str:
    """Open a streaming Messages request (caller peeks status before piping)."""
    headers = _build_headers(provider)
    return await _open_stream(request_body, provider, model_name, path, headers)


async def test_connection(
    provider: ProviderConfig,
    model_name: str = "claude-sonnet-5",
) -> tuple[bool, float, str | None, str | None]:
    """Verify the key (and that the model is usable) via a minimal request.

    The messages endpoint enforces auth: 2xx means the key works and the model
    exists; 401/403 means the key is bad; 404 means the model name isn't served
    here (the key is fine -- it's a model-name mismatch, not an auth failure).
    The prompt asks for a one-word reply ("有效"). ``max_tokens`` is 128 so
    reasoning/thinking models have room to finish and emit the text reply (a
    tiny budget leaves only a thinking block, no text). Returns ``(ok,
    latency_ms, error, reply)``; ``reply`` is the first ``text`` content block
    (best-effort, None on failure or non-standard body).
    """
    headers = _build_headers(provider)
    url = f"{provider.base_url.rstrip('/')}/v1/messages"
    body = {
        "model": model_name,
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "只回复：有效"}],
    }
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            latency = (time.monotonic() - start) * 1000
            code = resp.status_code
            if code < 400:
                reply = None
                try:
                    # thinking models put a "thinking" block first; take the
                    # first actual "text" block as the reply
                    blocks = resp.json()["content"]
                    reply = next(
                        (b["text"] for b in blocks
                         if isinstance(b, dict) and b.get("type") == "text"
                         and b.get("text")),
                        None,
                    )
                except (ValueError, KeyError, IndexError, TypeError):
                    pass
                return True, latency, None, reply
            if code in (401, 403):
                return False, latency, f"key 无效 ({code})", None
            if code == 404:
                return False, latency, f"模型 {model_name} 不存在 (404)", None
            return False, latency, f"上游返回 {code}", None
        except Exception as e:
            return False, 0.0, str(e), None
