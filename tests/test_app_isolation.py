"""Tests for the single-app gateway architecture.

The gateway is a SINGLE-app/single-port design. ``create_app(store)``
returns ONE Starlette app that serves the protocol-forwarding routes
(``/openai/v1/*``, ``/anthropic/v1/*``) and a read-only ``/health``
liveness probe on a single port. Lifecycle control (stop / restart) is
via process signals from the CLI, not HTTP, so no control surface rides
on the forwarding port.
"""

import tempfile

from starlette.applications import Starlette
from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app
from tests._helpers import TEST_API_KEY, AuthedClient


def _make_app():
    """Build a single Starlette app backed by a fresh ConfigStore.

    Returns ``(app, tmp)`` so the caller can keep the temp dir alive for
    the lifetime of the client.
    """
    tmp = tempfile.TemporaryDirectory()
    store = ConfigStore(tmp.name)
    store.init_first_run()
    store.set_api_key(TEST_API_KEY)
    app = create_app(store)
    return app, tmp


# ──────────────────────────────────────────────
# App structure
# ──────────────────────────────────────────────

class TestSingleAppStructure:

    def test_create_app_returns_single_starlette_app(self):
        """create_app() returns a single Starlette instance, not a tuple/list."""
        app, tmp = _make_app()
        try:
            assert isinstance(app, Starlette), (
                f"Expected a single Starlette app, got {type(app)}"
            )
            # Must NOT be a tuple or list (old two-app design is gone).
            assert not isinstance(app, (tuple, list)), (
                "create_app() must not return a tuple/list; the two-app "
                "design was removed."
            )
        finally:
            tmp.cleanup()

    def test_single_app_has_protocol_routes_and_health(self):
        """The one app has the protocol routes + /health, and no /api/* or /v1/* aliases."""
        app, tmp = _make_app()
        try:
            paths = {r.path for r in app.routes}
            # OpenAI protocol
            assert "/openai/v1/chat/completions" in paths
            assert "/openai/v1/models" in paths
            # Anthropic protocol
            assert "/anthropic/v1/messages" in paths
            # Read-only health probe
            assert "/health" in paths
            # No HTTP control surface; no SDK aliases.
            assert "/api/status" not in paths
            assert "/api/daemon/stop" not in paths
            assert "/api/daemon/restart" not in paths
            assert "/v1/chat/completions" not in paths
            assert "/v1/messages" not in paths
        finally:
            tmp.cleanup()

    def test_no_models_switch_route_registered(self):
        """The removed /api/models/switch route must not be registered."""
        app, tmp = _make_app()
        try:
            paths = {r.path for r in app.routes}
            assert "/api/models/switch" not in paths, (
                "/api/models/switch was removed and must not be registered"
            )
        finally:
            tmp.cleanup()


# ──────────────────────────────────────────────
# Single app serves routes
# ──────────────────────────────────────────────

class TestSingleAppServesAllRoutes:

    def test_get_health_returns_200(self):
        """GET /health on the single app returns 200 with status ok."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.get("/health")
            assert resp.status_code == 200, (
                f"GET /health should return 200, got {resp.status_code}"
            )
            assert resp.json()["status"] == "ok"
        finally:
            tmp.cleanup()

    def test_get_openai_models_returns_200(self):
        """GET /openai/v1/models on the single app returns 200."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.get("/openai/v1/models")
            assert resp.status_code == 200, (
                f"GET /openai/v1/models should return 200, got {resp.status_code}"
            )
        finally:
            tmp.cleanup()

    def test_v1_chat_completions_alias_removed(self):
        """The /v1/chat/completions SDK alias was removed -> 404."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.post("/v1/chat/completions", json={})
            assert resp.status_code == 404, (
                f"/v1/chat/completions was removed and must 404, got {resp.status_code}"
            )
        finally:
            tmp.cleanup()

    def test_v1_messages_alias_removed(self):
        """The /v1/messages SDK alias was removed -> 404."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.post("/v1/messages", json={})
            assert resp.status_code == 404, (
                f"/v1/messages was removed and must 404, got {resp.status_code}"
            )
        finally:
            tmp.cleanup()

    def test_post_openai_chat_completions_missing_model_returns_400(self):
        """POST /openai/v1/chat/completions with no model returns 400 (route exists)."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.post("/openai/v1/chat/completions", json={})
            assert resp.status_code == 400, (
                f"POST /openai/v1/chat/completions with missing model should "
                f"return 400 (route exists), got {resp.status_code}"
            )
            assert resp.status_code != 404, "Route must exist (not 404)"
        finally:
            tmp.cleanup()

    def test_post_anthropic_messages_missing_model_returns_400(self):
        """POST /anthropic/v1/messages with no model returns 400 (route exists)."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.post("/anthropic/v1/messages", json={})
            assert resp.status_code == 400, (
                f"POST /anthropic/v1/messages with missing model should "
                f"return 400 (route exists), got {resp.status_code}"
            )
            assert resp.status_code != 404, "Route must exist (not 404)"
        finally:
            tmp.cleanup()

    def test_api_models_switch_returns_404(self):
        """The removed /api/models/switch route returns 404."""
        app, tmp = _make_app()
        try:
            client = AuthedClient(app)
            resp = client.post("/api/models/switch", json={})
            assert resp.status_code == 404, (
                f"/api/models/switch was removed and must 404, "
                f"got {resp.status_code}"
            )
        finally:
            tmp.cleanup()


# ──────────────────────────────────────────────
# health module is importable; control_api module is gone
# ──────────────────────────────────────────────

class TestHealthModule:

    def test_health_module_importable(self):
        """The health module must be importable."""
        from llmport.gateway import health
        assert health is not None

    def test_health_endpoint_function_exists(self):
        """The health endpoint function must exist and be callable."""
        from llmport.gateway.health import health as health_handler
        assert callable(health_handler)

    def test_control_api_module_removed(self):
        """The control_api module was removed (control is via CLI/signals)."""
        import importlib.util
        assert importlib.util.find_spec("llmport.gateway.control_api") is None

    def test_no_control_endpoint_functions(self):
        """The control lifecycle functions must not exist on the health module."""
        from llmport.gateway import health
        for name in ("control_status", "control_daemon_stop", "control_daemon_restart"):
            assert not hasattr(health, name), f"health.{name} should not exist"
