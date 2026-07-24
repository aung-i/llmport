"""Anthropic Messages API forwarding (thin wrapper over ``handler_base``)."""

import time

import httpx

from llmport.models.provider import ProviderConfig
from llmport.gateway.handler_base import forward as _forward, stream as _stream


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
) -> tuple[dict | None, str | None]:
    """Forward a non-streaming Messages request."""
    headers = _build_headers(provider)
    return await _forward(request_body, provider, model_name, path, headers)


async def stream(
    request_body: dict | bytes,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/messages",
):
    """Forward a streaming Messages request, yielding raw SSE bytes."""
    headers = _build_headers(provider)
    async for chunk in _stream(request_body, provider, model_name, path, headers):
        yield chunk


async def test_connection(
    provider: ProviderConfig,
) -> tuple[bool, float, str | None]:
    """Test connection to an Anthropic provider. Returns (ok, latency_ms, error)."""
    headers = _build_headers(provider)
    url = f"{provider.base_url.rstrip('/')}/v1/messages"
    body = {
        "model": (
            provider.models[0].name if provider.models else "claude-sonnet-5"
        ),
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            latency = (time.monotonic() - start) * 1000
            ok = resp.status_code < 500
            return ok, latency, None if ok else f"Status {resp.status_code}"
        except Exception as e:
            return False, 0.0, str(e)
