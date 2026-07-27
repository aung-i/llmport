"""Tests for /api/models endpoint (Issue 5).

This endpoint replaces the alias_map duplicate logic in the TUI models screen
by providing a consolidated model list with provider bindings.

Under the new API, models are configured in the ``models`` config section
(with ``name`` + ``bindings``) rather than as provider aliases, and routing
is by the client-sent ``model`` field.  The ``/api/models`` response uses
``name`` / ``provider`` / ``upstream`` (not ``id`` / ``provider_id`` /
``model_name``) and no longer carries an ``active_model`` field.
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_app_with_providers(tmp):
    """Create the single gateway app with two providers and a multi-binding model.

    The ``chat`` model has two bindings (p1/gpt-5 + p2/claude-opus) so that
    fallback / multi-provider behaviour can be exercised.
    """
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {
                "id": "p1",
                "name": "P1",
                "protocol": "openai",
                "base_url": "https://api.p1.com",
            },
            {
                "id": "p2",
                "name": "P2",
                "protocol": "openai",
                "base_url": "https://api.p2.com",
            },
        ],
        "models": [
            {
                "name": "chat",
                "bindings": [
                    {"provider": "p1", "upstream": "gpt-5", "priority": 1},
                    {"provider": "p2", "upstream": "claude-opus", "priority": 2},
                ],
            },
            {"name": "gpt-4", "provider": "p1", "upstream": "gpt-4"},
        ],
    })
    store.save_secrets({"p1": "sk-p1", "p2": "sk-p2"})
    return create_app(store)


class TestApiModelsEndpoint:

    def test_api_models_returns_200(self):
        """GET /api/models must return 200."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app_with_providers(tmp)
            client = TestClient(app)

            resp = client.get("/api/models")
            assert resp.status_code == 200, (
                f"Expected 200, got {resp.status_code}"
            )

    def test_api_models_has_no_active_model_field(self):
        """The response must not carry an 'active_model' field (removed)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app_with_providers(tmp)
            client = TestClient(app)

            data = client.get("/api/models").json()
            assert "active_model" not in data

    def test_api_models_returns_models_with_bindings(self):
        """Response contains models keyed by 'name', each with bindings
        listing 'provider', 'upstream', and 'priority'."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app_with_providers(tmp)
            client = TestClient(app)

            data = client.get("/api/models").json()
            assert "models" in data, "Response must contain 'models' key"
            assert isinstance(data["models"], list), (
                f"Expected a list of models, got {type(data['models'])}"
            )
            assert len(data["models"]) > 0, "Must have at least one model"

            # Check each model has the right structure.
            for m in data["models"]:
                assert "name" in m, f"Model missing 'name': {m}"
                assert "bindings" in m, f"Model {m['name']} missing bindings"
                assert len(m["bindings"]) > 0, f"Model {m['name']} has no bindings"
                for b in m["bindings"]:
                    assert "provider" in b, f"Binding missing 'provider': {b}"
                    assert "upstream" in b, f"Binding missing 'upstream': {b}"

            # 'chat' should have 2 bindings (p1/gpt-5 + p2/claude-opus).
            chat_model = next(
                (m for m in data["models"] if m["name"] == "chat"), None
            )
            assert chat_model is not None, "chat model should exist (2 providers)"
            assert chat_model["provider_count"] == 2, (
                f"chat model should have provider_count=2, "
                f"got {chat_model['provider_count']}"
            )
            assert len(chat_model["bindings"]) == 2, (
                f"chat model should have 2 bindings, "
                f"got {len(chat_model['bindings'])}"
            )

            # Bindings should be sorted by priority and carry upstream names.
            providers = [b["provider"] for b in chat_model["bindings"]]
            assert providers == ["p1", "p2"]
            upstreams = [b["upstream"] for b in chat_model["bindings"]]
            assert upstreams == ["gpt-5", "claude-opus"]


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
    # POST /api/providers - SSRF rejection
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
    # DELETE /api/providers - missing id
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
    # POST /api/providers/models - anthropic protocol
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
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("models") is None, (
            f"Expected models=None for anthropic, got {data.get('models')}"
        )
        assert "Anthropic does not expose" in data.get("error", "")

    def test_fetch_models_anthropic_uppercase_protocol(self):
        """Uppercase 'Anthropic' protocol is case-sensitive: it is NOT treated
        as anthropic and falls through to the openai list_models path."""
        app = self._make_models_app()
        client = TestClient(app)
        # Mock the openai list_models call so the test is deterministic and
        # does not hit the network.
        with patch(
            "llmport.gateway.control_api.openai_handler.list_models",
            new=AsyncMock(return_value=(None, "Failed to fetch models: 401")),
        ):
            resp = client.post("/api/providers/models", json={
                "id": "ant",
                "name": "Anthropic",
                "protocol": "Anthropic",  # wrong case
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-ant-test",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data

    # ------------------------------------------------------------------
    # POST /api/providers - empty api_key clears the key
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
    # POST /api/providers/test - anthropic test_connection branch
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
            new=AsyncMock(return_value=(True, 123.4, None)),
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
    # POST /api/daemon/stop - graceful shutdown signal
    # ------------------------------------------------------------------

    def test_daemon_stop_signals_shutdown(self):
        """POST /api/daemon/stop sets should_exit on the registered server."""
        from llmport.gateway.control_api import (
            control_daemon_stop,
            set_shutdown_server,
        )
        app = Starlette(routes=[
            Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        ])
        client = TestClient(app)
        # Register a mock server with a should_exit flag.
        mock_server = MagicMock()
        mock_server.should_exit = False
        set_shutdown_server(mock_server)
        try:
            resp = client.post("/api/daemon/stop")
        finally:
            set_shutdown_server(None)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert mock_server.should_exit is True

    def test_daemon_stop_without_registered_server(self):
        """POST /api/daemon/stop still returns ok when no server is registered."""
        from llmport.gateway.control_api import (
            control_daemon_stop,
            set_shutdown_server,
        )
        app = Starlette(routes=[
            Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        ])
        client = TestClient(app)
        set_shutdown_server(None)
        resp = client.post("/api/daemon/stop")
        assert resp.status_code == 200
        assert resp.json().get("ok") is True


# ------------------------------------------------------------------
# "***" sentinel key resolution (Bug 3)
# ------------------------------------------------------------------

class TestStarSentinelResolution:
    """Tests for `api_key="***"` sentinel resolution in control API endpoints."""

    def _make_test_app(self):
        """Return a minimal Starlette app with only /api/providers/test."""
        from llmport.gateway.control_api import control_test_provider
        from starlette.routing import Route
        return Starlette(routes=[
            Route("/api/providers/test", control_test_provider, methods=["POST"]),
        ])

    def _make_providers_app(self):
        """Return a minimal Starlette app with only /api/providers routes."""
        from llmport.gateway.control_api import control_providers
        return Starlette(routes=[
            Route("/api/providers", control_providers, methods=["GET", "POST", "DELETE"]),
        ])

    def _make_fetch_app(self):
        """Return a minimal Starlette app with only /api/providers/models."""
        from llmport.gateway.control_api import control_fetch_models
        return Starlette(routes=[
            Route("/api/providers/models", control_fetch_models, methods=["POST"]),
        ])

    def _mock_state_with_provider(self, api_key="sk-real-key"):
        """Create a mock GatewayState with one provider."""
        from llmport.models.provider import ProviderConfig
        state = MagicMock()
        provider = ProviderConfig.from_dict({
            "id": "existing-provider",
            "name": "Existing",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": api_key,
        })
        state.providers = [provider]
        state.models = []
        state.save = MagicMock()
        return state

    # -- POST /api/providers/test with "***" -----------------------------

    def test_test_provider_resolves_asterisk_from_state(self):
        """POST /api/providers/test with api_key='***' must resolve from stored provider."""
        app = self._make_test_app()
        mock_state = self._mock_state_with_provider("sk-real-key")
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            with patch(
                "llmport.gateway.control_api.openai_handler.list_models",
                new=AsyncMock(return_value=([], None)),
            ):
                client = TestClient(app)
                resp = client.post("/api/providers/test", json={
                    "id": "existing-provider",
                    "name": "Existing",
                    "protocol": "openai",
                    "base_url": "https://api.example.com",
                    "api_key": "***",
                })
            # The request should go through with the resolved key (not "***")
            # We can't directly check the key, but the request should not fail with
            # key validation errors. The list_models mock will return successfully.
            assert resp.status_code == 200

    def test_test_provider_asterisk_not_found_uses_literal(self):
        """POST /api/providers/test with api_key='***' but unknown id uses literal '***'."""
        app = self._make_test_app()
        mock_state = self._mock_state_with_provider("sk-real-key")
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            with patch(
                "llmport.gateway.control_api.openai_handler.list_models",
                new=AsyncMock(return_value=([], None)),
            ):
                client = TestClient(app)
                resp = client.post("/api/providers/test", json={
                    "id": "non-existent-provider",
                    "name": "NonExistent",
                    "protocol": "openai",
                    "base_url": "https://api.example.com",
                    "api_key": "***",
                })
            # 'non-existent-provider' is not in the stored state, so "***" is sent
            # literally as the API key. This should still work with our mock.
            assert resp.status_code == 200

    # -- POST /api/providers with "***" ---------------------------------

    def test_post_providers_asterisk_keeps_existing_key(self):
        """POST /api/providers with api_key='***' on existing provider keeps old key."""
        app = self._make_providers_app()
        mock_state = self._mock_state_with_provider("sk-secret-keep")
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.post("/api/providers", json={
                "id": "existing-provider",
                "name": "Existing",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "***",
            })
        assert resp.status_code == 200
        # The stored api_key should still be the original, not overwritten by "***"
        updated = next(p for p in mock_state.providers if p.id == "existing-provider")
        assert updated.api_key == "sk-secret-keep", (
            f"Expected api_key to be preserved as 'sk-secret-keep', got {updated.api_key!r}"
        )

    def test_post_providers_asterisk_new_provider_stores_literal(self):
        """POST /api/providers with api_key='***' on NEW provider stores '***' literally."""
        app = self._make_providers_app()
        mock_state = self._mock_state_with_provider("sk-other")
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            client = TestClient(app)
            resp = client.post("/api/providers", json={
                "id": "brand-new-provider",
                "name": "Brand New",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "***",
            })
        assert resp.status_code == 200
        # The new provider doesn't exist in state yet, so "***" is stored literally
        new_provider = next(p for p in mock_state.providers if p.id == "brand-new-provider")
        assert new_provider is not None
        assert new_provider.api_key == "***", (
            f"Expected api_key to be '***', got {new_provider.api_key!r}"
        )

    # -- POST /api/providers/models with "***" --------------------------

    def test_fetch_models_resolves_asterisk_from_state(self):
        """POST /api/providers/models with api_key='***' resolves from stored provider."""
        app = self._make_fetch_app()
        mock_state = self._mock_state_with_provider("sk-fetch-key")
        with patch("llmport.gateway.control_api.get_state", return_value=mock_state):
            with patch(
                "llmport.gateway.control_api.openai_handler.list_models",
                new=AsyncMock(return_value=(["gpt-5", "gpt-4"], None)),
            ):
                client = TestClient(app)
                resp = client.post("/api/providers/models", json={
                    "id": "existing-provider",
                    "name": "Existing",
                    "protocol": "openai",
                    "base_url": "https://api.example.com",
                    "api_key": "***",
                })
            assert resp.status_code == 200
            data = resp.json()
            # Should have resolved key and fetched models successfully
            assert data.get("models") == ["gpt-5", "gpt-4"]
