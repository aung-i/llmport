"""Control API endpoints for the gateway daemon.

Only read-only status and lifecycle endpoints are mounted under ``/api/*``.
Configuration (providers / models / gateway host+port) is managed via the CLI,
which writes ``config.yaml`` (gateway + models) and ``providers.yaml`` (API
keys) and restarts the daemon; the write/test/fetch endpoints were removed to
close the programmatic SSRF entry (arbitrary ``base_url`` injection or fetch
at runtime). See ``llmport.config.validation`` for the base_url blocklist that
guards the CLI write path.
"""

import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from llmport.gateway.state import get_state

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
                "name": p.name,
                "status": p.health.status,
                "latency_ms": p.health.latency_ms,
            }
            for p in state.providers
        ],
    })


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
