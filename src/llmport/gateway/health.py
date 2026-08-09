"""Health endpoint for the gateway.

``GET /health`` is a read-only liveness probe -- it returns 200
``{"status": "ok"}`` so the daemon (and anything else) can tell the gateway
is up. It is NOT control: it mutates nothing. Lifecycle control (stop /
restart) is handled via process signals, not HTTP, so no control surface
rides on the forwarding port.
"""

from starlette.requests import Request
from starlette.responses import JSONResponse


async def health(request: Request) -> JSONResponse:
    """GET /health - liveness probe."""
    return JSONResponse({"status": "ok"})
