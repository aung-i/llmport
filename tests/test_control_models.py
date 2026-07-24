"""Tests for /api/models endpoint (Issue 5).

This endpoint replaces the alias_map duplicate logic in the TUI models screen
by providing a consolidated model list with provider bindings.
"""

import tempfile
from unittest.mock import MagicMock, patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_app_with_providers(tmp):
    store = ConfigStore(tmp)
    store.init_first_run()
    data = store.load()
    data["providers"] = [
        {
            "id": "p1",
            "name": "P1",
            "protocol": "openai",
            "base_url": "https://api.p1.com",
            "api_key": "sk-p1",
            "models": [
                {"name": "gpt-5", "aliases": ["gpt5", "chat"]},
                {"name": "gpt-4", "aliases": ["gpt4"]},
            ],
        },
        {
            "id": "p2",
            "name": "P2",
            "protocol": "openai",
            "base_url": "https://api.p2.com",
            "api_key": "sk-p2",
            "models": [
                {"name": "claude-opus", "aliases": ["opus", "chat"]},
            ],
        },
    ]
    data["active_model"] = "chat"
    store.save(data)
    gateway_app, control_app = create_app(store)
    return control_app


class TestApiModelsEndpoint:

    def test_api_models_returns_200(self):
        """GET /api/models must return 200."""
        with tempfile.TemporaryDirectory() as tmp:
            control_app = _make_app_with_providers(tmp)
            client = TestClient(control_app)

            resp = client.get("/api/models")
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}"
            )

    def test_api_models_returns_merged_models_with_bindings(self):
        """Response must contain models merged by alias, each with bindings
        listing provider_id, model_name, and priority."""
        with tempfile.TemporaryDirectory() as tmp:
            control_app = _make_app_with_providers(tmp)
            client = TestClient(control_app)

            resp = client.get("/api/models")
            data = resp.json()
            assert "models" in data, "Response must contain 'models' key"
            assert isinstance(data["models"], list), (
                f"Expected a list of models, got {type(data['models'])}"
            )
            assert len(data["models"]) > 0, "Must have at least one model"

            # Check each model has the right structure
            for m in data["models"]:
                assert "id" in m, f"Model missing 'id': {m}"
                assert "bindings" in m, f"Model {m['id']} missing bindings"
                assert len(m["bindings"]) > 0, f"Model {m['id']} has no bindings"
                for b in m["bindings"]:
                    assert "provider_id" in b, f"Binding missing provider_id: {b}"
                    assert "model_name" in b, f"Binding missing model_name: {b}"

            # 'chat' alias should have 2 bindings (p1/gpt-5 + p2/claude-opus)
            chat_model = next((m for m in data["models"] if m["id"] == "chat"), None)
            assert chat_model is not None, "chat model should exist (2 providers)"
            assert len(chat_model["bindings"]) == 2, (
                f"chat model should have 2 bindings, got {len(chat_model['bindings'])}"
            )


class TestControlApiErrorPaths:
    """Error-path tests for the control API endpoints, using a minimal
    Starlette app and a mock state."""

    def _make_providers_app(self):
        """Return a minimal Starlette app with only the /api/providers route."""
        from llmport.gateway.control_api import control_providers
        return Starlette(routes=[
            Route("/api/providers", control_providers, methods=["GET", "POST", "DELETE"]),
        ])

    def _make_models_app(self):
        """Return a minimal Starlette app with only /api/providers/models."""
        from llmport.gateway.control_api import control_fetch_models
        return Starlette(routes=[
            Route("/api/providers/models", control_fetch_models, methods=["POST"]),
        ])

    def _mock_state(self, providers=None, models=None):
        """Create a mock GatewayState with the given data."""
        state = MagicMock()
        state.providers = providers or []
        state.models = models or []
        state.save = MagicMock()
        return state

    # ------------------------------------------------------------------
    # POST /api/providers — SSRF rejection
    # ------------------------------------------------------------------

    def test_post_providers_rejects_private_url(self):
        """POST /api/providers with a private base_url must return 400."""
        app = self._make_providers_app()
        mock_state = self._mock_state()
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.post("/api/providers", json={
                "id": "test-p",
                "name": "Test",
                "protocol": "openai",
                "base_url": "http://192.168.1.1",
                "api_key": "sk-test",
            })
        assert resp.status_code == 400, (
            f"Expected 400 for private URL, got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("ok") is False
        assert "内网" in data.get("error", "")

    # ------------------------------------------------------------------
    # DELETE /api/providers — missing id
    # ------------------------------------------------------------------

    def test_delete_providers_empty_body(self):
        """DELETE /api/providers with no body must return 400."""
        app = self._make_providers_app()
        mock_state = self._mock_state()
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.request("DELETE", "/api/providers", json={})
        assert resp.status_code == 400, (
            f"Expected 400 for missing id, got {resp.status_code}"
        )
        data = resp.json()
        assert data.get("ok") is False
        assert "Missing provider id" in data.get("error", "")

    def test_delete_providers_no_id_in_body(self):
        """DELETE /api/providers with a body but no 'id' key must return 400."""
        app = self._make_providers_app()
        mock_state = self._mock_state()
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.request("DELETE", "/api/providers", json={"name": "whatever"})
        assert resp.status_code == 400
        data = resp.json()
        assert "Missing provider id" in data.get("error", "")

    # ------------------------------------------------------------------
    # POST /api/providers/models — anthropic protocol
    # ------------------------------------------------------------------

    def test_fetch_models_anthropic(self):
        """POST /api/providers/models with an anthropic protocol provider must
        return models=None and an informational error message."""
        app = self._make_models_app()
        client = TestClient(app)
        resp = client.post("/api/providers/models", json={
            "id": "ant",
            "name": "Anthropic",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-test",
            "models": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("models") is None, (
            f"Expected models=None for anthropic, got {data.get('models')}"
        )
        assert "Anthropic does not expose" in data.get("error", "")

    def test_fetch_models_anthropic_uppercase_protocol(self):
        """Anthropic protocol (case-sensitive check — must be lowercase)."""
        app = self._make_models_app()
        client = TestClient(app)
        resp = client.post("/api/providers/models", json={
            "id": "ant",
            "name": "Anthropic",
            "protocol": "Anthropic",  # wrong case
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-test",
        })
        assert resp.status_code == 200
        data = resp.json()
        # Uppercase 'Anthropic' != 'anthropic' so it falls into the else branch
        # but openai_handler.list_models would be called and fail.
        # The behaviour here is not well-defined — we just assert the endpoint
        # doesn't crash and returns some error.
        assert "error" in data

    # ------------------------------------------------------------------
    # POST /api/providers — empty api_key clears the key
    # ------------------------------------------------------------------

    def test_post_providers_empty_api_key_creates_new_provider_with_empty_key(self):
        """POST /api/providers with api_key="" on a new provider must store
        an empty key."""
        app = self._make_providers_app()
        mock_state = self._mock_state()
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.post("/api/providers", json={
                "id": "new-p",
                "name": "New",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "",
                "models": [],
            })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert len(mock_state.providers) == 1
        assert mock_state.providers[0].api_key == "", (
            f"Expected empty api_key, got {mock_state.providers[0].api_key!r}"
        )

    def test_post_providers_empty_api_key_overwrites_existing_key(self):
        """POST /api/providers with api_key="" on an existing provider must
        clear its key to empty string."""
        from llmport.models.provider import ProviderConfig
        app = self._make_providers_app()
        existing = ProviderConfig.from_dict({
            "id": "existing-p",
            "name": "Existing",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-old-secret",
        })
        mock_state = self._mock_state(providers=[existing])
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.post("/api/providers", json={
                "id": "existing-p",
                "name": "Existing",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "",
                "models": [],
            })
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        # Find the provider in the state (it should still be there with empty key)
        updated = next(p for p in mock_state.providers if p.id == "existing-p")
        assert updated.api_key == "", (
            f"Expected empty api_key, got {updated.api_key!r}"
        )

    # ------------------------------------------------------------------
    # POST /api/providers/test — anthropic test_connection branch
    # ------------------------------------------------------------------

    def test_test_provider_anthropic(self):
        """POST /api/providers/test with an anthropic provider must call
        anthropic_handler.test_connection and return its result."""
        from llmport.gateway.control_api import control_test_provider
        app = Starlette(routes=[
            Route("/api/providers/test", control_test_provider, methods=["POST"]),
        ])
        client = TestClient(app)
        with patch(
            "llmport.gateway.control_api.anthropic_handler.test_connection",
            return_value=(True, 123.4, None),
        ):
            resp = client.post("/api/providers/test", json={
                "id": "ant",
                "name": "Anthropic",
                "protocol": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-ant",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("latency_ms") == 123.4
        assert data.get("error") is None

    # ------------------------------------------------------------------
    # POST /api/daemon/stop — graceful shutdown signal
    # ------------------------------------------------------------------

    def test_daemon_stop_sends_sigterm(self):
        """POST /api/daemon/stop must call os.kill(os.getpid(), SIGTERM)."""
        from llmport.gateway.control_api import control_daemon_stop
        import os
        import signal as sig_mod
        app = Starlette(routes=[
            Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        ])
        client = TestClient(app)
        # The control_daemon_stop function does `import os` locally, so we
        # patch os.kill directly (the function binds to the os module at runtime).
        with patch("os.kill") as mock_kill:
            resp = client.post("/api/daemon/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        # Verify os.kill was called with the current PID and SIGTERM
        mock_kill.assert_called_once_with(os.getpid(), sig_mod.SIGTERM)
