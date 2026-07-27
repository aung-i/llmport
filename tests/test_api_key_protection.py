"""Tests for API key protection (Issue 6).

When the user sends ``"***"`` as the ``api_key`` value (the masked sentinel
returned by ``to_dict(include_key=False)``), the existing key must be preserved
rather than overwritten with the literal string ``"***"``.
"""

import tempfile

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


_REAL_KEY = "sk-real-secret-key-not-masked"


def _make_app_with_provider(tmp):
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {
                "id": "test-p",
                "name": "Test",
                "protocol": "openai",
                "base_url": "https://api.example.com",
            },
        ],
        "models": [],
    })
    store.save_secrets({"test-p": _REAL_KEY})
    app = create_app(store)
    return app


class TestApiKeyProtection:

    def test_asterisks_preserves_existing_key(self):
        """POST with api_key="***" must NOT overwrite the real key."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app_with_provider(tmp)
            client = TestClient(app)

            # Send "***" as the key - should be treated as "keep existing"
            resp = client.post("/api/providers", json={
                "id": "test-p",
                "name": "Test",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "***",
            })
            assert resp.status_code == 200

            # Fetch the provider and verify key is preserved
            resp = client.get("/api/providers")
            assert resp.status_code == 200
            providers = resp.json()
            # The key should be masked ("***") in the public listing,
            # but internally the real key must still be stored
            assert providers[0]["api_key"] == "***", (
                "Public listing should mask the key as '***'"
            )

            # Verify the real key is preserved by checking through ProviderConfig
            from llmport.gateway.state import STATE
            provider = next(p for p in STATE.providers if p.id == "test-p")
            assert provider.api_key == _REAL_KEY, (
                f"Real key was overwritten! Expected {_REAL_KEY}, got {provider.api_key}"
            )

    def test_new_key_overwrites(self):
        """POST with a new (non-"***") api_key should replace the existing key."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app_with_provider(tmp)
            client = TestClient(app)

            new_key = "sk-new-key"
            resp = client.post("/api/providers", json={
                "id": "test-p",
                "name": "Test",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": new_key,
            })
            assert resp.status_code == 200

            from llmport.gateway.state import STATE
            provider = next(p for p in STATE.providers if p.id == "test-p")
            assert provider.api_key == new_key, (
                f"Expected {new_key}, got {provider.api_key}"
            )

    def test_new_provider_with_asterisks_uses_sentinel(self):
        """A brand-new provider with api_key="***" should store "***"
        literally (there is no existing key to preserve)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app_with_provider(tmp)
            client = TestClient(app)

            # Add a brand-new provider with "***"
            resp = client.post("/api/providers", json={
                "id": "new-p",
                "name": "New",
                "protocol": "openai",
                "base_url": "https://api.new.com",
                "api_key": "***",
            })
            assert resp.status_code == 200

            from llmport.gateway.state import STATE
            provider = next(p for p in STATE.providers if p.id == "new-p")
            # For a new provider there is no "existing key", so "***" stays
            # (the caller should not send "***" for a new provider)
            assert provider.api_key == "***", (
                f"For a new provider '***' should be stored as-is, got {provider.api_key}"
            )
