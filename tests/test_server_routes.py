"""Tests for ``create_app()`` and route handlers in ``server.py``.

Uses the existing pattern from ``test_gateway.py``: creates a real
``ConfigStore`` with a temporary directory, registers a single provider
and a logical model, and exercises the app through
``starlette.testclient.TestClient``.

Routing is by the client-sent ``model`` field, so chat requests must
include ``"model": "gpt5"``.
"""

import tempfile
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway import server as gateway_server


_BASE_PROVIDERS = {
    "version": 1,
    "gateway": {"host": "127.0.0.1", "port": 11434},
    "providers": [
        {
            "id": "test-p",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-test",
        },
    ],
}

_BASE_MODELS = {"models": [
    {"name": "gpt5", "provider": "test-p", "upstream": "gpt-5"},
]}


def _make_app(tmp: str):
    """Create the gateway app with one OpenAI provider and a 'gpt5' model.

    ``create_app`` returns a single Starlette app serving both protocol
    and control routes.
    """
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_providers_config(_BASE_PROVIDERS)
    store.save_models_config(_BASE_MODELS)
    return gateway_server.create_app(store)


def _make_store(tmp: str):
    """Create an initialised ConfigStore with the base provider/model."""
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_providers_config(_BASE_PROVIDERS)
    store.save_models_config(_BASE_MODELS)
    return store


# ============================================================================
# create_app()
# ============================================================================

class TestCreateApp:
    """Verify the application factory registers all expected routes."""

    def test_returns_single_starlette_app(self):
        """create_app returns one Starlette app (not a tuple)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = gateway_server.create_app(_make_store(tmp))

            assert app is not None
            assert app.__class__.__name__ == "Starlette"

    def test_app_has_correct_route_count(self):
        """The single app has 9 routes (6 protocol + 3 control: status + 2 lifecycle)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = gateway_server.create_app(_make_store(tmp))
            assert len(app.routes) == 9

    def test_protocol_route_paths_present(self):
        """Each expected protocol route path is registered."""
        with tempfile.TemporaryDirectory() as tmp:
            app = gateway_server.create_app(_make_store(tmp))

            paths = {r.path for r in app.routes}
            assert "/openai/v1/chat/completions" in paths
            assert "/openai/v1/models" in paths
            assert "/openai/v1/{path:path}" in paths
            assert "/anthropic/v1/messages" in paths
            assert "/v1/chat/completions" in paths
            assert "/v1/messages" in paths

    def test_control_route_paths_present(self):
        """Only read-only status + lifecycle control routes are registered."""
        with tempfile.TemporaryDirectory() as tmp:
            app = gateway_server.create_app(_make_store(tmp))

            paths = {r.path for r in app.routes}
            # The only control routes left.
            assert "/api/status" in paths
            assert "/api/daemon/stop" in paths
            assert "/api/daemon/restart" in paths
            # Config write/test/fetch endpoints were removed (SSRF surface).
            assert "/api/models" not in paths
            assert "/api/providers" not in paths
            assert "/api/providers/test" not in paths
            assert "/api/providers/models" not in paths
            assert "/api/gateway/config" not in paths
            # The old /api/models/switch route has been removed.
            assert "/api/models/switch" not in paths

    def test_routes_have_correct_methods(self):
        """Verify key routes have the expected HTTP method constraints."""
        with tempfile.TemporaryDirectory() as tmp:
            app = gateway_server.create_app(_make_store(tmp))

            by_path = {}
            for r in app.routes:
                by_path.setdefault(r.path, []).append(r)

            chat = by_path["/openai/v1/chat/completions"][0]
            assert chat.methods == {"POST"}

            models = by_path["/openai/v1/models"][0]
            # Starlette auto-adds HEAD for any GET route
            assert "GET" in models.methods

            catchall = by_path["/openai/v1/{path:path}"][0]
            assert "POST" in catchall.methods
            assert "GET" in catchall.methods

            # /api/status is GET-only (Starlette auto-adds HEAD).
            assert "GET" in by_path["/api/status"][0].methods
            # Lifecycle endpoints are POST-only.
            assert by_path["/api/daemon/stop"][0].methods == {"POST"}
            assert by_path["/api/daemon/restart"][0].methods == {"POST"}

    def test_init_state_called(self):
        """create_app calls init_state so get_state() works afterwards.

        State has no ``active_model_id`` anymore (routing is by client-sent
        model field).
        """
        with tempfile.TemporaryDirectory() as tmp:
            gateway_server.create_app(_make_store(tmp))
            state = gateway_server.get_state()
            assert state is not None
            assert not hasattr(state, "active_model_id")
            # The configured model is exposed via state.models.
            assert [m.name for m in state.models] == ["gpt5"]


# ============================================================================
# Routing by client-sent model field
# ============================================================================

class TestModelRouting:
    """Chat endpoints route by the ``model`` field in the JSON body."""

    def test_missing_model_returns_400(self):
        """A chat request without a 'model' field returns 400."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.post("/openai/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 400
            assert "model" in resp.json()["error"]

    def test_unknown_model_returns_400(self):
        """A chat request with an unknown model name returns 400."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.post("/openai/v1/chat/completions", json={
                "model": "does-not-exist",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 400
            assert "Unknown model" in resp.json()["error"]

    def test_known_model_forwards_to_handler(self):
        """A chat request with a known model forwards to openai_handler."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            success = {
                "id": "x",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant",
                            "content": "hi"}}],
            }
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(success, None)),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })
            assert resp.status_code == 200
            assert resp.json() == success

    def test_sdk_alias_path_routes_chat(self):
        """The /v1/chat/completions SDK alias also routes by model."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            success = {"id": "x", "object": "chat.completion", "choices": []}
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(success, None)),
            ):
                resp = client.post("/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })
            assert resp.status_code == 200
            assert resp.json() == success


# ============================================================================
# openai_models endpoint
# ============================================================================

class TestOpenaiModels:
    """Test the GET /openai/v1/models endpoint."""

    def test_returns_list_object(self):
        """Response has top-level 'object' set to 'list'."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            assert resp.status_code == 200
            body = resp.json()
            assert body["object"] == "list"

    def test_data_is_list(self):
        """Response 'data' is a list."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            body = resp.json()
            assert isinstance(body["data"], list)

    def test_returns_available_models(self):
        """Each model from state.models appears in the response, keyed by name."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            body = resp.json()

            state = gateway_server.get_state()
            expected_ids = {m.name for m in state.models}
            returned_ids = {m["id"] for m in body["data"]}
            assert returned_ids == expected_ids

    def test_each_entry_has_id_and_object(self):
        """Every model entry has 'id' and 'object' == 'model'."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            body = resp.json()
            for entry in body["data"]:
                assert "id" in entry
                assert entry["object"] == "model"

    def test_models_via_sdk_alias_not_registered(self):
        """The SDK alias /v1/models is not registered (only openai prefix)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/v1/models")
            # No route registered -> 404
            assert resp.status_code == 404

    def test_post_to_unknown_path_returns_404(self):
        """POST to a path not registered under any prefix returns 404."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            # /v1/models is not registered (only /openai/v1/models)
            resp = client.post("/v1/models")
            assert resp.status_code == 404

    def test_no_models_returns_empty_list(self):
        """When no models are configured, data is empty."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            # No models saved -> models.yaml defaults to empty.
            app = gateway_server.create_app(store)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"] == []


class TestOpenaiCatchall:
    """The /openai/v1/{path} catchall forwards arbitrary OpenAI endpoints."""

    def test_forwards_arbitrary_endpoint(self):
        """A JSON request with a known model is streamed through to the handler."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)

            async def fake_stream(body, provider, model_name, path):
                yield b"data: ok\n\n"

            with patch(
                "llmport.gateway.server.openai_handler.stream",
                side_effect=fake_stream,
            ):
                resp = client.post(
                    "/openai/v1/embeddings",
                    json={"model": "gpt5", "input": "x"},
                )
            assert resp.status_code == 200
            assert b"data: ok" in resp.content

    def test_non_json_body_returns_400(self):
        """A non-JSON body is rejected with 400 (model cannot be read)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.post("/openai/v1/embeddings", content=b"not-json")
            assert resp.status_code == 400

    def test_unknown_model_returns_400(self):
        """An unknown model in the catchall body returns 400."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.post(
                "/openai/v1/embeddings", json={"model": "nope"}
            )
            assert resp.status_code == 400
