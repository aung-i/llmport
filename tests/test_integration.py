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
        resp = client.get("/openai/v1/models")
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

        resp = client.post("/openai/v1/chat/completions", json={
            "model": "claude",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 400
        assert "Anthropic" in resp.json()["error"]


def test_gateway_config():
    """Test gateway config GET and POST via control API."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        app = create_app(store)
        client = TestClient(app)

        # GET default config
        resp = client.get("/api/gateway/config")
        assert resp.status_code == 200
        cfg = resp.json()
        assert cfg["host"] == "127.0.0.1"
        assert cfg["port"] == 11434

        # POST update config
        resp = client.post("/api/gateway/config", json={
            "host": "localhost",
            "port": 9999,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["gateway"]["host"] == "localhost"
        assert resp.json()["gateway"]["port"] == 9999

        # GET confirm persistence
        resp = client.get("/api/gateway/config")
        assert resp.status_code == 200
        assert resp.json()["host"] == "localhost"
        assert resp.json()["port"] == 9999

        # POST with dangerous host is rejected
        resp = client.post("/api/gateway/config", json={
            "host": "0.0.0.0",
            "port": 11434,
        })
        assert resp.status_code == 400
        assert "不允许的主机地址" in resp.json()["error"]

        # POST with invalid port is rejected
        resp = client.post("/api/gateway/config", json={
            "host": "127.0.0.1",
            "port": 80,
        })
        assert resp.status_code == 400
        assert "端口号超出范围" in resp.json()["error"]
