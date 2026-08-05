"""Config store: readable ``config.yaml`` + encrypted ``secrets.enc`` vault.

Layout under ``~/.config/llmport/``::

    config.yaml   # gateway, providers (NO api keys), models - hand-editable
    secrets.enc   # Fernet-encrypted {provider_id: api_key}
    key           # Fernet key (0600)

API keys never touch the readable config file. The legacy single-blob
``config.enc`` is migrated to the split format on first run.
"""

import os
from pathlib import Path

import yaml

from llmport.config.crypto import generate_key, encrypt, decrypt

DEFAULT_GATEWAY = {"host": "127.0.0.1", "port": 11434}

# First-run config.yaml template. Commented examples guide the user; the real
# config stays empty so the gateway starts clean. Parsed as
# {version, gateway, providers: [], models: []} (comments are ignored).
_CONFIG_TEMPLATE = """\
# llmport 网关配置
# 改完重启生效: llmport restart
#
# 供应商 —— 连接信息。API key 不写这里,单独加密存在 secrets.enc。
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


def _normalize_gateway(data: dict) -> dict:
    """Return a canonical ``{"host", "port"}`` dict from a config dict.

    Migrates the legacy ``openai_port``/``anthropic_port`` fields if present.
    """
    gw = data.get("gateway") or {}
    if "host" not in gw:
        gw = {
            "host": "127.0.0.1",
            "port": gw.get("openai_port", gw.get("anthropic_port", 11434)),
        }
    return {"host": gw.get("host", "127.0.0.1"), "port": int(gw.get("port", 11434))}


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
    """Persists readable config and an encrypted secrets vault."""

    def __init__(self, config_dir: str | None = None):
        if config_dir:
            self.dir = Path(config_dir)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            self.dir = Path(xdg) / "llmport"
        self.key_path = self.dir / "key"
        self.config_path = self.dir / "config.yaml"
        self.secrets_path = self.dir / "secrets.enc"
        self.legacy_path = self.dir / "config.enc"

    # ------------------------------------------------------------------
    # First-run / migration
    # ------------------------------------------------------------------

    def init_first_run(self, config_template: bool = False) -> None:
        """Create the config directory, key, default config, and empty vault.

        If a legacy ``config.enc`` blob is present, it is migrated to the
        split format instead of creating a fresh config. When
        *config_template* is True and a fresh config is created, write the
        commented template (for ``llmport setup``) instead of the bare
        default used by the daemon start path.
        """
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Migrate legacy single-blob config before creating anything new.
        if self.legacy_path.exists() and self.key_path.exists():
            self.migrate_old_config()

        if not self.key_path.exists():
            self.key_path.write_bytes(generate_key())
            self.key_path.chmod(0o600)

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

    def needs_migration(self) -> bool:
        """True if a legacy ``config.enc`` exists and ``config.yaml`` does not."""
        return self.legacy_path.exists() and not self.config_path.exists()

    def migrate_old_config(self) -> None:
        """Migrate legacy encrypted ``config.enc`` -> ``config.yaml`` + ``secrets.enc``.

        Splits provider API keys into the encrypted vault and writes the rest
        as readable config. Removes the old blob. No-op if ``config.yaml``
        already exists (just cleans up a leftover blob).
        """
        if self.config_path.exists():
            if self.legacy_path.exists():
                self.legacy_path.unlink()
            return
        if not self.legacy_path.exists() or not self.key_path.exists():
            return

        key = self._read_key()
        plaintext = decrypt(key, self.legacy_path.read_bytes())
        data = yaml.safe_load(plaintext) or {}

        secrets: dict[str, str] = {}
        providers = []
        for p in data.get("providers", []):
            pid = p.get("id")
            if pid and p.get("api_key"):
                secrets[pid] = p["api_key"]
            providers.append({
                "id": pid,
                "name": p.get("name", pid),
                "protocol": p.get("protocol"),
                "base_url": p.get("base_url"),
            })

        # Legacy models used id/provider_id/model_name -> rename to
        # name/provider/upstream, dropping auto-alias-derived entries that
        # had no explicit binding.
        models = []
        for m in data.get("models", []):
            bs = m.get("bindings", [])
            if not bs:
                continue
            entry: dict = {"name": m.get("id") or m.get("name")}
            if len(bs) == 1:
                b0 = bs[0]
                entry["provider"] = b0.get("provider_id") or b0.get("provider")
                entry["upstream"] = b0.get("model_name") or b0.get("upstream")
                if b0.get("priority", 1) != 1:
                    entry["priority"] = b0["priority"]
            else:
                entry["bindings"] = [
                    {
                        "provider": b.get("provider_id") or b.get("provider"),
                        "upstream": b.get("model_name") or b.get("upstream"),
                        "priority": b.get("priority", 1),
                    }
                    for b in bs
                ]
            if m.get("routing_strategy") and m["routing_strategy"] != "priority_fallback":
                entry["routing_strategy"] = m["routing_strategy"]
            models.append(entry)

        config = {
            "version": 1,
            "gateway": _normalize_gateway(data),
            "providers": providers,
            "models": models,
        }
        self.save_config(config)
        self.save_secrets(secrets)
        self.legacy_path.unlink()

    # ------------------------------------------------------------------
    # Key
    # ------------------------------------------------------------------

    def _read_key(self) -> bytes:
        return self.key_path.read_bytes()

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
    # secrets.enc (encrypted vault)
    # ------------------------------------------------------------------

    def load_secrets(self) -> dict[str, str]:
        """Load and decrypt the ``{provider_id: api_key}`` vault."""
        if not self.secrets_path.exists():
            return {}
        key = self._read_key()
        plaintext = decrypt(key, self.secrets_path.read_bytes())
        data = yaml.safe_load(plaintext) or {}
        return {str(k): str(v) for k, v in data.items()}

    def save_secrets(self, secrets: dict[str, str]) -> None:
        """Encrypt and write the ``{provider_id: api_key}`` vault."""
        self.dir.mkdir(parents=True, exist_ok=True)
        key = self._read_key()
        plaintext = yaml.dump(
            secrets, default_flow_style=False, allow_unicode=True, sort_keys=True
        )
        _atomic_write_bytes(self.secrets_path, encrypt(key, plaintext))
        try:
            self.secrets_path.chmod(0o600)
        except (OSError, AttributeError):
            pass
        try:
            self.dir.chmod(0o700)
        except (OSError, AttributeError):
            pass
