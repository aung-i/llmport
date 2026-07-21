"""End-to-end integration test for config + gateway flow."""

import tempfile
from llmgate.config.store import ConfigStore
from llmgate.gateway.server import create_app
from starlette.testclient import TestClient


def test_full_flow():
    """Test: configure a provider, start server, switch model, send request."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()

        # Add a provider
        data = store.load()
        data["providers"].append({
            "id": "test-provider",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://httpbin.org",
            "api_key": "sk-test",
            "models": [{"name": "test-model", "aliases": ["test"]}],
        })
        data["active_model"] = "test"
        store.save(data)

        # Create app
        app = create_app(store)
        client = TestClient(app)

        # Check status
        resp = client.get("/api/status")
        assert resp.status_code == 200
        status = resp.json()
        assert status["active_model"] == "test"

        # Switch model
        resp = client.post("/api/models/switch", json={"model_id": "test"})
        assert resp.status_code == 200

        # Models endpoint returns model list
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        models = resp.json()
        assert len(models["data"]) >= 1


def test_control_api_providers():
    """Test provider CRUD via control API."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        app = create_app(store)
        client = TestClient(app)

        # List providers (empty)
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        assert resp.json() == []

        # Add provider
        resp = client.post("/api/providers", json={
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.test.com",
            "api_key": "sk-test",
            "models": [{"name": "gpt-5", "aliases": ["gpt5"]}],
        })
        assert resp.status_code == 200

        # List providers (should have 1)
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == "test"


def test_protocol_mismatch_error():
    """Test that requesting OpenAI endpoint with Anthropic provider returns error."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        data["providers"].append({
            "id": "ant",
            "name": "Anthropic",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-test",
            "models": [{"name": "claude", "aliases": ["claude"]}],
        })
        data["active_model"] = "claude"
        store.save(data)
        app = create_app(store)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={
            "model": "claude",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 400
        assert "Anthropic" in resp.json()["error"]
