"""Tests for config store."""

import tempfile
from pathlib import Path

from llmgate.config.store import ConfigStore


def test_init_first_run_creates_key_and_config():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        assert Path(tmp, "key").exists()
        assert Path(tmp, "config.enc").exists()
        data = store.load()
        assert data["version"] == 1
        assert data["gateway"]["openai_port"] == 11434


def test_save_and_load_preserves_data():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        data["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-secret",
            "models": [],
        })
        store.save(data)
        loaded = store.load()
        assert len(loaded["providers"]) == 1
        assert loaded["providers"][0]["api_key"] == "sk-secret"


def test_api_key_is_encrypted_at_rest():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        data["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-secret",
            "models": [],
        })
        store.save(data)
        raw = Path(tmp, "config.enc").read_bytes()
        assert b"sk-secret" not in raw
