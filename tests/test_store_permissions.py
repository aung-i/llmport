"""Tests for config file and directory permissions after save.

After save, the spec requires:
- providers.yaml  -> permissions 0o600 (owner read/write only; holds api keys)
- config.yaml     -> permissions 0o600 (owner read/write only; holds llmport api_key)
- directory       -> permissions 0o700 (owner read/write/execute only)
- Non-POSIX platforms where chmod is unsupported must not raise.
"""

import os
import stat
import tempfile
from unittest.mock import patch

from llmport.config.store import ConfigStore


def test_save_providers_config_sets_providers_yaml_to_600():
    """After save_providers_config(), providers.yaml has permissions 0o600."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.save_providers_config({"providers": []})

        mode = stat.S_IMODE(os.stat(store.providers_path).st_mode)
        assert mode == 0o600, (
            f"Expected providers.yaml permissions 0o600, got {oct(mode)}"
        )


def test_save_models_config_sets_config_yaml_to_600():
    """After save_models_config(), config.yaml has permissions 0o600.

    config.yaml holds the llmport api_key (a credential), so it is locked to
    0600 like providers.yaml -- not 0644.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.save_models_config({"models": {}})

        mode = stat.S_IMODE(os.stat(store.config_path).st_mode)
        assert mode == 0o600, (
            f"Expected config.yaml permissions 0o600, got {oct(mode)}"
        )


def test_save_providers_config_sets_directory_to_700():
    """After save_providers_config(), the config directory has permissions 0o700."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()

        # Deliberately loosen directory permissions so we can verify the save
        # restores them.
        os.chmod(store.dir, 0o755)
        store.save_providers_config({"providers": []})

        mode = stat.S_IMODE(os.stat(store.dir).st_mode)
        assert mode == 0o700, (
            f"Expected directory permissions 0o700, got {oct(mode)}"
        )


def test_init_first_run_creates_directory_with_700():
    """init_first_run() creates the config directory with permissions 0o700."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()

        mode = stat.S_IMODE(os.stat(store.dir).st_mode)
        assert mode == 0o700, (
            f"Expected directory permissions 0o700, got {oct(mode)}"
        )


def test_set_api_key_locks_config_yaml_to_600():
    """Writing the llmport api_key credential locks config.yaml to 0600.

    config.yaml holds the api_key, so the credential-write path must not leave
    it group/world-readable (0644) even if it was loosened beforehand.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        # Loosen the file first to prove set_api_key tightens it.
        store.save_models_config({"models": {}})
        os.chmod(store.config_path, 0o644)

        store.set_api_key("sk-llmport-secret")

        mode = stat.S_IMODE(os.stat(store.config_path).st_mode)
        assert mode == 0o600, (
            f"Expected config.yaml permissions 0o600, got {oct(mode)}"
        )


def test_non_posix_platform_does_not_raise():
    """If the underlying platform does not support chmod (OSError),
    save_providers_config()/save_models_config() must catch the error and not
    propagate it."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        pdata = store.load_providers_config()
        pdata["providers"].append({
            "name": "test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-test",
        })

        with patch("os.chmod", side_effect=OSError("not supported on this platform")):
            # Must not raise.
            store.save_providers_config(pdata)
            store.save_models_config({"models": {}})

        # Data must still be readable and intact.
        loaded = store.load_providers_config()
        assert len(loaded["providers"]) == 1
        assert loaded["providers"][0]["api_key"] == "sk-test"
