"""OpenAI-compatible request forwarding (thin wrapper over ``handler_base``)."""

import time

import httpx

from llmport.models.provider import ProviderConfig
from llmport.gateway.handler_base import forward as _forward, stream as _stream

ENDPOINTS = {
    "chat_completions": "/v1/chat/completions",
    "models": "/v1/models",
}


def _build_headers(provider: ProviderConfig) -> dict:
    return {"Authorization": f"Bearer {provider.api_key}"}


async def forward(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/chat/completions",
) -> tuple[dict | None, str | None]:
    """Forward a non-streaming OpenAI request."""
    headers = _build_headers(provider)
    return await _forward(request_body, provider, model_name, path, headers)


async def stream(
    request_body: dict | bytes,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/chat/completions",
):
    """Forward a streaming OpenAI request, yielding raw SSE bytes."""
    headers = _build_headers(provider)
    async for chunk in _stream(request_body, provider, model_name, path, headers):
        yield chunk


async def list_models(
    provider: ProviderConfig,
) -> tuple[list[dict] | None, str | None]:
    """Fetch available models from an OpenAI-compatible provider.

    Only proves the URL is reachable and gathers model ids -- many
    OpenAI-compatible servers serve ``/v1/models`` without checking the
    key, so a bad key can still return 200 here. Use :func:`test_connection`
    to verify the key itself.
    """
    url = f"{provider.base_url.rstrip('/')}/v1/models"
    headers = _build_headers(provider)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code < 400:
                data = resp.json()
                return data.get("data", []), None
            return None, f"Failed to fetch models: {resp.status_code}"
        except Exception as e:
            return None, str(e)


async def test_connection(
    provider: ProviderConfig,
    model_name: str,
) -> tuple[bool, float, str | None, str | None]:
    """Verify the key (and that the model is usable) via a minimal request.

    The chat-completions endpoint enforces auth: 2xx means the key works and
    the model exists; 401/403 means the key is bad; 404 means the model name
    doesn't exist (the key is fine -- it's a model-name mismatch). The prompt
    asks for a one-word reply ("有效"). ``max_tokens`` is 128 -- reasoning
    models (e.g. DeepSeek-V4) spend tokens in ``reasoning_content`` before
    emitting ``content``, so a tiny budget leaves ``content`` empty; 128 lets
    them finish and actually reply "有效" (real cost is the generated token
    count, ~30-80, not the cap). Returns ``(ok, latency_ms, error, reply)``;
    ``reply`` falls back to ``reasoning_content`` when ``content`` is empty
    (truncated reasoning), else None.
    """
    headers = _build_headers(provider)
    url = f"{provider.base_url.rstrip('/')}/v1/chat/completions"
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
            if resp.status_code < 400:
                reply = None
                try:
                    msg = resp.json()["choices"][0]["message"]
                    # reasoning models may leave ``content`` empty (truncated)
                    # and put text in ``reasoning_content`` instead
                    reply = msg.get("content") or msg.get("reasoning_content") or None
                except (ValueError, KeyError, IndexError, TypeError):
                    pass
                return True, latency, None, reply
            if resp.status_code in (401, 403):
                return False, latency, f"key 无效 ({resp.status_code})", None
            if resp.status_code == 404:
                return False, latency, f"模型 {model_name} 不存在 (404)", None
            return False, latency, f"上游返回 {resp.status_code}", None
        except Exception as e:
            return False, 0.0, str(e), None
