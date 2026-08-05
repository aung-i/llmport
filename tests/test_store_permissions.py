"""Tests for config file and directory permissions after save (Issue 3).

After save, the spec requires:
- config.yaml   -> permissions 0o600 (owner read/write only)
- secrets.yaml  -> permissions 0o600 (owner read/write only)
- directory     -> permissions 0o700 (owner read/write/execute only)
- Non-POSIX platforms where chmod is unsupported must not raise.
"""

import os
import stat
import tempfile
from unittest.mock import patch

from llmport.config.store import ConfigStore


def test_save_config_sets_config_yaml_to_600():
    """After save_config(), config.yaml has permissions 0o600."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.save_config({
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [],
            "models": [],
        })

        mode = stat.S_IMODE(os.stat(store.config_path).st_mode)
        assert mode == 0o600, (
            f"Expected config.yaml permissions 0o600, got {oct(mode)}"
        )


def test_save_secrets_sets_secrets_yaml_to_600():
    """After save_secrets(), secrets.yaml has permissions 0o600."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        store.save_secrets({"test": "sk-test"})

        mode = stat.S_IMODE(os.stat(store.secrets_path).st_mode)
        assert mode == 0o600, (
            f"Expected secrets.yaml permissions 0o600, got {oct(mode)}"
        )


def test_save_secrets_sets_directory_to_700():
    """After save_secrets(), the config directory has permissions 0o700."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()

        # Deliberately loosen directory permissions so we can verify save_secrets
        # restores them.
        os.chmod(store.dir, 0o755)
        store.save_secrets({"test": "sk-test"})

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


def test_non_posix_platform_does_not_raise():
    """If the underlying platform does not support chmod (OSError),
    save_config()/save_secrets() must catch the error and not propagate it."""
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

        with patch("os.chmod", side_effect=OSError("not supported on this platform")):
            # Must not raise.
            store.save_config(config)
            store.save_secrets({"test": "sk-test"})

        # Data must still be readable and intact.
        loaded = store.load_config()
        assert loaded["version"] == 1
        assert len(loaded["providers"]) == 1
        assert store.load_secrets() == {"test": "sk-test"}
