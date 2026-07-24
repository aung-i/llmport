"""Tests for config file and directory permissions after save (Issue 3).

After save(), the spec requires:
- config.enc  -> permissions 0o600 (owner read/write only)
- directory   -> permissions 0o700 (owner read/write/execute only)
- Non-POSIX platforms where chmod is unsupported must not raise.
"""

import os
import stat
import tempfile
from unittest.mock import patch

from llmport.config.store import ConfigStore


def test_save_sets_config_enc_to_600():
    """After save(), config.enc has permissions 0o600."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        data["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-test",
            "models": [],
        })
        store.save(data)

        mode = stat.S_IMODE(os.stat(store.config_path).st_mode)
        assert mode == 0o600, (
            f"Expected config.enc permissions 0o600, got {oct(mode)}"
        )


def test_save_sets_directory_to_700():
    """After save(), the config directory has permissions 0o700."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()

        # Deliberately loosen directory permissions so we can verify save() fixes them
        os.chmod(store.dir, 0o755)
        data["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-test",
            "models": [],
        })
        store.save(data)

        mode = stat.S_IMODE(os.stat(store.dir).st_mode)
        assert mode == 0o700, (
            f"Expected directory permissions 0o700, got {oct(mode)}"
        )


def test_non_posix_platform_does_not_raise():
    """If the underlying platform does not support chmod (OSError),
    save() must catch the error and not propagate it."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ConfigStore(tmp)
        store.init_first_run()
        data = store.load()
        data["providers"].append({
            "id": "test",
            "name": "Test",
            "protocol": "openai",
            "base_url": "https://api.example.com",
            "api_key": "sk-test",
            "models": [],
        })

        with patch("os.chmod", side_effect=OSError("not supported on this platform")):
            # Must not raise
            store.save(data)

        # Data must still be readable and intact
        loaded = store.load()
        assert loaded["version"] == 1
        assert len(loaded["providers"]) == 1
