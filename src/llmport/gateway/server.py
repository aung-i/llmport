"""Gateway HTTP server with OpenAI and Anthropic protocol endpoints."""

import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse, Response
from starlette.routing import Route

from llmport.config.store import ConfigStore
from llmport.models.provider import ProviderConfig, ProviderHealth
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


ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


async def control_gateway_config(request: Request) -> JSONResponse:
    """Get or update gateway configuration."""
    state = _get_state()
    if request.method == "GET":
        return JSONResponse(state.gateway)
    elif request.method == "POST":
        body = await request.json()
        host = body.get("host", "127.0.0.1").strip()
        port = int(body.get("port", 11434))

        # Reject dangerous bind addresses — the gateway should only listen locally
        if host not in ALLOWED_HOSTS:
            return JSONResponse(
                {"ok": False, "error": f"不允许的主机地址: {host}。仅允许: {', '.join(sorted(ALLOWED_HOSTS))}"},
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


async def control_daemon_stop(request: Request) -> JSONResponse:
    """Initiate graceful shutdown."""
    import os
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return JSONResponse({"ok": True})


async def control_daemon_restart(request: Request) -> JSONResponse:
    """Signal the launcher to restart the gateway."""
    return JSONResponse({"ok": True, "action": "restart"})


def create_app(store: ConfigStore) -> Starlette:
    """Create the full gateway application (endpoints + control API)."""
    global STATE
    STATE = GatewayState(store)

    routes = [
        # OpenAI protocol
        Route("/openai/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/openai/v1/models", openai_models, methods=["GET"]),
        Route("/openai/v1/{path:path}", openai_catchall, methods=["POST", "GET"]),
        # Anthropic protocol
        Route("/anthropic/v1/messages", anthropic_messages, methods=["POST"]),
        # Control API
        Route("/api/status", control_status, methods=["GET"]),
        Route("/api/models/switch", control_switch_model, methods=["POST"]),
        Route("/api/providers", control_providers, methods=["GET", "POST", "DELETE"]),
        Route("/api/providers/test", control_test_provider, methods=["POST"]),
        Route("/api/providers/models", control_fetch_models, methods=["POST"]),
        Route("/api/gateway/config", control_gateway_config, methods=["GET", "POST"]),
        Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        Route("/api/daemon/restart", control_daemon_restart, methods=["POST"]),
    ]

    return Starlette(routes=routes)


def run_daemon(store: ConfigStore) -> None:
    """Start the gateway daemon on the configured host:port (default 127.0.0.1:11434)
    and the control port from LLMGATE_CONTROL_PORT.

    All protocols (OpenAI, Anthropic) share the same port; they are
    differentiated by URL path (/v1/chat/completions vs /v1/messages).
    """
    import os
    import threading
    import uvicorn

    app = create_app(store)
    control_port = int(os.environ.get("LLMGATE_CONTROL_PORT", "0"))

    gw = _migrate_gateway_config(store.load())
    host = gw["host"]
    gateway_port = gw["port"]

    ports = [gateway_port]
    if control_port:
        ports.append(control_port)

    def _serve(port: int) -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
        )
        uvicorn.Server(config).run()

    # Start gateway port in a daemon thread
    threads = []
    for port in ports:
        if port == control_port:
            continue  # control port runs in main thread
        t = threading.Thread(target=_serve, args=(port,), daemon=True)
        t.start()
        threads.append(t)

    # Control port runs in the main thread (handles signals for graceful stop)
    if control_port:
        _serve(control_port)
    elif threads:
        # If no control port, block on the gateway thread
        threads[0].join()
