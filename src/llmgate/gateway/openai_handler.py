"""Forward OpenAI-compatible requests to provider."""

import httpx

from llmgate.models.provider import ProviderConfig


FORWARD_HEADERS = {"content-type", "authorization", "x-api-key", "x-request-id"}

ENDPOINTS = {
    "chat_completions": "/v1/chat/completions",
    "models": "/v1/models",
}


def _build_forward_headers(provider: ProviderConfig) -> dict:
    return {"Authorization": f"Bearer {provider.api_key}"}


def _filter_request_headers(headers: dict) -> dict:
    return {k.lower(): v for k, v in headers.items()
            if k.lower() not in FORWARD_HEADERS}


async def forward(
    request_body: dict,
    provider: ProviderConfig,
    model_name: str,
    path: str = "/v1/chat/completions",
) -> tuple[dict | None, str | None]:
    """Forward a non-streaming request. Returns (response_body, error)."""
    body = {**request_body, "model": model_name}
    url = f"{provider.base_url.rstrip('/')}{path}"
    headers = _build_forward_headers(provider)
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
    path: str = "/v1/chat/completions",
):
    """Forward a streaming request, yielding raw SSE bytes."""
    if isinstance(request_body, bytes):
        # If raw bytes, we need to inject the model override. Read the body,
        # override model, re-serialize.
        import json as _json
        body = _json.loads(request_body)
    else:
        body = request_body
    body["model"] = model_name
    body.setdefault("stream", True)

    url = f"{provider.base_url.rstrip('/')}{path}"
    headers = _build_forward_headers(provider)
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


async def list_models(provider: ProviderConfig) -> tuple[list[dict] | None, str | None]:
    """Fetch available models from an OpenAI-compatible provider."""
    url = f"{provider.base_url.rstrip('/')}/v1/models"
    headers = _build_forward_headers(provider)
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code < 400:
                data = resp.json()
                return data.get("data", []), None
            return None, f"Failed to fetch models: {resp.status_code}"
        except Exception as e:
            return None, str(e)
