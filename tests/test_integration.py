"""End-to-end integration test for config + gateway flow."""

import tempfile

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app
from starlette.testclient import TestClient
from tests._helpers import TEST_API_KEY, AuthedClient


def test_full_flow():
    """Test: configure a provider, start server, send request."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.set_api_key(TEST_API_KEY)

        # Add a provider (api_key lives in providers.yaml alongside base_url)
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "name": "test-provider",
            "protocol": "openai",
            "base_url": "https://httpbin.org",
            "api_key": "sk-test",
        })
        store.save_providers_config(pdata)
        store.save_models_config({"models": {
            "test-model": {"test-provider": "test-model-real"}}})

        # Single app serves protocol routes + the /health probe.
        app = create_app(store)
        client = AuthedClient(app)

        # Liveness probe (read-only, no stats).
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Models endpoint via gateway
        resp = client.get("/openai/v1/models")
        assert resp.status_code == 200
        models = resp.json()
        assert len(models["data"]) >= 1
        assert any(m["id"] == "test-model" for m in models["data"])


def test_protocol_mismatch_translates():
    """An OpenAI request routed to an Anthropic provider is translated (not 400).

    Previously a protocol mismatch returned 400; now the gateway translates
    OpenAI<->Anthropic so a single provider serves both interfaces.
    """
    import json as _json
    from unittest.mock import patch
    from llmport.gateway.handler_base import UpstreamResult

    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.set_api_key(TEST_API_KEY)
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "name": "ant",
            "protocol": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-test",
        })
        store.save_providers_config(pdata)
        store.save_models_config({"models": {"claude": {"ant": "claude-real"}}})
        app = create_app(store)
        client = AuthedClient(app)

        captured = {}

        async def fake_forward(body, provider, model_name, path):
            captured["body"] = body
            captured["path"] = path
            anth = {"id": "msg_1", "type": "message", "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
            return UpstreamResult(200, _json.dumps(anth).encode(),
                                  "application/json", None)

        with patch("llmport.gateway.server.anthropic_handler.forward",
                   new=fake_forward):
            resp = client.post("/openai/v1/chat/completions", json={
                "model": "claude",
                "messages": [{"role": "user", "content": "hi"}],
            })
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "ok"
        # Upstream received Anthropic format (translated), not a 400.
        assert captured["path"] == "/v1/messages"
        assert captured["body"]["max_tokens"] == 1024


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

        assert store.providers_path.exists()
        assert store.config_path.exists()
        assert not (store.dir / "models.yaml").exists()
        assert store.load_providers_config()["providers"] == []


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
    from unittest.mock import patch
    from llmport.daemon import DaemonManager

    dm = DaemonManager(str(tmp_path))
    assert dm.is_running() is False

    # Write a fake PID file with current PID; the pid is alive, and we pretend
    # its command line is our daemon (is_running now identity-checks the cmdline
    # so a recycled pid is never mistaken for our daemon).
    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text(json.dumps({"pid": os.getpid(), "control_port": 12345}))
    with patch.object(dm, "_process_cmdline",
                      return_value="/p/python -m llmport --daemon"):
        assert dm.is_running() is True

    # Stale PID file (nonexistent process) -> not running, file cleared.
    pid_path.write_text(json.dumps({"pid": 99999, "control_port": 12345}))
    assert dm.is_running() is False


def test_first_run_detection_with_empty_providers():
    """When providers list is empty, first-run check should trigger."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.set_api_key(TEST_API_KEY)
        pdata = store.load_providers_config()
        # Fresh config has empty providers - should be detected as first run
        assert pdata.get("providers") == []

        # Add a provider and verify detection works
        pdata["providers"].append({"name": "test"})
        store.save_providers_config(pdata)
        pdata = store.load_providers_config()
        assert len(pdata["providers"]) == 1
