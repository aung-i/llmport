"""Tests for config store (config.yaml + providers.yaml, secret-split layout)."""

import tempfile
from pathlib import Path

import pytest

from llmport.config.store import ConfigStore


def test_init_first_run_creates_both_files():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        assert Path(tmp, "config.yaml").exists()
        assert Path(tmp, "providers.yaml").exists()
        # No separate models.yaml (folded into config.yaml); no legacy files.
        assert not Path(tmp, "models.yaml").exists()
        assert not Path(tmp, "secrets.yaml").exists()
        assert not Path(tmp, "key").exists()
        assert not Path(tmp, "config.enc").exists()

        pdata = store.load_providers_config()
        assert pdata == {"providers": []}

        cfg = store.load_config()
        assert cfg["version"] == 1
        assert cfg["gateway"]["host"] == "127.0.0.1"
        assert cfg["gateway"]["port"] == 11434
        assert cfg["models"] == {}

        # models convenience reads from config.yaml.
        assert store.load_models_config() == {"models": {}}


def test_init_first_run_backs_up_ancient_single_file_config():
    """An ancient single-file config.yaml (has a providers key) is backed up,
    not misread as the new non-secret config."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "config.yaml").write_text(
            "version: 1\ngateway: {host: 127.0.0.1, port: 8000}\nproviders: []\n")
        Path(tmp, "secrets.yaml").write_text("anthropic: sk-old\n")
        store = ConfigStore(tmp)
        store.init_first_run()
        # secrets.yaml deleted; ancient config.yaml backed up, fresh one created.
        assert not Path(tmp, "secrets.yaml").exists()
        assert Path(tmp, "config.yaml.bak").exists()
        assert Path(tmp, "config.yaml").exists()
        assert Path(tmp, "providers.yaml").exists()
        # The fresh config.yaml is the default (not the ancient 8000 port).
        assert store.load_gateway()["port"] == 11434


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
        store.providers_path.write_text("- name: anthropic\n  base_url: x\n")  # list
        with pytest.raises(ValueError):
            store.load_providers_config()


def test_load_models_config_rejects_non_dict_config():
    """A valid-YAML but non-mapping config.yaml raises ValueError via the
    models convenience accessor."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("- name: x\n")  # list
        with pytest.raises(ValueError):
            store.load_models_config()


def test_save_and_load_preserves_providers_with_key():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "name": "test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-secret",
        })
        store.save_providers_config(pdata)

        loaded = store.load_providers_config()
        assert len(loaded["providers"]) == 1
        p = loaded["providers"][0]
        assert p["name"] == "test"
        assert p["base_url"] == "https://api.example.com"
        assert p["api_key"] == "sk-secret"


def test_api_key_lives_in_providers_yaml_not_config():
    """The key lives only in providers.yaml; config.yaml (gateway + models)
    has none."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "name": "test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-secret",
        })
        store.save_providers_config(pdata)
        store.save_models_config({"models": {"my-model": {"test": "gpt-4"}}})

        providers_text = Path(tmp, "providers.yaml").read_text(encoding="utf-8")
        config_text = Path(tmp, "config.yaml").read_text(encoding="utf-8")
        assert "sk-secret" in providers_text
        assert "sk-secret" not in config_text
        assert "api_key" not in config_text
        assert not Path(tmp, "models.yaml").exists()


def test_models_config_preserved_when_saving_models():
    """save_models_config preserves the existing gateway/version in config.yaml;
    only the models section is replaced."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        # Set a custom gateway.
        cfg = store.load_config()
        cfg["gateway"] = {"host": "127.0.0.1", "port": 9999}
        store.save_config(cfg)

        store.save_models_config({"models": {"gpt-4o": "openai"}})
        cfg2 = store.load_config()
        assert cfg2["gateway"]["port"] == 9999  # preserved
        assert cfg2["models"] == {"gpt-4o": "openai"}
        # providers.yaml untouched (still empty).
        assert store.load_providers_config()["providers"] == []


def test_load_gateway_returns_default_when_missing():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        # No config.yaml yet.
        assert store.load_gateway() == {"host": "127.0.0.1", "port": 11434}


def test_load_gateway_reads_from_config_file():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        cfg = store.load_config()
        cfg["gateway"] = {"host": "127.0.0.1", "port": 9999}
        store.save_config(cfg)
        assert store.load_gateway() == {"host": "127.0.0.1", "port": 9999}


def test_load_gateway_tolerates_corrupt_config_file():
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("- not a dict\n")
        assert store.load_gateway() == {"host": "127.0.0.1", "port": 11434}


def test_stray_legacy_config_enc_is_ignored():
    """A leftover legacy encrypted config.enc blob cannot be read; init_first_run
    ignores it and creates fresh files rather than crash or migrate."""
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "config.enc").write_bytes(b"\x80\x8c not a fernet token")
        Path(tmp, "key").write_bytes(b"orphan")

        store = ConfigStore(tmp)
        store.init_first_run()

        assert Path(tmp, "config.yaml").exists()
        assert Path(tmp, "providers.yaml").exists()
        assert not Path(tmp, "models.yaml").exists()
        assert Path(tmp, "config.enc").exists()  # left in place, not deleted

        assert store.load_providers_config()["providers"] == []
        assert store.load_models_config() == {"models": {}}


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


def test_write_config_template_contains_examples():
    """The first-run config.yaml template shows model mappings and parses to
    an empty models dict (comments ignored)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.write_config_template()
        text = store.config_path.read_text(encoding="utf-8")
        assert "models: {}" in text
        assert "gateway" in text
        assert store.load_models_config() == {"models": {}}


# ── migration from the prior two-file layout ─────────────────────────────


def test_migrate_old_two_file_layout():
    """providers.yaml (with gateway) + models.yaml -> config.yaml + providers.yaml."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        # Old providers.yaml carried gateway + version + providers.
        store.providers_path.write_text(
            "version: 1\n"
            "gateway: {host: 127.0.0.1, port: 22000}\n"
            "providers:\n"
            "  - name: anthropic\n"
            "    protocol: anthropic\n"
            "    base_url: https://api.anthropic.com\n"
            "    api_key: sk-ant\n")
        # Old models.yaml.
        Path(tmp, "models.yaml").write_text("models: {claude: anthropic}\n")

        store.init_first_run()

        # gateway + models lifted into config.yaml.
        cfg = store.load_config()
        assert cfg["gateway"]["port"] == 22000
        assert cfg["models"] == {"claude": "anthropic"}
        # providers.yaml stripped to {providers} only (no gateway/version).
        pdata = store.load_providers_config()
        assert "gateway" not in pdata
        assert "version" not in pdata
        assert len(pdata["providers"]) == 1
        assert pdata["providers"][0]["api_key"] == "sk-ant"
        # models.yaml deleted.
        assert not Path(tmp, "models.yaml").exists()


def test_migrate_is_idempotent():
    """Running init_first_run again after migration is a no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text(
            "version: 1\ngateway: {host: 127.0.0.1, port: 22000}\nproviders: []\n")
        Path(tmp, "models.yaml").write_text("models: {claude: anthropic}\n")

        store.init_first_run()
        cfg = store.load_config()
        assert cfg["gateway"]["port"] == 22000
        assert cfg["models"] == {"claude": "anthropic"}

        # Second run must not clobber or duplicate.
        store.init_first_run()
        cfg2 = store.load_config()
        assert cfg2 == cfg
        assert not Path(tmp, "models.yaml").exists()


def test_migrate_providers_without_gateway():
    """Old providers.yaml with providers but no gateway -> config gets the
    default gateway; providers are preserved."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text(
            "providers:\n"
            "  - name: openai\n"
            "    protocol: openai\n"
            "    base_url: https://api.openai.com\n")
        store.init_first_run()

        cfg = store.load_config()
        assert cfg["gateway"] == {"host": "127.0.0.1", "port": 11434}
        assert cfg["models"] == {}
        pdata = store.load_providers_config()
        assert [p["name"] for p in pdata["providers"]] == ["openai"]


def test_migrate_corrupt_providers_yaml_preserves_models():
    """A corrupt old providers.yaml is skipped (not crashed on); models.yaml
    is still lifted into config.yaml."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text(": not valid yaml : ::\n")
        Path(tmp, "models.yaml").write_text("models: {claude: anthropic}\n")
        store.init_first_run()

        cfg = store.load_config()
        assert cfg["models"] == {"claude": "anthropic"}
        assert cfg["gateway"] == {"host": "127.0.0.1", "port": 11434}


def test_load_config_empty_file_returns_empty_dict():
    """An empty config.yaml parses to {} (not None or a crash)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.dir.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("")
        assert store.load_config() == {}

