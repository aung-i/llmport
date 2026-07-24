"""Control API endpoints for the gateway daemon.

Every endpoint is mounted on the *control_app* under ``/api/*``.
"""

import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from llmport.models.provider import ProviderConfig
from llmport.models.model import merge_aliases_into_logical_models
from llmport.gateway.state import get_state
from llmport.gateway import openai_handler, anthropic_handler

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def control_status(request: Request) -> JSONResponse:
    """Return current daemon status including stats and provider health."""
    state = get_state()
    return JSONResponse({
        "active_model": state.active_model_id,
        "uptime": time.time() - state.started_at,
        "request_count": state.request_count,
        "total_tokens": state.total_tokens,
        "provider_count": len(state.providers),
        "model_count": len(state.models),
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


async def control_switch_model(request: Request) -> JSONResponse:
    """Switch the active model."""
    state = get_state()
    body = await request.json()
    model_id = body.get("model_id")
    state.active_model_id = model_id
    state.save()
    return JSONResponse({"ok": True, "active_model": model_id})


async def control_models(request: Request) -> JSONResponse:
    """Return the list of logical models with their bindings."""
    state = get_state()
    return JSONResponse({
        "models": [
            {
                "id": m.id,
                "provider_count": len(m.bindings),
                "routing_strategy": m.routing_strategy,
                "bindings": [
                    {
                        "provider_id": b.provider_id,
                        "model_name": b.model_name,
                        "priority": b.priority,
                    }
                    for b in m.bindings_sorted
                ],
            }
            for m in state.models
        ],
        "active_model": state.active_model_id,
    })


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def control_providers(request: Request) -> JSONResponse:
    """CRUD for provider configurations."""
    state = get_state()
    if request.method == "GET":
        return JSONResponse([
            p.to_dict(include_key=False) for p in state.providers
        ])
    elif request.method == "POST":
        body = await request.json()
        # Issue #6: Protect existing API key when the UI sends "***"
        if body.get("api_key") == "***":
            existing = {p.id: p for p in state.providers}
            if body["id"] in existing:
                body["api_key"] = existing[body["id"]].api_key
        provider = ProviderConfig.from_dict(body)
        existing = [p for p in state.providers if p.id != provider.id]
        existing.append(provider)
        state.providers = existing
        state.models = merge_aliases_into_logical_models(
            state.providers,
            [{"id": m.id, "bindings": [
                {"provider_id": b.provider_id,
                 "model_name": b.model_name,
                 "priority": b.priority}
                for b in m.bindings
            ]} for m in state.models],
        )
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
        state.models = merge_aliases_into_logical_models(
            state.providers,
            [{"id": m.id, "bindings": [
                {"provider_id": b.provider_id,
                 "model_name": b.model_name,
                 "priority": b.priority}
                for b in m.bindings
            ]} for m in state.models],
        )
        state.save()
        return JSONResponse({"ok": True})


async def control_test_provider(request: Request) -> JSONResponse:
    """Test a provider connection."""
    body = await request.json()
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

        # Issue #9: Reject non-loopback addresses
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


async def control_daemon_stop(request: Request) -> JSONResponse:
    """Initiate graceful shutdown."""
    import os
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return JSONResponse({"ok": True})


async def control_daemon_restart(request: Request) -> JSONResponse:
    """Signal the launcher to restart the gateway."""
    return JSONResponse({"ok": True, "action": "restart"})
