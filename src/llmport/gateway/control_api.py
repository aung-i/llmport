"""Control API endpoints for the gateway daemon.

Every endpoint is mounted under ``/api/*`` on the gateway port.
"""

import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from llmport.models.provider import ProviderConfig
from llmport.gateway.state import get_state
from llmport.gateway.ip_utils import validate_public_url
from llmport.gateway import openai_handler, anthropic_handler

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def control_status(request: Request) -> JSONResponse:
    """Return current daemon status including stats and provider health."""
    state = get_state()
    return JSONResponse({
        "uptime": time.time() - state.started_at,
        "request_count": state.request_count,
        "total_tokens": state.total_tokens,
        "provider_count": len(state.providers),
        "model_count": len(state.models),
        "gateway": state.gateway,
        "models": [m.name for m in state.models],
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "status": p.health.status,
                "latency_ms": p.health.latency_ms,
            }
            for p in state.providers
        ],
    })


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


async def control_models(request: Request) -> JSONResponse:
    """Return the list of configured models with their bindings."""
    state = get_state()
    return JSONResponse({
        "models": [
            {
                "name": m.name,
                "provider_count": m.provider_count,
                "routing_strategy": m.routing_strategy,
                "bindings": [
                    {
                        "provider": b.provider,
                        "upstream": b.upstream,
                        "priority": b.priority,
                    }
                    for b in m.bindings_sorted
                ],
            }
            for m in state.models
        ],
    })


async def control_models_delete(request: Request) -> JSONResponse:
    """Delete a configured model by name."""
    state = get_state()
    body = await request.json()
    name = body.get("name") or body.get("model_id")
    if not name:
        return JSONResponse(
            {"ok": False, "error": "Missing model name"}, status_code=400
        )
    state.models = [m for m in state.models if m.name != name]
    state.save()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def control_providers(request: Request) -> JSONResponse:
    """CRUD for provider configurations.

    API keys are stored in the encrypted secrets vault, never in the readable
    config. A ``"***"`` api_key means "keep the existing key".
    """
    state = get_state()
    if request.method == "GET":
        return JSONResponse([
            p.to_dict(include_key=False) for p in state.providers
        ])
    elif request.method == "POST":
        body = await request.json()
        # SSRF: validate base_url
        if not validate_public_url(body.get("base_url", "")):
            return JSONResponse(
                {"ok": False, "error": "不允许使用内网/本地地址"},
                status_code=400,
            )
        # Protect existing API key when the UI sends "***"
        raw_key = body.get("api_key")
        if raw_key == "***":
            existing = {p.id: p for p in state.providers}
            if body.get("id") and body["id"] in existing:
                stored = existing[body["id"]]
                if body.get("base_url") != stored.base_url:
                    return JSONResponse(
                        {"error": "base_url mismatch"}, status_code=400
                    )
                body["api_key"] = stored.api_key
        elif raw_key == "":
            # Empty string means "clear the key"
            body["api_key"] = ""
        provider = ProviderConfig.from_dict(body)
        state.providers = [
            p for p in state.providers if p.id != provider.id
        ] + [provider]
        state.save()
        return JSONResponse({"ok": True})
    elif request.method == "DELETE":
        body = await request.json()
        provider_id = body.get("id")
        if not provider_id:
            return JSONResponse(
                {"ok": False, "error": "Missing provider id"},
                status_code=400,
            )
        state.providers = [p for p in state.providers if p.id != provider_id]
        state.save()
        return JSONResponse({"ok": True})


async def control_test_provider(request: Request) -> JSONResponse:
    """Test a provider connection."""
    body = await request.json()
    # Resolve "***" sentinel - look up real key from stored providers
    raw_key = body.get("api_key")
    if raw_key == "***":
        state = get_state()
        existing = {p.id: p for p in state.providers}
        provider_id = body.get("id", "")
        if provider_id in existing:
            stored = existing[provider_id]
            if body.get("base_url") != stored.base_url:
                return JSONResponse({"error": "base_url mismatch"}, status_code=400)
            body["api_key"] = stored.api_key
    provider = ProviderConfig.from_dict(body)
    if provider.protocol == "openai":
        t0 = time.monotonic()
        models, error = await openai_handler.list_models(provider)
        latency = (time.monotonic() - t0) * 1000
        ok = models is not None
    else:
        ok, latency, error = await anthropic_handler.test_connection(provider)
    return JSONResponse({"ok": ok, "latency_ms": latency, "error": error})


async def control_fetch_models(request: Request) -> JSONResponse:
    """Fetch model list from a provider."""
    body = await request.json()
    # Resolve "***" sentinel - look up real key from stored providers
    raw_key = body.get("api_key")
    if raw_key == "***":
        state = get_state()
        existing = {p.id: p for p in state.providers}
        provider_id = body.get("id", "")
        if provider_id in existing:
            stored = existing[provider_id]
            if body.get("base_url") != stored.base_url:
                return JSONResponse({"error": "base_url mismatch"}, status_code=400)
            body["api_key"] = stored.api_key
    provider = ProviderConfig.from_dict(body)
    if provider.protocol == "openai":
        models, error = await openai_handler.list_models(provider)
    else:
        models, error = None, "Anthropic does not expose a model list API"
    return JSONResponse({"models": models, "error": error})


# ---------------------------------------------------------------------------
# Gateway configuration
# ---------------------------------------------------------------------------


async def control_gateway_config(request: Request) -> JSONResponse:
    """Get or update gateway configuration."""
    state = get_state()
    if request.method == "GET":
        return JSONResponse(state.gateway)
    elif request.method == "POST":
        body = await request.json()
        host = body.get("host", "127.0.0.1").strip()
        port = int(body.get("port", 11434))

        # Validate host is non-empty and reasonable length
        if not host or len(host) > 253:
            return JSONResponse(
                {"ok": False, "error": f"无效的主机地址: {host}"},
                status_code=400,
            )

        # Reject non-loopback addresses
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse(
                {"ok": False, "error": "仅支持本地回环地址 (127.0.0.1 / localhost / ::1)"},
                status_code=400,
            )

        if not (1024 <= port <= 65535):
            return JSONResponse(
                {"ok": False, "error": f"端口号超出范围: {port} (1024-65535)"},
                status_code=400,
            )

        state.gateway = {"host": host, "port": port}
        state.save()
        return JSONResponse({"ok": True, "gateway": state.gateway})


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------

# The uvicorn Server instance, set by run_daemon() so the control API can
# trigger a graceful shutdown by flipping ``should_exit``.
_shutdown_server = None


def set_shutdown_server(server) -> None:
    """Register the running uvicorn server for graceful shutdown."""
    global _shutdown_server
    _shutdown_server = server


async def control_daemon_stop(request: Request) -> JSONResponse:
    """Initiate graceful shutdown by signalling the uvicorn server to exit."""
    if _shutdown_server is not None:
        _shutdown_server.should_exit = True
    return JSONResponse({"ok": True})


async def control_daemon_restart(request: Request) -> JSONResponse:
    """Signal the launcher to restart the gateway."""
    return JSONResponse({"ok": True, "action": "restart"})
