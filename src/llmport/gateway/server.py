"""Gateway HTTP server - route handlers and application factory.

This module owns the protocol-specific route handlers (``openai_chat``,
``anthropic_messages``, etc.) and the ``create_app()`` factory.

State management lives in :mod:`llmport.gateway.state`; the read-only
health endpoint lives in :mod:`llmport.gateway.health`. Lifecycle control
(stop / restart) is via process signals, not HTTP.

Error handling is **transparent**: the real upstream status code + body are
passed through to the client. There is no in-request fallback -- if a
provider fails, the client sees the real error, and the provider is marked
down for a cooldown so the *next* request routes to the next binding. Only
when the upstream never responds (timeout / unreachable) does the gateway
synthesize a 504.
"""

import hmac
import json

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route

from llmport.config.store import ConfigStore
from llmport.gateway.router import Router, RouterError
from llmport.gateway.state import init_state, get_state
from llmport.gateway import openai_handler, anthropic_handler, translator
from llmport.gateway.health import health
from llmport.gateway.handler_base import UpstreamResult, OpenedStream

# How long (seconds) a provider stays down after a runtime failure before the
# router retries it.
COOLDOWN_SECONDS = 30.0


# ============================================================================
# Client auth: llmport's own API key
# ============================================================================


def _extract_api_key(scope: dict) -> str | None:
    """Pull the client-presented API key from the ASGI request scope.

    Accepts ``Authorization: Bearer <key>`` (OpenAI SDK style) or
    ``x-api-key: <key>`` (Anthropic SDK style). Returns the raw key string or
    None. Headers are read straight from the scope (no Request object) so the
    request body is not consumed -- important for streaming passthrough.
    """
    auth = ""
    xkey = ""
    for name, value in scope.get("headers", ()):
        lname = name.decode("latin-1").lower()
        if lname == "authorization":
            auth = value.decode("latin-1")
        elif lname == "x-api-key":
            xkey = value.decode("latin-1")
    if auth.lower().startswith("bearer "):
        candidate = auth[7:].strip()
        if candidate:
            return candidate
    if xkey:
        return xkey.strip()
    return None


class APIKeyAuthMiddleware:
    """Pure-ASGI middleware enforcing llmport's API key on every request.

    Auth is mandatory, never optional. Every forwarding route requires the
    client to present llmport's API key via ``Authorization: Bearer <key>``
    (OpenAI SDK style) or ``x-api-key: <key>`` (Anthropic SDK style);
    ``/health`` is always exempt (a liveness probe must work unauthenticated).
    A missing or mismatched key yields 401. Comparison is constant-time
    (:func:`hmac.compare_digest`) to avoid a timing side channel.

    If no key is configured at all (``state.api_key`` empty -- a
    misconfiguration, since ``llmport setup`` generates one and ``start``
    refuses without one), the middleware fails *closed*: every non-/health
    route returns 503 rather than serving unauthenticated. This is a
    defense-in-depth backstop for direct ASGI invocation; the normal start
    path can't reach it.

    Implemented as plain ASGI (not ``BaseHTTPMiddleware``) so streaming SSE
    responses pass through untouched -- the middleware never buffers the body.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        state = get_state()
        expected = getattr(state, "api_key", "") or ""
        if not expected:
            # No key configured = misconfiguration. Fail closed (503), never
            # serve unauthenticated. Unreachable via `llmport start` (which
            # refuses without a key); guards direct ASGI invocation.
            response = JSONResponse(
                {"error": "llmport api_key not configured -- run `llmport setup`"},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        provided = _extract_api_key(scope)
        if not provided or not hmac.compare_digest(provided, expected):
            response = JSONResponse(
                {"error": "Unauthorized: missing or invalid API key"},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _read_json_body(request: Request) -> dict | None:
    """Read and return the request JSON body, or None if absent/invalid.

    Returns None for unparseable bodies AND for valid JSON that is not a dict
    (e.g. ``[1,2,3]`` or ``"foo"``), so callers return a 400 instead of an
    unhandled 500 from ``body.get("model")`` (matching ``openai_catchall``).
    """
    try:
        body = await request.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body


def _is_availability_failure(status: int) -> bool:
    """True for upstream statuses that mean the provider is unhealthy right now.

    5xx (server error) and 429 (rate limited) are availability problems --
    another provider might serve the same request, so the failing one is
    marked down. Other 4xx (bad request, auth, not found) are not: switching
    providers won't help, and the client should see the real error.
    """
    return status >= 500 or status == 429


def _no_response_response(provider, reason: str) -> Response:
    """504 when the upstream never responded (timeout / unreachable)."""
    provider.health.mark_down(COOLDOWN_SECONDS)
    return JSONResponse(
        {"error": f"provider {provider.name} {reason}"},
        status_code=504,
    )


def _passthrough(result: UpstreamResult, provider) -> Response:
    """Return the real upstream response to the client; mark down on availability failures."""
    if result.status is None:
        return _no_response_response(provider, result.reason)
    if _is_availability_failure(result.status):
        provider.health.mark_down(COOLDOWN_SECONDS)
    return Response(
        content=result.body,
        status_code=result.status,
        media_type=result.content_type or "application/json",
    )


async def _stream_response(body, provider, model_name, handler, path) -> Response:
    """Open the upstream stream, peek its status, then decide.

    A 2xx is piped to the client as SSE (the 200 is committed only once we
    know the upstream succeeded). An error status is read in full and passed
    through verbatim -- never turned into a fake 200 + ``[ERROR]`` text. No
    response (timeout / unreachable) yields a 504.
    """
    opened = await handler.open_stream(body, provider, model_name, path)
    if isinstance(opened, str):  # "timeout" | "unreachable"
        return _no_response_response(provider, opened)

    status = opened.status
    if status >= 400:
        error_body = await opened.aread()
        content_type = opened.content_type
        await opened.aclose()
        if _is_availability_failure(status):
            provider.health.mark_down(COOLDOWN_SECONDS)
        return Response(
            content=error_body,
            status_code=status,
            media_type=content_type or "application/json",
        )

    async def _pipe():
        try:
            async for chunk in opened.aiter_bytes():
                yield chunk
        finally:
            await opened.aclose()

    return StreamingResponse(_pipe(), media_type="text/event-stream")


# ============================================================================
# Cross-format translation (OpenAI client <-> Anthropic provider, and reverse)
# ============================================================================
#
# When the client's protocol differs from the resolved provider's, the request
# is translated to the provider's format, forwarded, and the response
# translated back. The translator (llmport.gateway.translator) is pure
# functions; this layer just orchestrates and reuses the same handler_base
# forward/stream primitives. Same-protocol requests skip this entirely.


async def _forward_translated(
    body: dict, provider, model_name: str, *, client_format: str,
) -> Response:
    """Forward a cross-format chat request via the translator.

    ``client_format`` is the protocol the client spoke ("openai" or
    "anthropic"); the provider speaks the other. The request is translated to
    the provider's format, forwarded, and the response translated back --
    non-streaming here, streaming via :func:`_stream_translated`.
    """
    provider_format = provider.protocol
    if client_format == "openai" and provider_format == "anthropic":
        upstream_body = translator.openai_to_anthropic_request(body)
        handler = anthropic_handler
        path = "/v1/messages"
    elif client_format == "anthropic" and provider_format == "openai":
        upstream_body = translator.anthropic_to_openai_request(body)
        handler = openai_handler
        path = "/v1/chat/completions"
    else:
        return JSONResponse(
            {"error": "format translation not applicable"}, status_code=400,
        )

    requested_model = body.get("model")
    if body.get("stream", False):
        return await _stream_translated(
            upstream_body, provider, model_name, handler, path,
            client_format, requested_model,
        )
    result = await handler.forward(upstream_body, provider, model_name, path)
    return _passthrough_translated(result, provider, client_format, requested_model)


def _passthrough_translated(
    result, provider, client_format: str, requested_model: str,
) -> Response:
    """Translate a non-streaming upstream response into the client's format.

    Availability failures mark the provider down (same as direct passthrough).
    Upstream errors (>=400) are passed through verbatim in the upstream format
    -- error-body translation is out of scope (see issue #2).
    """
    if result.status is None:
        return _no_response_response(provider, result.reason)
    if _is_availability_failure(result.status):
        provider.health.mark_down(COOLDOWN_SECONDS)
    if result.status >= 400:
        return Response(
            content=result.body, status_code=result.status,
            media_type=result.content_type or "application/json",
        )
    try:
        upstream_json = json.loads(result.body)
    except (ValueError, TypeError):
        return Response(
            content=result.body, status_code=result.status,
            media_type=result.content_type or "application/json",
        )
    if client_format == "openai":
        translated = translator.anthropic_to_openai_response(
            upstream_json, requested_model)
    else:
        translated = translator.openai_to_anthropic_response(
            upstream_json, requested_model)
    return JSONResponse(translated, status_code=result.status)


async def _stream_translated(
    upstream_body, provider, model_name, handler, path,
    client_format: str, requested_model: str,
) -> Response:
    """Open a translated upstream stream and pipe it back converted.

    A 2xx is converted event-by-event to the client's SSE format; an error
    status is read in full and passed through verbatim (no error translation).
    No upstream response yields a 504, same as direct streaming.
    """
    opened = await handler.open_stream(upstream_body, provider, model_name, path)
    if isinstance(opened, str):  # "timeout" | "unreachable"
        return _no_response_response(provider, opened)

    status = opened.status
    if status >= 400:
        error_body = await opened.aread()
        content_type = opened.content_type
        await opened.aclose()
        if _is_availability_failure(status):
            provider.health.mark_down(COOLDOWN_SECONDS)
        return Response(
            content=error_body, status_code=status,
            media_type=content_type or "application/json",
        )

    if client_format == "openai":
        gen = translator.anthropic_stream_to_openai(opened, requested_model)
    else:
        gen = translator.openai_stream_to_anthropic(opened, requested_model)

    async def _pipe():
        try:
            async for chunk in gen:
                yield chunk
        finally:
            await opened.aclose()

    return StreamingResponse(_pipe(), media_type="text/event-stream")


# ============================================================================
# OpenAI protocol endpoints
# ============================================================================


async def openai_chat(request: Request) -> Response:
    """POST /openai/v1/chat/completions."""
    state = get_state()
    router = state.get_router()
    body = await _read_json_body(request)
    if body is None:
        return JSONResponse(
            {"error": "Request body must be JSON with a 'model' field"},
            status_code=400,
        )
    requested = body.get("model")
    try:
        provider, model_name = router.resolve(requested)
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol == "anthropic":
        # OpenAI client, Anthropic provider: translate.
        return await _forward_translated(
            body, provider, model_name, client_format="openai")
    if provider.protocol != "openai":
        return JSONResponse(
            {"error": f"Unsupported provider protocol {provider.protocol!r}"},
            status_code=400,
        )

    path = "/v1/chat/completions"
    if body.get("stream", False):
        return await _stream_response(body, provider, model_name, openai_handler, path)
    result = await openai_handler.forward(body, provider, model_name, path)
    return _passthrough(result, provider)


async def openai_models(request: Request) -> Response:
    """Return the list of model names the gateway can route to."""
    state = get_state()
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": m.name, "object": "model"}
            for m in state.models
        ],
    })


async def openai_catchall(request: Request) -> Response:
    """Forward any other OpenAI endpoint transparently.

    This is a generic passthrough for arbitrary endpoints
    (e.g. ``/v1/embeddings``, ``/v1/audio/transcriptions``). The ``/openai``
    prefix is stripped so the upstream sees ``/v1/<path>``. It has no
    in-request fallback (same transparency rule as the chat endpoint); a
    failure marks the provider down and the next request routes around it.
    """
    state = get_state()
    router = state.get_router()

    # Catchall forwards arbitrary OpenAI endpoints (e.g. /v1/embeddings).
    # The model must be present in the JSON body so we can route it.
    body = await _read_json_body(request)
    if body is None:
        return JSONResponse(
            {"error": "Request body must be JSON with a 'model' field"},
            status_code=400,
        )
    requested = body.get("model")
    try:
        provider, model_name = router.resolve(requested)
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol != "openai":
        return JSONResponse(
            {"error": f"Model {requested!r} is not on an OpenAI provider"},
            status_code=400,
        )

    # Strip the /openai prefix: client hits /openai/v1/embeddings, upstream
    # gets /v1/embeddings. The route's literal prefix is /openai/v1/, so the
    # captured path is the part after it (e.g. "embeddings").
    path = "/v1/" + request.path_params["path"]
    if body.get("stream", False):
        return await _stream_response(body, provider, model_name, openai_handler, path)
    result = await openai_handler.forward(body, provider, model_name, path)
    return _passthrough(result, provider)


# ============================================================================
# Anthropic protocol endpoints
# ============================================================================


async def anthropic_messages(request: Request) -> Response:
    """POST /anthropic/v1/messages."""
    state = get_state()
    router = state.get_router()
    body = await _read_json_body(request)
    if body is None:
        return JSONResponse(
            {"error": "Request body must be JSON with a 'model' field"},
            status_code=400,
        )
    requested = body.get("model")
    try:
        provider, model_name = router.resolve(requested)
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol == "openai":
        # Anthropic client, OpenAI provider: translate.
        return await _forward_translated(
            body, provider, model_name, client_format="anthropic")
    if provider.protocol != "anthropic":
        return JSONResponse(
            {"error": f"Unsupported provider protocol {provider.protocol!r}"},
            status_code=400,
        )

    path = "/v1/messages"
    if body.get("stream", False):
        return await _stream_response(body, provider, model_name, anthropic_handler, path)
    result = await anthropic_handler.forward(body, provider, model_name, path)
    return _passthrough(result, provider)


# ============================================================================
# Application factory
# ============================================================================


def create_app(store: ConfigStore) -> Starlette:
    """Create the gateway application.

    A single Starlette app serves the protocol-forwarding routes
    (``/openai/v1/*``, ``/anthropic/v1/*``) and a read-only ``/health``
    liveness probe on one port. Lifecycle control is via process signals.
    """
    init_state(store)

    routes = [
        # OpenAI protocol
        Route("/openai/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/openai/v1/models", openai_models, methods=["GET"]),
        Route("/openai/v1/{path:path}", openai_catchall, methods=["POST", "GET"]),
        # Anthropic protocol
        Route("/anthropic/v1/messages", anthropic_messages, methods=["POST"]),
        # Read-only liveness probe. Lifecycle control (stop / restart) is via
        # process signals, not HTTP, so no control surface rides on the
        # forwarding port.
        Route("/health", health, methods=["GET"]),
    ]

    return Starlette(
        routes=routes,
        middleware=[Middleware(APIKeyAuthMiddleware)],
    )
