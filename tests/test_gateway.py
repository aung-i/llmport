"""Integration tests for gateway server."""

import tempfile

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_store(tmp):
    """Create a ConfigStore with one OpenAI provider and one logical model."""
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_providers_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {
                "name": "openai",
                "protocol": "openai",
                "base_url": "https://api.openai.com",
                "api_key": "sk-test",
            },
        ],
    })
    store.save_models_config({"models": {"gpt-5": "openai"}})
    return store


def test_create_app_returns_single_starlette_app():
    """create_app returns a single Starlette app (not a tuple).

    Protocol routes and the read-only ``/health`` probe live on the same app.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = _make_store(tmp)
        app = create_app(store)

        # Single app, not a tuple.
        assert app is not None
        assert app.__class__.__name__ == "Starlette"

        paths = {r.path for r in app.routes}
        # Protocol routes + health probe on the same app.
        assert "/openai/v1/chat/completions" in paths
        assert "/openai/v1/models" in paths
        assert "/anthropic/v1/messages" in paths
        assert "/health" in paths
