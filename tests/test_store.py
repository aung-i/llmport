"""Tests for config store."""

import tempfile
from pathlib import Path

import pytest

from llmport.config.store import ConfigStore


def test_init_first_run_creates_config_and_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        assert Path(tmp, "config.yaml").exists()
        assert Path(tmp, "secrets.yaml").exists()
        # No Fernet key or legacy blob is created.
        assert not Path(tmp, "key").exists()
        assert not Path(tmp, "config.enc").exists()

        data = store.load_config()
        assert data["version"] == 1
        assert data["gateway"]["host"] == "127.0.0.1"
        assert data["gateway"]["port"] == 11434
        assert data["providers"] == []
        assert data["models"] == []


def test_load_config_empty_file_returns_empty_dict():
    """An empty config.yaml parses to {} (not None or a crash)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("")
        assert store.load_config() == {}


def test_load_config_rejects_non_dict_top_level():
    """A valid-YAML but non-mapping config raises ValueError, not a silent
    bad return that downstream callers would crash on."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("- id: anthropic\n  base_url: x\n")  # list
        with pytest.raises(ValueError):
            store.load_config()


def test_save_and_load_preserves_providers_and_models():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        config = store.load_config()
        config["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
        })
        config["models"].append({
            "name": "my-model",
            "provider": "test",
            "upstream": "gpt-4",
        })
        store.save_config(config)

        loaded = store.load_config()
        assert len(loaded["providers"]) == 1
        assert loaded["providers"][0] == {
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
        }
        assert len(loaded["models"]) == 1
        assert loaded["models"][0]["name"] == "my-model"
        assert loaded["models"][0]["provider"] == "test"
        assert loaded["models"][0]["upstream"] == "gpt-4"
        # No api_key leaks into the readable config.
        assert "api_key" not in loaded["providers"][0]


def test_api_key_stored_separately_from_config():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        config = store.load_config()
        config["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
        })
        # Provider config (no key) -> config.yaml; key -> plaintext secrets.yaml.
        store.save_config(config)
        store.save_secrets({"test": "sk-secret"})

        # The key must NOT appear in the readable config.yaml (name or value).
        config_text = Path(tmp, "config.yaml").read_text(encoding="utf-8")
        assert "sk-secret" not in config_text
        assert "api_key" not in config_text

        # The key lives as plaintext in secrets.yaml (no encryption layer).
        secrets_text = Path(tmp, "secrets.yaml").read_text(encoding="utf-8")
        assert "sk-secret" in secrets_text

        # Round-trip: load_secrets returns the key.
        assert store.load_secrets() == {"test": "sk-secret"}


def test_stray_legacy_config_enc_is_ignored():
    """A leftover legacy encrypted config.enc blob cannot be read without the
    old Fernet key, which we no longer keep. init_first_run must ignore it
    and create a fresh config rather than crash or migrate."""
    with tempfile.TemporaryDirectory() as tmp:
        # Plant a stray legacy blob + orphan key (contents are irrelevant).
        Path(tmp, "config.enc").write_bytes(b"\x80\x8c not a fernet token")
        Path(tmp, "key").write_bytes(b"orphan")

        store = ConfigStore(tmp)
        store.init_first_run()

        # Fresh config + secrets are created; the stray blob is left in place
        # (not silently deleted) and never consulted.
        assert Path(tmp, "config.yaml").exists()
        assert Path(tmp, "secrets.yaml").exists()
        assert Path(tmp, "config.enc").exists()

        assert store.load_config()["providers"] == []
        assert store.load_secrets() == {}
