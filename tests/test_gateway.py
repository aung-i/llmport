"""Integration tests for gateway server."""

import pytest
from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app
from llmport.models.provider import ProviderConfig, ProviderModel


def test_create_app_returns_starlette():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        data["providers"].append({
            "id": "openai",
            "name": "OpenAI",
            "protocol": "openai",
            "base_url": "https://api.openai.com",
            "api_key": "sk-test",
            "models": [{"name": "gpt-5", "aliases": ["gpt5"]}],
        })
        data["active_model"] = "gpt5"
        store.save(data)
        gateway_app, control_app = create_app(store)
        assert gateway_app is not None
        assert control_app is not None
        # Gateway should have 4 routes (chat, models, catchall, messages)
        assert len(gateway_app.routes) == 4
        # Control should have 8 routes
        assert len(control_app.routes) == 8
