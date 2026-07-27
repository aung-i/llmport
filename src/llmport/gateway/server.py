"""Gateway HTTP server — route handlers and application factory.

This module owns the protocol-specific route handlers (``openai_chat``,
``anthropic_messages``, etc.) and the ``create_app()`` factory.

State management lives in :mod:`llmport.gateway.state` and control-API
endpoints live in :mod:`llmport.gateway.control_api`.
"""

import json as _json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route

from llmport.config.store import ConfigStore
from llmport.models.provider import ProviderConfig
from llmport.gateway.router import Router, RouterError
from llmport.gateway.state import GatewayState, init_state, get_state
from llmport.gateway import openai_handler, anthropic_handler
from llmport.gateway.control_api import (
    control_status,
    control_models,
    control_models_delete,
    control_providers,
    control_test_provider,
    control_fetch_models,
    control_gateway_config,
    control_daemon_stop,
    control_daemon_restart,
)

# ============================================================================
# Token-tracking helpers
# ============================================================================


def _safe_token_val(val) -> int:
    """Return max(0, int(val)) handling non-int/negative values gracefully."""
    if isinstance(val, int) and val >= 0:
        return val
    if isinstance(val, (float, str)):
        try:
            return max(0, int(float(val)))
        except (ValueError, TypeError):
            pass
    return 0


def _extract_tokens(result: dict) -> int:
    """Extract total token count from a non-streaming response dict.

    Supports both OpenAI (``usage.total_tokens``) and Anthropic
    (``usage.input_tokens + usage.output_tokens``) formats.
    Returns 0 when no usage is present.
    """
    usage = result.get("usage") or {}
    # OpenAI format
    val = usage.get("total_tokens")
    if val is not None:
        return _safe_token_val(val)
    # Anthropic format
    total = 0
    for key in ("input_tokens", "output_tokens"):
        val = usage.get(key)
        if val is not None:
            total += _safe_token_val(val)
    return total


def _parse_usage_from_sse(chunk: bytes) -> int | None:
    """Parse usage from a single SSE chunk (OpenAI or Anthropic format).

    Returns total_tokens or None if the chunk contains no usage info.
    """
    if not chunk:
        return None
    try:
        text = chunk.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload in ("[DONE]",):
                continue
            obj = _json.loads(payload)
            usage = obj.get("usage")
            if usage is None:
                # Anthropic message_start nests usage under "message"
                msg = obj.get("message")
                if isinstance(msg, dict):
                    usage = msg.get("usage")
            if not usage:
                continue
            # OpenAI: usage.total_tokens
            total = _safe_token_val(usage.get("total_tokens"))
            if total:
                return total
            # Anthropic: usage.input_tokens + usage.output_tokens
            inp = _safe_token_val(usage.get("input_tokens"))
            out = _safe_token_val(usage.get("output_tokens"))
            if inp or out:
                return inp + out
    except Exception:
        pass
    return None


async def _tracked_stream(generator, state):
    """Wrap an async SSE generator to track usage and request count.

    Passes through all chunks and, after the stream completes, increments
    *request_count* and accumulates *total_tokens* from any SSE usage fields.
    """
    try:
        async for chunk in generator:
            yield chunk
            usage = _parse_usage_from_sse(chunk)
            if usage is not None:
                state.total_tokens += usage
    finally:
        state.request_count += 1


# ============================================================================
# OpenAI protocol endpoints
# ============================================================================


async def openai_chat(request: Request) -> Response:
    """POST /openai/v1/chat/completions (and SDK alias ``/v1/chat/completions``)."""
    state = get_state()
    router = state.get_router()
    body = await request.json()
    requested = body.get("model")
    try:
        provider, model_name = router.resolve(requested)
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol != "openai":
        return JSONResponse(
            {"error": f"Model {requested!r} is on an Anthropic provider, not OpenAI"},
            status_code=400,
        )

    is_stream = body.get("stream", False)

    if is_stream:
        async def generate():
            last_id = provider.id
            cur_provider = provider
            cur_model = model_name

            while True:
                gen = openai_handler.stream(body, cur_provider, cur_model)
                try:
                    first_chunk = await gen.__anext__()
                except StopAsyncIteration:
                    return

                if b"[ERROR]" not in first_chunk:
                    yield first_chunk
                    async for chunk in gen:
                        yield chunk
                    return

                await gen.aclose()

                # Find next fallback with matching protocol
                while True:
                    fb = router.try_fallback(requested, last_id)
                    if not fb:
                        yield first_chunk  # all exhausted
                        return
                    fb_provider, fb_model = fb
                    last_id = fb_provider.id
                    if fb_provider.protocol == "openai":
                        cur_provider = fb_provider
                        cur_model = fb_model
                        break
                # Continue outer loop with new provider

        return StreamingResponse(
            _tracked_stream(generate(), state),
            media_type="text/event-stream",
        )
    else:
        result, error = await openai_handler.forward(body, provider, model_name)
        if error:
            last_id = provider.id
            while True:
                fb = router.try_fallback(requested, last_id)
                if not fb:
                    break
                fb_provider, fb_model = fb
                if fb_provider.protocol != "openai":
                    last_id = fb_provider.id
                    continue
                result, error = await openai_handler.forward(
                    body, fb_provider, fb_model
                )
                if not error:
                    break
                last_id = fb_provider.id

        state.request_count += 1
        if error:
            return JSONResponse({"error": error}, status_code=502)
        state.total_tokens += _extract_tokens(result)
        return JSONResponse(result)


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

    Note: this is a generic passthrough for arbitrary endpoints
    (e.g. ``/v1/embeddings``, ``/v1/audio/transcriptions``).  It
    intentionally has **no fallback logic** and **no stats tracking**
    because the upstream protocol/response format is unknown at this
    level.  Protocol-specific endpoints (``/v1/chat/completions``)
    with full fallback + stats are handled by ``openai_chat``.
    """
    state = get_state()
    router = state.get_router()

    # Catchall forwards arbitrary OpenAI endpoints (e.g. /v1/embeddings).
    # The model must be present in the JSON body so we can route it.
    try:
        body = await request.json()
    except Exception:
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

    path = request.url.path

    async def generate():
        async for chunk in openai_handler.stream(
            body, provider, model_name, path=path
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


# ============================================================================
# Anthropic protocol endpoints
# ============================================================================


async def anthropic_messages(request: Request) -> Response:
    """POST /anthropic/v1/messages (and SDK alias ``/v1/messages``)."""
    state = get_state()
    router = state.get_router()
    body = await request.json()
    requested = body.get("model")
    try:
        provider, model_name = router.resolve(requested)
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol != "anthropic":
        return JSONResponse(
            {"error": f"Model {requested!r} is on an OpenAI provider, not Anthropic"},
            status_code=400,
        )

    is_stream = body.get("stream", False)

    if is_stream:
        async def generate():
            last_id = provider.id
            cur_provider = provider
            cur_model = model_name

            while True:
                gen = anthropic_handler.stream(body, cur_provider, cur_model)
                try:
                    first_chunk = await gen.__anext__()
                except StopAsyncIteration:
                    return

                if b"[ERROR]" not in first_chunk:
                    yield first_chunk
                    async for chunk in gen:
                        yield chunk
                    return

                await gen.aclose()

                # Find next fallback with matching protocol
                while True:
                    fb = router.try_fallback(requested, last_id)
                    if not fb:
                        yield first_chunk  # all exhausted
                        return
                    fb_provider, fb_model = fb
                    last_id = fb_provider.id
                    if fb_provider.protocol == "anthropic":
                        cur_provider = fb_provider
                        cur_model = fb_model
                        break
                # Continue outer loop with new provider

        return StreamingResponse(
            _tracked_stream(generate(), state),
            media_type="text/event-stream",
        )
    else:
        result, error = await anthropic_handler.forward(body, provider, model_name)
        if error:
            last_id = provider.id
            while True:
                fb = router.try_fallback(requested, last_id)
                if not fb:
                    break
                fb_provider, fb_model = fb
                if fb_provider.protocol != "anthropic":
                    last_id = fb_provider.id
                    continue
                result, error = await anthropic_handler.forward(
                    body, fb_provider, fb_model
                )
                if not error:
                    break
                last_id = fb_provider.id

        state.request_count += 1
        if error:
            return JSONResponse({"error": error}, status_code=502)
        state.total_tokens += _extract_tokens(result)
        return JSONResponse(result)


# ============================================================================
# Application factory
# ============================================================================


def create_app(store: ConfigStore) -> Starlette:
    """Create the gateway application.

    A single Starlette app serves both the protocol-forwarding routes
    (``/openai/v1/*``, ``/anthropic/v1/*``, ``/v1/*``) and the control API
    (``/api/*``) on one port.
    """
    init_state(store)

    routes = [
        # OpenAI protocol (explicit prefix + SDK short path)
        Route("/openai/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/openai/v1/models", openai_models, methods=["GET"]),
        Route("/openai/v1/{path:path}", openai_catchall, methods=["POST", "GET"]),
        # Anthropic protocol (explicit prefix + SDK short path)
        Route("/anthropic/v1/messages", anthropic_messages, methods=["POST"]),
        # SDK-compatible alias paths
        Route("/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/v1/messages", anthropic_messages, methods=["POST"]),
        # Control API
        Route("/api/status", control_status, methods=["GET"]),
        Route("/api/models", control_models, methods=["GET"]),
        Route("/api/models", control_models_delete, methods=["DELETE"]),
        Route("/api/providers", control_providers, methods=["GET", "POST", "DELETE"]),
        Route("/api/providers/test", control_test_provider, methods=["POST"]),
        Route("/api/providers/models", control_fetch_models, methods=["POST"]),
        Route("/api/gateway/config", control_gateway_config, methods=["GET", "POST"]),
        Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        Route("/api/daemon/restart", control_daemon_restart, methods=["POST"]),
    ]

    return Starlette(routes=routes)
