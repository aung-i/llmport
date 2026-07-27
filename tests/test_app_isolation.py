"""Tests for the single-app gateway architecture.

The gateway was refactored from a TWO-app/two-port design (separate
``gateway_app`` and ``control_app``) to a SINGLE-app/single-port design.
``create_app(store)`` returns ONE Starlette app that serves both the
protocol-forwarding routes (``/openai/v1/*``, ``/anthropic/v1/*``,
``/v1/*``) and the control API (``/api/*``) on a single port.

These tests assert the new design: one app, all routes, no cross-app 404
isolation (because there is only one app).
"""

import tempfile

from starlette.applications import Starlette
from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_app():
    """Build a single Starlette app backed by a fresh ConfigStore.

    Returns ``(app, tmp)`` so the caller can keep the temp dir alive for
    the lifetime of the client.
    """
    tmp = tempfile.TemporaryDirectory()
    store = ConfigStore(tmp.name)
    store.init_first_run()
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

    def test_single_app_has_both_protocol_and_control_routes(self):
        """The one app must contain both /api/* and protocol routes."""
        app, tmp = _make_app()
        try:
            paths = {r.path for r in app.routes}
            # Control API
            assert "/api/status" in paths, "Missing /api/status control route"
            assert "/api/models" in paths, "Missing /api/models control route"
            assert "/api/providers" in paths, "Missing /api/providers control route"
            # OpenAI protocol
            assert "/openai/v1/chat/completions" in paths
            assert "/openai/v1/models" in paths
            # Anthropic protocol
            assert "/anthropic/v1/messages" in paths
            # SDK alias paths
            assert "/v1/chat/completions" in paths
            assert "/v1/messages" in paths
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
# Single app serves both control and protocol routes
# ──────────────────────────────────────────────

class TestSingleAppServesAllRoutes:

    def test_get_api_status_returns_200(self):
        """GET /api/status on the single app returns 200."""
        app, tmp = _make_app()
        try:
            client = TestClient(app)
            resp = client.get("/api/status")
            assert resp.status_code == 200, (
                f"GET /api/status should return 200, got {resp.status_code}"
            )
        finally:
            tmp.cleanup()

    def test_get_openai_models_returns_200(self):
        """GET /openai/v1/models on the single app returns 200."""
        app, tmp = _make_app()
        try:
            client = TestClient(app)
            resp = client.get("/openai/v1/models")
            assert resp.status_code == 200, (
                f"GET /openai/v1/models should return 200, got {resp.status_code}"
            )
        finally:
            tmp.cleanup()

    def test_post_v1_chat_completions_missing_model_returns_400(self):
        """POST /v1/chat/completions with no model returns 400 (route exists)."""
        app, tmp = _make_app()
        try:
            client = TestClient(app)
            resp = client.post("/v1/chat/completions", json={})
            assert resp.status_code == 400, (
                f"POST /v1/chat/completions with missing model should return "
                f"400 (route exists), got {resp.status_code}"
            )
            assert resp.status_code != 404, "Route must exist (not 404)"
        finally:
            tmp.cleanup()

    def test_post_v1_messages_missing_model_returns_400(self):
        """POST /v1/messages with no model returns 400 (route exists)."""
        app, tmp = _make_app()
        try:
            client = TestClient(app)
            resp = client.post("/v1/messages", json={})
            assert resp.status_code == 400, (
                f"POST /v1/messages with missing model should return 400 "
                f"(route exists), got {resp.status_code}"
            )
            assert resp.status_code != 404, "Route must exist (not 404)"
        finally:
            tmp.cleanup()

    def test_post_openai_chat_completions_missing_model_returns_400(self):
        """POST /openai/v1/chat/completions with no model returns 400 (route exists)."""
        app, tmp = _make_app()
        try:
            client = TestClient(app)
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
            client = TestClient(app)
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
            client = TestClient(app)
            resp = client.post("/api/models/switch", json={})
            assert resp.status_code == 404, (
                f"/api/models/switch was removed and must 404, "
                f"got {resp.status_code}"
            )
        finally:
            tmp.cleanup()


# ──────────────────────────────────────────────
# control_api module is importable and endpoint functions exist
# ──────────────────────────────────────────────

class TestControlApiModule:

    def test_control_api_module_importable(self):
        """The control_api module must be importable."""
        from llmport.gateway import control_api
        assert control_api is not None

    def test_control_endpoint_functions_exist(self):
        """All control endpoint functions must exist and be callable."""
        from llmport.gateway import control_api
        expected = [
            "control_status",
            "control_models",
            "control_models_delete",
            "control_providers",
            "control_test_provider",
            "control_fetch_models",
            "control_gateway_config",
            "control_daemon_stop",
            "control_daemon_restart",
        ]
        for name in expected:
            handler = getattr(control_api, name, None)
            assert handler is not None, f"control_api.{name} is missing"
            assert callable(handler), f"control_api.{name} is not callable"

    def test_no_control_switch_model_function(self):
        """The removed control_switch_model function must not exist."""
        from llmport.gateway import control_api
        assert not hasattr(control_api, "control_switch_model"), (
            "control_switch_model was removed along with /api/models/switch"
        )
