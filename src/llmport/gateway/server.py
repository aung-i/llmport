"""Gateway HTTP server with OpenAI and Anthropic protocol endpoints."""

import json as _json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route

from llmport.config.store import ConfigStore
from llmport.models.provider import ProviderConfig
from llmport.models.model import merge_aliases_into_logical_models
from llmport.gateway.router import Router, RouterError
from llmport.gateway import openai_handler, anthropic_handler


def _migrate_gateway_config(data: dict) -> dict:
    """Migrate old-format gateway config (openai_port/anthropic_port) to new format (host/port).

    Modifies *data* in place when migration is needed so callers can persist
    the result.  Returns the canonical ``{"host": str, "port": int}`` dict.
    """
    gw = data.get("gateway", {})
    if "host" not in gw:
        gw = {"host": "127.0.0.1", "port": gw.get("openai_port", 11434)}
        data["gateway"] = gw
    return {"host": gw["host"], "port": gw.get("port", 11434)}


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


class GatewayState:
    """Mutable state shared between the server and control API."""

    def __init__(self, store: ConfigStore):
        self.store = store
        self.providers: list[ProviderConfig] = []
        self.models = []
        self.active_model_id: str | None = None
        self.started_at = time.time()
        self.request_count = 0
        self.total_tokens = 0
        self.reload()

    def reload(self) -> None:
        """Reload config from disk."""
        data = self.store.load()
        had_host = "host" in data.get("gateway", {})
        self.gateway = _migrate_gateway_config(data)
        if not had_host:
            self.store.save(data)
        self.providers = [
            ProviderConfig.from_dict(p) for p in data.get("providers", [])
        ]
        self.models = merge_aliases_into_logical_models(
            self.providers, data.get("models", []),
        )
        self.active_model_id = data.get("active_model")

    def save(self) -> None:
        """Persist current state to disk."""
        data = {
            "version": 1,
            "gateway": self.gateway,
            "providers": [p.to_dict() for p in self.providers],
            "models": [
                {
                    "id": m.id,
                    "bindings": [
                        {
                            "provider_id": b.provider_id,
                            "model_name": b.model_name,
                            "priority": b.priority,
                        }
                        for b in m.bindings
                    ],
                    "routing_strategy": m.routing_strategy,
                }
                for m in self.models
            ],
            "active_model": self.active_model_id,
        }
        self.store.save(data)

    def get_router(self) -> Router:
        return Router(self.providers, self.models, self.active_model_id)


STATE: GatewayState | None = None


def _get_state() -> GatewayState:
    assert STATE is not None
    return STATE


# --- OpenAI endpoints ---

async def openai_chat(request: Request) -> Response:
    state = _get_state()
    router = state.get_router()
    try:
        provider, model_name = router.resolve()
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol != "openai":
        return JSONResponse(
            {"error": f"Model '{state.active_model_id}' is on an Anthropic provider, not OpenAI"},
            status_code=400,
        )

    body = await request.json()
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
                    fb = router.try_fallback(last_id)
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
                fb = router.try_fallback(last_id)
                if not fb:
                    break
                fb_provider, fb_model = fb
                if fb_provider.protocol != "openai":
                    last_id = fb_provider.id
                    continue
                result, error = await openai_handler.forward(body, fb_provider, fb_model)
                if not error:
                    break
                last_id = fb_provider.id

        state.request_count += 1
        if error:
            return JSONResponse({"error": error}, status_code=502)
        state.total_tokens += _extract_tokens(result)
        return JSONResponse(result)


async def openai_models(request: Request) -> Response:
    """Return a filtered list of models available on the active route."""
    state = _get_state()
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": m.id, "object": "model"}
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
    state = _get_state()
    router = state.get_router()
    try:
        provider, model_name = router.resolve()
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol != "openai":
        return JSONResponse(
            {"error": "Active model is not on an OpenAI provider"},
            status_code=400,
        )

    body = await request.body()
    path = request.url.path

    async def generate():
        async for chunk in openai_handler.stream(
            body, provider, model_name, path=path
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Anthropic endpoints ---

async def anthropic_messages(request: Request) -> Response:
    state = _get_state()
    router = state.get_router()
    try:
        provider, model_name = router.resolve()
    except RouterError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if provider.protocol != "anthropic":
        return JSONResponse(
            {"error": f"Model '{state.active_model_id}' is on an OpenAI provider, not Anthropic"},
            status_code=400,
        )

    body = await request.json()
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
                    fb = router.try_fallback(last_id)
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
                fb = router.try_fallback(last_id)
                if not fb:
                    break
                fb_provider, fb_model = fb
                if fb_provider.protocol != "anthropic":
                    last_id = fb_provider.id
                    continue
                result, error = await anthropic_handler.forward(body, fb_provider, fb_model)
                if not error:
                    break
                last_id = fb_provider.id

        state.request_count += 1
        if error:
            return JSONResponse({"error": error}, status_code=502)
        state.total_tokens += _extract_tokens(result)
        return JSONResponse(result)


# --- Control API ---

async def control_status(request: Request) -> JSONResponse:
    state = _get_state()
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


async def control_switch_model(request: Request) -> JSONResponse:
    state = _get_state()
    body = await request.json()
    model_id = body.get("model_id")
    state.active_model_id = model_id
    state.save()
    return JSONResponse({"ok": True, "active_model": model_id})


async def control_providers(request: Request) -> JSONResponse:
    state = _get_state()
    if request.method == "GET":
        return JSONResponse([p.to_dict(include_key=False) for p in state.providers])
    elif request.method == "POST":
        body = await request.json()
        provider = ProviderConfig.from_dict(body)
        existing = [p for p in state.providers if p.id != provider.id]
        existing.append(provider)
        state.providers = existing
        state.models = merge_aliases_into_logical_models(
            state.providers,
            [{"id": m.id, "bindings": [
                {"provider_id": b.provider_id, "model_name": b.model_name, "priority": b.priority}
                for b in m.bindings
            ]} for m in state.models],
        )
        state.save()
        return JSONResponse({"ok": True})
    elif request.method == "DELETE":
        body = await request.json()
        provider_id = body.get("id")
        if not provider_id:
            return JSONResponse({"ok": False, "error": "Missing provider id"}, status_code=400)
        state.providers = [p for p in state.providers if p.id != provider_id]
        state.models = merge_aliases_into_logical_models(
            state.providers,
            [{"id": m.id, "bindings": [
                {"provider_id": b.provider_id, "model_name": b.model_name, "priority": b.priority}
                for b in m.bindings
            ]} for m in state.models],
        )
        state.save()
        return JSONResponse({"ok": True})


async def control_test_provider(request: Request) -> JSONResponse:
    """Test a provider connection.

    For OpenAI providers, uses list_models as a connectivity check.
    For Anthropic providers, uses test_connection.
    """
    state = _get_state()
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
    """Fetch model list from a provider. Accepts a full provider body so it
    works before the provider is saved."""
    body = await request.json()
    provider = ProviderConfig.from_dict(body)
    if provider.protocol == "openai":
        models, error = await openai_handler.list_models(provider)
    else:
        models, error = None, "Anthropic does not expose a model list API"
    return JSONResponse({"models": models, "error": error})


async def control_gateway_config(request: Request) -> JSONResponse:
    """Get or update gateway configuration."""
    state = _get_state()
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

        if not (1024 <= port <= 65535):
            return JSONResponse(
                {"ok": False, "error": f"端口号超出范围: {port} (1024-65535)"},
                status_code=400,
            )

        state.gateway = {"host": host, "port": port}
        state.save()

        # Warn if binding to a non-loopback address (gateway will be network-accessible)
        warning = None
        if host not in {"127.0.0.1", "localhost", "::1"}:
            warning = f"网关绑定到 {host}，局域网内其他设备可访问你的 API key"

        return JSONResponse({"ok": True, "gateway": state.gateway, "warning": warning})


async def control_daemon_stop(request: Request) -> JSONResponse:
    """Initiate graceful shutdown."""
    import os
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return JSONResponse({"ok": True})


async def control_daemon_restart(request: Request) -> JSONResponse:
    """Signal the launcher to restart the gateway."""
    return JSONResponse({"ok": True, "action": "restart"})


def create_app(store: ConfigStore) -> tuple[Starlette, Starlette]:
    """Create the gateway and control applications.

    Returns a ``(gateway_app, control_app)`` tuple:

    - **gateway_app** — OpenAI and Anthropic protocol endpoints only.
    - **control_app** — Control API endpoints (``/api/*``) only.
    """
    global STATE
    STATE = GatewayState(store)

    gateway_routes = [
        # OpenAI protocol
        Route("/openai/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/openai/v1/models", openai_models, methods=["GET"]),
        Route("/openai/v1/{path:path}", openai_catchall, methods=["POST", "GET"]),
        # Anthropic protocol
        Route("/anthropic/v1/messages", anthropic_messages, methods=["POST"]),
    ]

    control_routes = [
        Route("/api/status", control_status, methods=["GET"]),
        Route("/api/models/switch", control_switch_model, methods=["POST"]),
        Route("/api/providers", control_providers, methods=["GET", "POST", "DELETE"]),
        Route("/api/providers/test", control_test_provider, methods=["POST"]),
        Route("/api/providers/models", control_fetch_models, methods=["POST"]),
        Route("/api/gateway/config", control_gateway_config, methods=["GET", "POST"]),
        Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        Route("/api/daemon/restart", control_daemon_restart, methods=["POST"]),
    ]

    return Starlette(routes=gateway_routes), Starlette(routes=control_routes)


def run_daemon(store: ConfigStore) -> None:
    """Start the gateway daemon on the configured host:port (default 127.0.0.1:11434)
    and the control port from LLMGATE_CONTROL_PORT.

    Gateway and control APIs run as separate Starlette applications on separate
    ports.  The gateway app serves OpenAI/Anthropic protocol endpoints; the
    control app serves ``/api/*`` endpoints.
    """
    import os
    import threading
    import uvicorn

    gateway_app, control_app = create_app(store)
    control_port = int(os.environ.get("LLMGATE_CONTROL_PORT", "0"))

    gw = _migrate_gateway_config(store.load())
    host = gw["host"]
    gateway_port = gw["port"]

    def _serve(app, port: int) -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
        )
        uvicorn.Server(config).run()

    # Start gateway on gateway port in a daemon thread
    t = threading.Thread(
        target=_serve, args=(gateway_app, gateway_port), daemon=True
    )
    t.start()

    # Control port runs in the main thread (handles signals for graceful stop)
    if control_port:
        _serve(control_app, control_port)
    else:
        t.join()
