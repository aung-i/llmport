"""Shared HTTP forwarding logic for protocol handlers.

Provides the generic ``forward()`` and ``stream()`` functions used by both
``openai_handler`` and ``anthropic_handler``.  Each handler passes its own
headers and defaults so the call sites in ``server.py`` remain unchanged.
"""

import httpx

from llmport.models.provider import ProviderConfig
from llmport.gateway.ip_utils import validate_public_url


async def forward(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str,
    headers: dict,
    timeout: float = 120.0,
) -> tuple[dict | None, str | None]:
    """Forward a non-streaming request.  Returns ``(response_body, error)``."""
    body = {**request_body, "model": model_name}
    url = f"{provider.base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                url, json=body, headers=headers, allow_redirects=False
            )
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
    path: str,
    headers: dict,
    timeout: float = 300.0,
):
    """Forward a streaming request, yielding raw SSE bytes."""
    import json as _json
    if isinstance(request_body, bytes):
        body = _json.loads(request_body)
    else:
        body = request_body
    body["model"] = model_name
    body.setdefault("stream", True)

    url = f"{provider.base_url.rstrip('/')}{path}"
    forward_headers = {**headers, "Accept": "text/event-stream"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream(
                "POST",
                url,
                json=body,
                headers=forward_headers,
                allow_redirects=False,
            ) as resp:
                if resp.status_code >= 400:
                    error_body = await resp.aread()
                    yield (
                        f"data: [ERROR] Provider {resp.status_code}:"
                        f" {error_body.decode()[:200]}\n\n"
                    ).encode()
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.TimeoutException:
            yield b"data: [ERROR] Provider timeout\n\n"
        except httpx.ConnectError:
            yield b"data: [ERROR] Provider unreachable\n\n"
