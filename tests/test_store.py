"""Tests for config store."""

import tempfile
from pathlib import Path

import yaml

from llmport.config.crypto import encrypt, generate_key
from llmport.config.store import ConfigStore


def test_init_first_run_creates_key_config_and_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        assert Path(tmp, "key").exists()
        assert Path(tmp, "config.yaml").exists()
        assert Path(tmp, "secrets.enc").exists()
        # The legacy single-blob config.enc must NOT be created.
        assert not Path(tmp, "config.enc").exists()

        data = store.load_config()
        assert data["version"] == 1
        assert data["gateway"]["host"] == "127.0.0.1"
        assert data["gateway"]["port"] == 11434
        assert data["providers"] == []
        assert data["models"] == []


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


def test_api_key_is_encrypted_at_rest():
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
        # Provider config (no key) -> config.yaml; key -> encrypted vault.
        store.save_config(config)
        store.save_secrets({"test": "sk-secret"})

        # The key must NOT appear in the readable config.yaml (name or value).
        config_text = Path(tmp, "config.yaml").read_text(encoding="utf-8")
        assert "sk-secret" not in config_text
        assert "api_key" not in config_text

        # The key must NOT appear as plaintext in secrets.enc (it is encrypted).
        secrets_raw = Path(tmp, "secrets.enc").read_bytes()
        assert b"sk-secret" not in secrets_raw

        # Round-trip: load_secrets returns the key.
        assert store.load_secrets() == {"test": "sk-secret"}


def test_migrate_old_config_splits_legacy_blob():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)

        # Seed a legacy encrypted single-blob config.enc + key.
        key = generate_key()
        Path(tmp, "key").write_bytes(key)
        legacy = {
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [{
                "id": "oldprov",
                "name": "OldProv",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "sk-old",
                "models": [],
            }],
            "models": [{
                "id": "my-model",
                "bindings": [{
                    "provider_id": "oldprov",
                    "model_name": "gpt-4",
                    "priority": 1,
                }],
            }],
            "active_model": "my-model",
        }
        blob = encrypt(key, yaml.dump(legacy, default_flow_style=False))
        Path(tmp, "config.enc").write_bytes(blob)

        # init_first_run detects the legacy blob + key and migrates it.
        store.init_first_run()

        # config.yaml exists, legacy config.enc is gone.
        assert Path(tmp, "config.yaml").exists()
        assert not Path(tmp, "config.enc").exists()

        config = store.load_config()
        config_text = Path(tmp, "config.yaml").read_text(encoding="utf-8")

        # No api_key / active_model / secret value in the readable config.
        assert "api_key" not in config_text
        assert "active_model" not in config_text
        assert "sk-old" not in config_text
        assert "api_key" not in config["providers"][0]
        assert "active_model" not in config

        # Provider migrated to {id, name, protocol, base_url} (no api_key).
        assert config["providers"][0] == {
            "id": "oldprov",
            "name": "OldProv",
            "protocol": "openai",
            "base_url": "https://api.example.com",
        }

        # Model migrated to {name, provider, upstream} shape.
        assert config["models"] == [{
            "name": "my-model",
            "provider": "oldprov",
            "upstream": "gpt-4",
        }]

        # API key lives in the encrypted vault, not as plaintext.
        assert store.load_secrets() == {"oldprov": "sk-old"}
        assert b"sk-old" not in Path(tmp, "secrets.enc").read_bytes()
