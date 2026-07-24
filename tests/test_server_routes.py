"""Tests for ``create_app()`` and route handlers in ``server.py``.

Uses the existing pattern from ``test_gateway.py``: creates a real
``ConfigStore`` with a temporary directory, registers a single provider,
and exercises the app through ``starlette.testclient.TestClient``.
"""

import tempfile
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway import server as gateway_server


_BASE_PROVIDER = {
    "id": "test-p",
    "name": "Test",
    "protocol": "openai",
    "base_url": "https://api.example.com",
    "api_key": "sk-test",
    "models": [{"name": "gpt-5", "aliases": ["gpt5"]}],
}


def _make_app(tmp: str):
    """Create a gateway app with one OpenAI provider. Returns the gateway app."""
    store = ConfigStore(tmp)
    store.init_first_run()
    data = store.load()
    data["providers"].append(_BASE_PROVIDER)
    data["active_model"] = "gpt5"
    store.save(data)
    gateway_app, _control_app = gateway_server.create_app(store)
    return gateway_app


# ============================================================================
# create_app()
# ============================================================================

class TestCreateApp:
    """Verify the application factory registers all expected routes."""

    def test_returns_two_starlette_apps(self):
        """create_app returns a (gateway, control) tuple of Starlette apps."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            gw, ctrl = gateway_server.create_app(store)

            assert gw is not None
            assert ctrl is not None
            assert gw.__class__.__name__ == "Starlette"
            assert ctrl.__class__.__name__ == "Starlette"

    def test_gateway_has_correct_route_count(self):
        """Gateway app has 6 routes (chat, models, catchall, messages, SDK aliases)."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            gw, _ctrl = gateway_server.create_app(store)
            assert len(gw.routes) == 6

    def test_control_has_correct_route_count(self):
        """Control app has 9 routes."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            _gw, ctrl = gateway_server.create_app(store)
            assert len(ctrl.routes) == 9

    def test_gateway_route_paths_present(self):
        """Each expected gateway route path is registered."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            gw, _ctrl = gateway_server.create_app(store)

            paths = {r.path for r in gw.routes}
            assert "/openai/v1/chat/completions" in paths
            assert "/openai/v1/models" in paths
            assert "/openai/v1/{path:path}" in paths
            assert "/anthropic/v1/messages" in paths
            assert "/v1/chat/completions" in paths
            assert "/v1/messages" in paths

    def test_control_route_paths_present(self):
        """Each expected control route path is registered."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            _gw, ctrl = gateway_server.create_app(store)

            paths = {r.path for r in ctrl.routes}
            assert "/api/status" in paths
            assert "/api/models/switch" in paths
            assert "/api/models" in paths
            assert "/api/providers" in paths
            assert "/api/providers/test" in paths
            assert "/api/providers/models" in paths
            assert "/api/gateway/config" in paths
            assert "/api/daemon/stop" in paths
            assert "/api/daemon/restart" in paths

    def test_gateway_routes_have_correct_methods(self):
        """Verify key routes have the expected HTTP method constraints."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            gw, _ctrl = gateway_server.create_app(store)

            by_path = {}
            for r in gw.routes:
                by_path[r.path] = r

            chat = by_path["/openai/v1/chat/completions"]
            assert chat.methods == {"POST"}

            models = by_path["/openai/v1/models"]
            # Starlette auto-adds HEAD for any GET route
            assert "GET" in models.methods

            catchall = by_path["/openai/v1/{path:path}"]
            assert "POST" in catchall.methods
            assert "GET" in catchall.methods

    def test_init_state_called(self):
        """create_app calls init_state so get_state() works afterwards."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            data = store.load()
            data["providers"].append(_BASE_PROVIDER)
            data["active_model"] = "gpt5"
            store.save(data)
            gateway_server.create_app(store)
            state = gateway_server.get_state()
            assert state is not None
            assert state.active_model_id == "gpt5"


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
        """Each model from state.models appears in the response."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            body = resp.json()

            state = gateway_server.get_state()
            expected_ids = {m.id for m in state.models}
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

    def test_models_via_sdk_alias(self):
        """The SDK alias /v1/models is not registered (only openai prefix)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            resp = client.get("/v1/models")
            # No route registered -> 405 (method not allowed) or 404
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
        """When no providers/models are configured, data is empty."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            # Don't add any providers — models list will be empty
            data = store.load()
            data["active_model"] = "gpt5"
            store.save(data)
            app, _ctrl = gateway_server.create_app(store)
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"] == []
