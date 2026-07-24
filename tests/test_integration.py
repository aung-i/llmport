"""End-to-end integration test for config + gateway flow."""

import tempfile
from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app
from llmport.gateway.state import migrate_gateway_config
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

        # Create apps
        gateway_app, control_app = create_app(store)
        control = TestClient(control_app)
        gateway = TestClient(gateway_app)

        # Check status via control API
        resp = control.get("/api/status")
        assert resp.status_code == 200
        status = resp.json()
        assert status["active_model"] == "test"
        assert "total_tokens" in status

        # Switch model via control API
        resp = control.post("/api/models/switch", json={"model_id": "test"})
        assert resp.status_code == 200

        # Models endpoint via gateway
        resp = gateway.get("/openai/v1/models")
        assert resp.status_code == 200
        models = resp.json()
        assert len(models["data"]) >= 1


def test_control_api_providers():
    """Test provider CRUD via control API."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

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
        gateway_app, control_app = create_app(store)
        client = TestClient(gateway_app)

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
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

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
        assert resp.json().get("warning") is None  # loopback → no warning

        # GET confirm persistence
        resp = client.get("/api/gateway/config")
        assert resp.status_code == 200
        assert resp.json()["host"] == "localhost"
        assert resp.json()["port"] == 9999

        # POST with empty host is rejected
        resp = client.post("/api/gateway/config", json={
            "host": "",
            "port": 11434,
        })
        assert resp.status_code == 400
        assert "无效的主机地址" in resp.json()["error"]

        # POST with invalid port is rejected
        resp = client.post("/api/gateway/config", json={
            "host": "127.0.0.1",
            "port": 80,
        })
        assert resp.status_code == 400
        assert "端口号超出范围" in resp.json()["error"]


def test_config_migration_old_format():
    """migrate_gateway_config converts old openai_port format."""
    data = {"gateway": {"openai_port": 8080}}
    result = migrate_gateway_config(data)
    assert result == {"host": "127.0.0.1", "port": 8080}
    # Data dict was migrated in place
    assert data["gateway"] == {"host": "127.0.0.1", "port": 8080}


def test_config_migration_empty_gateway():
    """migrate_gateway_config with empty gateway uses defaults."""
    data = {"gateway": {}}
    result = migrate_gateway_config(data)
    assert result == {"host": "127.0.0.1", "port": 11434}


def test_provider_delete():
    """Provider can be deleted via DELETE /api/providers."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

        # Add a provider
        client.post("/api/providers", json={
            "id": "test-provider",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.test.com",
            "api_key": "sk-test",
            "models": [{"name": "gpt-5", "aliases": ["gpt5"]}],
        })

        # Confirm it exists
        resp = client.get("/api/providers")
        assert len(resp.json()) == 1

        # Delete via DELETE
        resp = client.request("DELETE", "/api/providers", json={"id": "test-provider"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify list is empty
        resp = client.get("/api/providers")
        assert resp.json() == []


def test_daemon_restart_endpoint():
    """POST /api/daemon/restart returns ok with action."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

        resp = client.post("/api/daemon/restart")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["action"] == "restart"


def test_gateway_config_rejects_non_loopback():
    """POST /api/gateway/config with non-loopback address is rejected (Issue 9)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

        resp = client.post("/api/gateway/config", json={
            "host": "0.0.0.0",
            "port": 11434,
        })
        assert resp.status_code == 400
        data = resp.json()
        assert data["ok"] is False
        assert "回环地址" in data["error"]


def test_gateway_config_rejects_empty_host():
    """POST /api/gateway/config with empty host returns 400."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

        resp = client.post("/api/gateway/config", json={
            "host": "",
            "port": 11434,
        })
        assert resp.status_code == 400


def test_control_test_provider_endpoint():
    """POST /api/providers/test returns ok + latency_ms."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

        resp = client.post("/api/providers/test", json={
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://httpbin.org",
            "api_key": "sk-test",
            "models": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "ok" in data
        assert "latency_ms" in data


def test_control_fetch_models_endpoint():
    """POST /api/providers/models returns models list."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        gateway_app, control_app = create_app(store)
        client = TestClient(control_app)

        resp = client.post("/api/providers/models", json={
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://httpbin.org",
            "api_key": "sk-test",
            "models": [],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data


def test_parse_models_utility():
    """Test _parse_models with various inputs."""
    from llmport.models.parser import parse_models

    # Normal input
    result = parse_models("gpt-5,gpt5,chatgpt\nclaude-opus,opus")
    assert len(result) == 2
    assert result[0]["name"] == "gpt-5"
    assert result[0]["aliases"] == ["gpt5", "chatgpt"]
    assert result[1]["name"] == "claude-opus"
    assert result[1]["aliases"] == ["opus"]

    # Empty string
    assert parse_models("") == []

    # Whitespace only
    assert parse_models("  \n  \n") == []

    # Single model no aliases
    result = parse_models("gpt-5")
    assert len(result) == 1
    assert result[0]["name"] == "gpt-5"
    assert result[0]["aliases"] == []


def test_daemon_manager_pid_file(tmp_path):
    """DaemonManager.is_running() reflects PID file state."""
    import json
    import os
    from llmport.daemon import DaemonManager

    dm = DaemonManager(str(tmp_path))
    assert dm.is_running() is False

    # Write a fake PID file with current PID
    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text(json.dumps({"pid": os.getpid(), "control_port": 12345}))
    assert dm.is_running() is True

    # Stale PID file (nonexistent process)
    pid_path.write_text(json.dumps({"pid": 99999, "control_port": 12345}))
    assert dm.is_running() is False


def test_first_run_detection_with_empty_providers():
    """When providers list is empty, first-run check should trigger."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        # Fresh config has empty providers — should be detected as first run
        assert data.get("providers") == []

        # Add a provider and verify detection works
        data["providers"].append({"id": "test", "name": "Test"})
        store.save(data)
        data = store.load()
        assert len(data["providers"]) == 1
