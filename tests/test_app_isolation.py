"""Tests for control API / gateway API isolation (Issue 2).

The spec requires create_app() to return two separate Starlette instances:
- gateway_app:  OpenAI / Anthropic protocol routes only (no /api/*)
- control_app:  /api/* control routes only

Cross-requests between the two apps must return 404.
"""

import tempfile

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_store(tmp):
    store = ConfigStore(tmp)
    store.init_first_run()
    return store


# ──────────────────────────────────────────────
# App structure
# ──────────────────────────────────────────────

class TestAppSeparation:

    def test_create_app_returns_two_apps(self):
        """create_app() now returns a (gateway_app, control_app) tuple."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            result = create_app(store)
            assert isinstance(result, tuple), (
                f"Expected tuple[Starlette, Starlette], got {type(result)}"
            )
            assert len(result) == 2

    def test_gateway_app_excludes_api_routes(self):
        """The gateway app should contain protocol routes but no /api/* routes."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            gateway_app, control_app = create_app(store)

            gateway_paths = {r.path for r in gateway_app.routes}
            # Must contain protocol endpoints
            assert any("/openai/v1" in p for p in gateway_paths), (
                "Gateway app missing OpenAI routes"
            )
            assert any("/anthropic/v1" in p for p in gateway_paths), (
                "Gateway app missing Anthropic routes"
            )
            # Must NOT contain any /api/ route
            assert not any(p.startswith("/api") for p in gateway_paths), (
                f"Gateway app should not have /api/ routes, got: "
                f"{[p for p in gateway_paths if p.startswith('/api')]}"
            )

    def test_control_app_only_has_api_routes(self):
        """The control app should contain /api/* routes and nothing else."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            gateway_app, control_app = create_app(store)

            control_paths = {r.path for r in control_app.routes}
            assert len(control_paths) > 0, "Control app has no routes"
            assert all(p.startswith("/api") for p in control_paths), (
                f"Control app should only have /api/ routes, got: "
                f"{[p for p in control_paths if not p.startswith('/api')]}"
            )


# ──────────────────────────────────────────────
# Cross-request 404 isolation
# ──────────────────────────────────────────────

class TestCrossRequestIsolation:

    def test_gateway_app_returns_404_for_api_request(self):
        """GET /api/status on the gateway app returns 404."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            gateway_app, control_app = create_app(store)
            client = TestClient(gateway_app)

            resp = client.get("/api/status")
            assert resp.status_code == 404, (
                f"Gateway app should return 404 for /api/status, "
                f"got {resp.status_code}"
            )

    def test_control_app_returns_404_for_gateway_request(self):
        """GET /openai/v1/models on the control app returns 404."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            gateway_app, control_app = create_app(store)
            client = TestClient(control_app)

            resp = client.get("/openai/v1/models")
            assert resp.status_code == 404, (
                f"Control app should return 404 for /openai/v1/models, "
                f"got {resp.status_code}"
            )

    def test_control_app_returns_404_for_chat_completions(self):
        """POST /openai/v1/chat/completions on the control app returns 404."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            gateway_app, control_app = create_app(store)
            client = TestClient(control_app)

            resp = client.post("/openai/v1/chat/completions", json={})
            assert resp.status_code == 404, (
                f"Control app should return 404 for chat completions, "
                f"got {resp.status_code}"
            )

    def test_control_app_returns_404_for_anthropic_messages(self):
        """POST /anthropic/v1/messages on the control app returns 404."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            gateway_app, control_app = create_app(store)
            client = TestClient(control_app)

            resp = client.post("/anthropic/v1/messages", json={})
            assert resp.status_code == 404, (
                f"Control app should return 404 for anthropic messages, "
                f"got {resp.status_code}"
            )
