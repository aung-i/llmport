"""Tests for config store (two-file providers/models layout)."""

import tempfile
from pathlib import Path

import pytest

from llmport.config.store import ConfigStore


def test_init_first_run_creates_both_files():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        assert Path(tmp, "providers.yaml").exists()
        assert Path(tmp, "models.yaml").exists()
        # No legacy single-file layout or Fernet artifacts.
        assert not Path(tmp, "config.yaml").exists()
        assert not Path(tmp, "secrets.yaml").exists()
        assert not Path(tmp, "key").exists()
        assert not Path(tmp, "config.enc").exists()

        pdata = store.load_providers_config()
        assert pdata["version"] == 1
        assert pdata["gateway"]["host"] == "127.0.0.1"
        assert pdata["gateway"]["port"] == 11434
        assert pdata["providers"] == []

        mdata = store.load_models_config()
        assert mdata == {"models": []}


def test_init_first_run_deletes_legacy_files():
    """init_first_run removes old config.yaml/secrets.yaml from the prior layout."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "config.yaml").write_text("version: 1\nproviders: []\n")
        Path(tmp, "secrets.yaml").write_text("anthropic: sk-old\n")
        store = ConfigStore(tmp)
        store.init_first_run()
        assert not Path(tmp, "config.yaml").exists()
        assert not Path(tmp, "secrets.yaml").exists()
        assert Path(tmp, "providers.yaml").exists()


def test_load_providers_config_empty_file_returns_empty_dict():
    """An empty providers.yaml parses to {} (not None or a crash)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text("")
        assert store.load_providers_config() == {}


def test_load_providers_config_rejects_non_dict_top_level():
    """A valid-YAML but non-mapping providers.yaml raises ValueError, not a
    silent bad return that downstream callers would crash on."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text("- id: anthropic\n  base_url: x\n")  # list
        with pytest.raises(ValueError):
            store.load_providers_config()


def test_load_models_config_rejects_non_dict_top_level():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.models_path.write_text("- name: x\n")  # list
        with pytest.raises(ValueError):
            store.load_models_config()


def test_save_and_load_preserves_providers_with_key():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-secret",
        })
        store.save_providers_config(pdata)

        loaded = store.load_providers_config()
        assert len(loaded["providers"]) == 1
        p = loaded["providers"][0]
        assert p["id"] == "test"
        assert p["base_url"] == "https://api.example.com"
        assert p["api_key"] == "sk-secret"


def test_api_key_lives_in_providers_yaml_not_models():
    """The key lives in providers.yaml (self-contained); models.yaml has none."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-secret",
        })
        store.save_providers_config(pdata)
        store.save_models_config({"models": [
            {"name": "my-model", "provider": "test", "upstream": "gpt-4"}]})

        providers_text = Path(tmp, "providers.yaml").read_text(encoding="utf-8")
        models_text = Path(tmp, "models.yaml").read_text(encoding="utf-8")
        assert "sk-secret" in providers_text
        assert "sk-secret" not in models_text
        assert "api_key" not in models_text


def test_models_config_independent_of_providers():
    """models.yaml can be loaded/saved without touching providers.yaml."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.save_models_config({"models": [
            {"name": "gpt-4o", "provider": "openai", "upstream": "gpt-4o"}]})
        assert len(store.load_models_config()["models"]) == 1
        # providers.yaml untouched (still empty).
        assert store.load_providers_config()["providers"] == []


def test_load_gateway_returns_default_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        # No providers.yaml yet.
        assert store.load_gateway() == {"host": "127.0.0.1", "port": 11434}


def test_load_gateway_reads_from_providers_file():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        pdata = store.load_providers_config()
        pdata["gateway"] = {"host": "127.0.0.1", "port": 9999}
        store.save_providers_config(pdata)
        assert store.load_gateway() == {"host": "127.0.0.1", "port": 9999}


def test_load_gateway_tolerates_corrupt_providers_file():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text("- not a dict\n")
        assert store.load_gateway() == {"host": "127.0.0.1", "port": 11434}


def test_stray_legacy_config_enc_is_ignored():
    """A leftover legacy encrypted config.enc blob cannot be read; init_first_run
    ignores it and creates fresh files rather than crash or migrate."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "config.enc").write_bytes(b"\x80\x8c not a fernet token")
        Path(tmp, "key").write_bytes(b"orphan")

        store = ConfigStore(tmp)
        store.init_first_run()

        assert Path(tmp, "providers.yaml").exists()
        assert Path(tmp, "models.yaml").exists()
        assert Path(tmp, "config.enc").exists()  # left in place, not deleted

        assert store.load_providers_config()["providers"] == []
        assert store.load_models_config() == {"models": []}


def test_write_providers_template_contains_examples():
    """The first-run template shows api_key inline with a provider and parses
    to an empty providers list (comments ignored)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.write_providers_template()
        text = store.providers_path.read_text(encoding="utf-8")
        assert "api_key" in text
        assert "providers: []" in text
        assert store.load_providers_config()["providers"] == []
