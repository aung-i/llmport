"""OpenAI-compatible request forwarding (thin wrapper over ``handler_base``)."""

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
    """Fetch available models from an OpenAI-compatible provider."""
    import httpx
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
