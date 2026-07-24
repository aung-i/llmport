"""Tests for /api/models endpoint (Issue 5).

This endpoint replaces the alias_map duplicate logic in the TUI models screen
by providing a consolidated model list with provider bindings.
"""

import tempfile

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
