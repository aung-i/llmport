"""Forward Anthropic Messages API requests to provider."""

import httpx

from llmgate.models.provider import ProviderConfig


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
    body = {**request_body, "model": model_name}
    url = f"{provider.base_url.rstrip('/')}{path}"
    headers = _build_headers(provider)
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code < 400:
                return resp.json(), None
            return None, f"Provider returned {resp.status_code}: {resp.text[:500]}"
        except httpx.TimeoutException:
            return None, "Provider timeout"
        except httpx.ConnectError:
            return None, "Provider unreachable"


async def stream(
    request_body: dict | bytes,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/messages",
):
    """Forward a streaming Messages request, yielding raw SSE/bytes."""
    import json as _json
    if isinstance(request_body, bytes):
        body = _json.loads(request_body)
    else:
        body = request_body
    body["model"] = model_name
    body.setdefault("stream", True)

    url = f"{provider.base_url.rstrip('/')}{path}"
    headers = _build_headers(provider)
    headers["Accept"] = "text/event-stream"
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    error_body = await resp.aread()
                    yield f"data: [ERROR] Provider {resp.status_code}: {error_body.decode()[:200]}\n\n".encode()
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.TimeoutException:
            yield b"data: [ERROR] Provider timeout\n\n"
        except httpx.ConnectError:
            yield b"data: [ERROR] Provider unreachable\n\n"


async def test_connection(provider: ProviderConfig) -> tuple[bool, float, str | None]:
    """Test connection to an Anthropic provider. Returns (ok, latency_ms, error)."""
    import time
    headers = _build_headers(provider)
    url = f"{provider.base_url.rstrip('/')}/v1/messages"
    body = {
        "model": (provider.models[0].name if provider.models else "claude-sonnet-5"),
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
