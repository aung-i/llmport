"""Integration tests for gateway server."""

import tempfile

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_store(tmp):
    """Create a ConfigStore with one OpenAI provider and one logical model."""
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "protocol": "openai",
                "base_url": "https://api.openai.com",
            },
        ],
        "models": [
            {"name": "gpt-5", "provider": "openai", "upstream": "gpt-5"},
        ],
    })
    store.save_secrets({"openai": "sk-test"})
    return store


def test_create_app_returns_single_starlette_app():
    """create_app returns a single Starlette app (not a tuple).

    Both protocol routes and ``/api/*`` control routes live on the same app.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        app = create_app(store)

        # Single app, not a tuple.
        assert app is not None
        assert app.__class__.__name__ == "Starlette"

        paths = {r.path for r in app.routes}
        # Protocol routes are on the same app as control routes.
        assert "/openai/v1/chat/completions" in paths
        assert "/openai/v1/models" in paths
        assert "/anthropic/v1/messages" in paths
        assert "/api/status" in paths
        assert "/api/models" in paths
        assert "/api/providers" in paths
