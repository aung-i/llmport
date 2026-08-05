"""Config store: readable ``config.yaml`` + plaintext ``secrets.yaml`` vault.

Layout under ``~/.config/llmport/``::

    config.yaml    # gateway, providers (NO api keys), models - hand-editable
    secrets.yaml   # plaintext {provider_id: api_key} (0600)

API keys never touch the readable config file, so it stays safe to diff or
share. The secrets file is plaintext (0600) -- no encryption layer to manage.
"""

import os
from pathlib import Path

import yaml

DEFAULT_GATEWAY = {"host": "127.0.0.1", "port": 11434}

# First-run config.yaml template. Commented examples guide the user; the real
# config stays empty so the gateway starts clean. Parsed as
# {version, gateway, providers: [], models: []} (comments are ignored).
_CONFIG_TEMPLATE = """\
# llmport 网关配置
# 改完重启生效: llmport restart
#
# 供应商 —— 连接信息。API key 不写这里,单独明文存在 secrets.yaml (0600)。
#   protocol: openai | anthropic
#
# providers: []   # 下面是示例,去掉行首的 # 启用
#   - id: anthropic
#     name: Anthropic
#     protocol: anthropic
#     base_url: https://api.anthropic.com
#   - id: openai
#     name: OpenAI
#     protocol: openai
#     base_url: https://api.openai.com   # 主机根,/v1 由网关自动补
#
# 模型 —— 客户端请求时填的公开名,映射到供应商的真实模型名。
# 简写(单供应商):
#   - name: claude-sonnet
#     provider: anthropic
#     upstream: claude-sonnet-4
# 完整形式(多供应商 fallback,priority 小的优先):
#   - name: gpt-4o
#     bindings:
#       - {provider: openai, upstream: gpt-4o, priority: 1}
#       - {provider: azure, upstream: gpt4o-deploy, priority: 2}

version: 1
gateway:
  host: 127.0.0.1
  port: 11434
providers: []
models: []
"""


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically: temp file in the same dir, then ``os.replace``.

    A crash mid-write leaves the temp file, not a truncated ``path``, so the
    existing config/secrets survive. ``os.replace`` is atomic on POSIX when
    source and dest share a filesystem (they do: same dir). Text callers
    encode to UTF-8 bytes first.
    """
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class ConfigStore:
    """Persists readable config and a plaintext secrets vault."""

    def __init__(self, config_dir: str | None = None):
        if config_dir:
            self.dir = Path(config_dir)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            self.dir = Path(xdg) / "llmport"
        self.config_path = self.dir / "config.yaml"
        self.secrets_path = self.dir / "secrets.yaml"

    # ------------------------------------------------------------------
    # First-run
    # ------------------------------------------------------------------

    def init_first_run(self, config_template: bool = False) -> None:
        """Create the config directory, default config, and empty secrets file.

        When *config_template* is True, write the commented template (for
        ``llmport setup``) instead of the bare default used by the daemon
        start path. A stray legacy ``config.enc`` blob, if present, is
        ignored -- it cannot be read without the old Fernet key.
        """
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        if not self.config_path.exists():
            if config_template:
                self.write_config_template()
            else:
                self.save_config({
                    "version": 1,
                    "gateway": dict(DEFAULT_GATEWAY),
                    "providers": [],
                    "models": [],
                })

        if not self.secrets_path.exists():
            self.save_secrets({})

    # ------------------------------------------------------------------
    # config.yaml (readable, no secrets)
    # ------------------------------------------------------------------

    def load_config(self) -> dict:
        """Load and return the readable config (no API keys).

        Raises ``ValueError`` if the file is valid YAML but not a mapping
        (e.g. a top-level list from a copy-paste mistake), so callers can
        abort cleanly instead of crashing on ``.get()`` or overwriting the
        user's data on the next write.
        """
        with self.config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(
                f"config.yaml 顶层必须是字典，得到 {type(data).__name__}"
            )
        return data

    def save_config(self, data: dict) -> None:
        """Write the readable config. Must NOT contain API keys.

        Validates provider base_urls against the SSRF blocklist (see
        :mod:`llmport.config.validation`) so every write path -- CLI and any
        future config editor -- is guarded from one place.
        """
        from llmport.config.validation import validate_config
        validate_config(data)
        self.dir.mkdir(parents=True, exist_ok=True)
        plaintext = yaml.dump(
            data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        _atomic_write_bytes(self.config_path, plaintext.encode("utf-8"))
        try:
            self.config_path.chmod(0o600)
        except (OSError, AttributeError):
            pass

    def write_config_template(self) -> None:
        """Write the first-run config.yaml with commented examples.

        Used by ``llmport setup`` so the user gets a reference template to
        fill in (or hand-edit). Not used by the daemon start path.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(self.config_path, _CONFIG_TEMPLATE.encode("utf-8"))
        try:
            self.config_path.chmod(0o600)
        except (OSError, AttributeError):
            pass

    # ------------------------------------------------------------------
    # secrets.yaml (plaintext vault, 0600)
    # ------------------------------------------------------------------

    def load_secrets(self) -> dict[str, str]:
        """Load the plaintext ``{provider_id: api_key}`` vault."""
        if not self.secrets_path.exists():
            return {}
        data = yaml.safe_load(self.secrets_path.read_bytes()) or {}
        return {str(k): str(v) for k, v in data.items()}

    def save_secrets(self, secrets: dict[str, str]) -> None:
        """Write the plaintext ``{provider_id: api_key}`` vault (0600)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        plaintext = yaml.dump(
            secrets, default_flow_style=False, allow_unicode=True, sort_keys=True
        )
        _atomic_write_bytes(self.secrets_path, plaintext.encode("utf-8"))
        try:
            self.secrets_path.chmod(0o600)
        except (OSError, AttributeError):
            pass
        try:
            self.dir.chmod(0o700)
        except (OSError, AttributeError):
            pass
