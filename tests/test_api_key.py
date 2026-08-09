"""Tests for llmport's own API key auth (Issue #1).

Covers:
  * ConfigStore persistence of the top-level ``api_key`` in providers.yaml
    (load / set / clear, provider preservation, tolerance of missing file).
  * GatewayState loading ``api_key`` into runtime state.
  * APIKeyAuthMiddleware enforcement on the gateway app: no key -> open;
    key set -> 401 without/wrong credential, 200 with bearer or x-api-key,
    ``/health`` always open.
"""

import json as _json
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
            "name": "test-p",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-test",
        },
    ],
}
_BASE_MODELS = {"models": {"gpt5": {"test-p": "gpt-5"}}}


def _make_store(tmp: str, api_key: str | None = None) -> ConfigStore:
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_providers_config(_BASE_PROVIDERS)
    store.save_models_config(_BASE_MODELS)
    if api_key is not None:
        store.set_api_key(api_key)
    return store


def _make_app(tmp: str, api_key: str | None = None):
    return gateway_server.create_app(_make_store(tmp, api_key=api_key))


def _ok_upstream():
    """A successful OpenAI chat-completion body to return from a mocked forward."""
    from llmport.gateway.handler_base import UpstreamResult
    return UpstreamResult(
        200,
        _json.dumps({
            "id": "x",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant",
                        "content": "hi"}}],
        }).encode(),
        "application/json",
        None,
    )


# ============================================================================
# ConfigStore
# ============================================================================


class TestStoreApiKey:
    def test_load_unset_returns_empty(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_providers_config(_BASE_PROVIDERS)
        assert store.load_api_key() == ""

    def test_set_then_load(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_providers_config(_BASE_PROVIDERS)
        store.set_api_key("sk-llmport-secret")
        assert store.load_api_key() == "sk-llmport-secret"

    def test_set_persists_to_providers_yaml_top_level(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_providers_config(_BASE_PROVIDERS)
        store.set_api_key("sk-llmport-secret")
        raw = store.providers_path.read_text()
        # Top-level api_key present alongside providers.
        assert "api_key: sk-llmport-secret" in raw
        assert "providers:" in raw

    def test_set_preserves_existing_providers(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_providers_config(_BASE_PROVIDERS)
        store.set_api_key("sk-llmport-secret")
        pdata = store.load_providers_config()
        assert pdata["providers"] == _BASE_PROVIDERS["providers"]
        assert pdata["api_key"] == "sk-llmport-secret"

    def test_clear_removes_key(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_providers_config(_BASE_PROVIDERS)
        store.set_api_key("sk-llmport-secret")
        store.clear_api_key()
        assert store.load_api_key() == ""
        pdata = store.load_providers_config()
        assert "api_key" not in pdata
        # Providers survive the clear.
        assert pdata["providers"] == _BASE_PROVIDERS["providers"]

    def test_clear_when_unset_is_noop(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_providers_config(_BASE_PROVIDERS)
        store.clear_api_key()  # must not raise
        assert store.load_api_key() == ""

    def test_load_tolerates_missing_providers_file(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        # No init, no providers.yaml.
        assert store.load_api_key() == ""

    def test_load_tolerates_non_string_key(self, tmp_path):
        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.providers_path.write_text("api_key: 12345\nproviders: []\n")
        assert store.load_api_key() == ""


# ============================================================================
# GatewayState
# ============================================================================


class TestStateApiKey:
    def test_state_api_key_empty_when_unset(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmp:
            gateway_server.create_app(_make_store(tmp))
            assert gateway_server.get_state().api_key == ""

    def test_state_api_key_loaded_when_set(self, tmp_path):
        with tempfile.TemporaryDirectory() as tmp:
            gateway_server.create_app(_make_store(tmp, api_key="sk-xyz"))
            assert gateway_server.get_state().api_key == "sk-xyz"


# ============================================================================
# APIKeyAuthMiddleware
# ============================================================================


class TestNoKeyConfigured:
    """When no API key is set, the gateway is open (backward compatible)."""

    def test_request_without_auth_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=_ok_upstream()),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })
            assert resp.status_code == 200

    def test_health_open_without_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            assert client.get("/health").status_code == 200


class TestKeyConfigured:
    """When an API key is set, forwarding routes require it."""

    KEY = "sk-llmport-secret"

    def test_health_open_without_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            assert client.get("/health").status_code == 200

    def test_chat_without_auth_returns_401(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            resp = client.post("/openai/v1/chat/completions", json={
                "model": "gpt5",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp.status_code == 401
            assert "API key" in resp.json()["error"]

    def test_chat_with_wrong_bearer_returns_401(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            resp = client.post(
                "/openai/v1/chat/completions",
                json={"model": "gpt5", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer wrong-key"},
            )
            assert resp.status_code == 401

    def test_chat_with_correct_bearer_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=_ok_upstream()),
            ):
                resp = client.post(
                    "/openai/v1/chat/completions",
                    json={"model": "gpt5", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": f"Bearer {self.KEY}"},
                )
            assert resp.status_code == 200

    def test_chat_with_correct_x_api_key_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=_ok_upstream()),
            ):
                resp = client.post(
                    "/openai/v1/chat/completions",
                    json={"model": "gpt5", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"x-api-key": self.KEY},
                )
            assert resp.status_code == 200

    def test_models_endpoint_requires_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            assert client.get("/openai/v1/models").status_code == 401
            resp = client.get("/openai/v1/models", headers={"x-api-key": self.KEY})
            assert resp.status_code == 200

    def test_empty_bearer_token_rejected(self):
        """``Authorization: Bearer `` (empty) must not bypass auth."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, api_key=self.KEY)
            client = TestClient(app)
            resp = client.post(
                "/openai/v1/chat/completions",
                json={"model": "gpt5", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer "},
            )
            assert resp.status_code == 401
