"""Gateway HTTP server with OpenAI and Anthropic protocol endpoints."""

import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route

from llmgate.config.store import ConfigStore
from llmgate.models.provider import ProviderConfig, ProviderHealth
from llmgate.models.model import merge_aliases_into_logical_models
from llmgate.gateway.router import Router, RouterError
from llmgate.gateway import openai_handler, anthropic_handler


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
            "gateway": {"openai_port": 11434, "anthropic_port": 11435},
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
            primary = openai_handler.stream(body, provider, model_name)
            try:
                first_chunk = await primary.__anext__()
            except StopAsyncIteration:
                return

            if b"[ERROR]" in first_chunk:
                await primary.aclose()
                fallback = router.try_fallback(provider.id)
                if fallback:
                    fb_provider, fb_model = fallback
                    if fb_provider.protocol == "openai":
                        async for fb_chunk in openai_handler.stream(
                            body, fb_provider, fb_model
                        ):
                            yield fb_chunk
                        return
                yield first_chunk
                return

            yield first_chunk
            async for chunk in primary:
                yield chunk

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        result, error = await openai_handler.forward(body, provider, model_name)
        if error:
            fallback = router.try_fallback(provider.id)
            if fallback:
                fb_provider, fb_model = fallback
                if fb_provider.protocol == "openai":
                    result, error = await openai_handler.forward(body, fb_provider, fb_model)
        if error:
            return JSONResponse({"error": error}, status_code=502)
        state.request_count += 1
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
    """Forward any other OpenAI endpoint transparently."""
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
            primary = anthropic_handler.stream(body, provider, model_name)
            try:
                first_chunk = await primary.__anext__()
            except StopAsyncIteration:
                return

            if b"[ERROR]" in first_chunk:
                await primary.aclose()
                fallback = router.try_fallback(provider.id)
                if fallback:
                    fb_provider, fb_model = fallback
                    if fb_provider.protocol == "anthropic":
                        async for fb_chunk in anthropic_handler.stream(
                            body, fb_provider, fb_model
                        ):
                            yield fb_chunk
                        return
                yield first_chunk
                return

            yield first_chunk
            async for chunk in primary:
                yield chunk

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        result, error = await anthropic_handler.forward(body, provider, model_name)
        if error:
            fallback = router.try_fallback(provider.id)
            if fallback:
                fb_provider, fb_model = fallback
                if fb_provider.protocol == "anthropic":
                    result, error = await anthropic_handler.forward(
                        body, fb_provider, fb_model
                    )
        if error:
            return JSONResponse({"error": error}, status_code=502)
        state.request_count += 1
        return JSONResponse(result)


# --- Control API ---

async def control_status(request: Request) -> JSONResponse:
    state = _get_state()
    return JSONResponse({
        "active_model": state.active_model_id,
        "uptime": time.time() - state.started_at,
        "request_count": state.request_count,
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
        return JSONResponse([p.to_dict() for p in state.providers])
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


async def control_daemon_stop(request: Request) -> JSONResponse:
    """Initiate graceful shutdown."""
    import os
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return JSONResponse({"ok": True})


def create_app(store: ConfigStore) -> Starlette:
    """Create the full gateway application (endpoints + control API)."""
    global STATE
    STATE = GatewayState(store)

    routes = [
        # OpenAI protocol
        Route("/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/v1/models", openai_models, methods=["GET"]),
        Route("/v1/{path:path}", openai_catchall, methods=["POST", "GET"]),
        # Anthropic protocol
        Route("/v1/messages", anthropic_messages, methods=["POST"]),
        # Control API
        Route("/api/status", control_status, methods=["GET"]),
        Route("/api/models/switch", control_switch_model, methods=["POST"]),
        Route("/api/providers", control_providers, methods=["GET", "POST"]),
        Route("/api/providers/test", control_test_provider, methods=["POST"]),
        Route("/api/providers/models", control_fetch_models, methods=["POST"]),
        Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
    ]

    return Starlette(routes=routes)


def run_daemon(store: ConfigStore) -> None:
    """Start the gateway daemon bound to the control port, OpenAI port (11434),
    and Anthropic port (11435), all serving the same app.

    The control API is reachable on all three ports.  The control port from
    *LLMGATE_CONTROL_PORT* is the port the TUI uses for management requests.
    """
    import os
    import threading
    import uvicorn

    app = create_app(store)
    control_port = int(os.environ.get("LLMGATE_CONTROL_PORT", "0"))

    ports = [11434, 11435]
    if control_port:
        ports.append(control_port)

    def _serve(port: int) -> None:
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        uvicorn.Server(config).run()

    # Start protocol ports in daemon threads
    threads = []
    for port in [11434, 11435]:
        t = threading.Thread(target=_serve, args=(port,), daemon=True)
        t.start()
        threads.append(t)

    # Control port runs in the main thread (handles signals for graceful stop)
    if control_port:
        _serve(control_port)
    else:
        # If no control port, block on one of the protocol ports
        threads[0].join()
