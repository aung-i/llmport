"""Encrypted YAML config store for ~/.config/llmport/."""

import os
from pathlib import Path

import yaml

from llmport.config.crypto import generate_key, encrypt, decrypt


class ConfigStore:
    def __init__(self, config_dir: str | None = None):
        if config_dir:
            self.dir = Path(config_dir)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            self.dir = Path(xdg) / "llmport"
        self.key_path = self.dir / "key"
        self.config_path = self.dir / "config.enc"

    def init_first_run(self) -> None:
        """Create config directory, generate key, write empty config."""
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.key_path.exists():
            key = generate_key()
            self.key_path.write_bytes(key)
            self.key_path.chmod(0o600)
        default = {
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [],
            "active_model": None,
        }
        self.save(default)

    def _read_key(self) -> bytes:
        return self.key_path.read_bytes()

    def load(self) -> dict:
        """Load and decrypt the config file."""
        key = self._read_key()
        ciphertext = self.config_path.read_bytes()
        plaintext = decrypt(key, ciphertext)
        return yaml.safe_load(plaintext)

    def save(self, data: dict) -> None:
        """Encrypt and write the config file."""
        key = self._read_key()
        plaintext = yaml.dump(data, default_flow_style=False, allow_unicode=True)
        ciphertext = encrypt(key, plaintext)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_bytes(ciphertext)
        # Secure file and directory permissions (POSIX only)
        try:
            self.config_path.chmod(0o600)
        except (OSError, AttributeError):
            pass  # non-POSIX platform
        try:
            self.dir.chmod(0o700)
        except (OSError, AttributeError):
            pass  # non-POSIX platform
