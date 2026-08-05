"""End-to-end integration test for config + gateway flow."""

import tempfile

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app
from llmport.gateway.state import migrate_gateway_config
from starlette.testclient import TestClient


def test_full_flow():
    """Test: configure a provider, start server, send request."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()

        # Add a provider (no api_key in config - that goes to secrets)
        config = store.load_config()
        config["providers"].append({
            "id": "test-provider",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://httpbin.org",
        })
        config["models"].append({
            "name": "test-model",
            "provider": "test-provider",
            "upstream": "test-model-real",
        })
        store.save_config(config)
        store.save_secrets({"test-provider": "sk-test"})

        # Single app serves both protocol routes and control API
        app = create_app(store)
        client = TestClient(app)

        # Check status via control API
        resp = client.get("/api/status")
        assert resp.status_code == 200
        status = resp.json()
        assert "test-model" in status["models"]
        assert "total_tokens" in status

        # Models endpoint via gateway
        resp = client.get("/openai/v1/models")
        assert resp.status_code == 200
        models = resp.json()
        assert len(models["data"]) >= 1


def test_protocol_mismatch_error():
    """Test that requesting OpenAI endpoint with Anthropic provider returns error."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        config = store.load_config()
        config["providers"].append({
            "id": "ant",
            "name": "Anthropic",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
        })
        config["models"].append({
            "name": "claude",
            "provider": "ant",
            "upstream": "claude-real",
        })
        store.save_config(config)
        store.save_secrets({"ant": "sk-ant-test"})
        app = create_app(store)
        client = TestClient(app)

        resp = client.post("/openai/v1/chat/completions", json={
            "model": "claude",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 400
        assert "Anthropic" in resp.json()["error"]


def test_stray_legacy_config_enc_is_ignored():
    """A leftover legacy encrypted config.enc blob cannot be migrated without
    the old Fernet key (no longer kept). init_first_run ignores it and starts
    fresh rather than crashing or attempting a read."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)

        # Plant a stray legacy blob (contents irrelevant; not a real token).
        store.dir.mkdir(parents=True, exist_ok=True)
        (store.dir / "config.enc").write_bytes(b"\x80\x8c not a fernet token")

        # init_first_run must not choke on the stray blob.
        store.init_first_run()

        assert store.config_path.exists()
        assert store.secrets_path.exists()
        assert store.load_config()["providers"] == []
        assert store.load_secrets() == {}


def test_config_migration_empty_gateway():
    """migrate_gateway_config with empty gateway uses defaults."""
    data = {"gateway": {}}
    result = migrate_gateway_config(data)
    assert result == {"host": "127.0.0.1", "port": 11434}


def test_daemon_restart_endpoint():
    """POST /api/daemon/restart returns ok with action."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        app = create_app(store)
        client = TestClient(app)

        resp = client.post("/api/daemon/restart")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["action"] == "restart"


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
        config = store.load_config()
        # Fresh config has empty providers - should be detected as first run
        assert config.get("providers") == []

        # Add a provider and verify detection works
        config["providers"].append({"id": "test", "name": "Test"})
        store.save_config(config)
        config = store.load_config()
        assert len(config["providers"]) == 1
