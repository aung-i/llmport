"""Config store: two role-split files under ``~/.config/llmport/``.

Layout::

    providers.yaml   # gateway + providers (WITH api_key), 0600
    models.yaml      # public-name -> provider model mappings, 0600

Providers are self-contained: ``base_url`` and ``api_key`` live together in
``providers.yaml`` so ``llmport provider test`` only needs that one file. The
providers file holds secrets (0600); ``models.yaml`` carries no keys.

Legacy single-file ``config.yaml`` + ``secrets.yaml`` from the old layout are
deleted on init -- the new code only reads the two files above.
"""

import os
from pathlib import Path

import yaml

DEFAULT_GATEWAY = {"host": "127.0.0.1", "port": 11434}

# First-run providers.yaml template. Commented examples guide the user; the
# real config stays empty so the gateway starts clean. Parsed as
# {version, gateway, providers: []} (comments are ignored).
_PROVIDERS_TEMPLATE = """\
# llmport 供应商配置 (含 API key, 0600, 勿提交/分享)
# 改完重启生效: llmport restart
#
# gateway: 网关监听地址。始终强制回环(0.0.0.0 等会被改为 127.0.0.1);
#   也可用 `llmport start --host/--port` 覆盖(优先级: CLI > 此文件 > 默认)。
#
# providers: 供应商连接信息 + API key,自包含。
#   name: 供应商标识(模型映射里用此名引用),如 anthropic
#   protocol: openai | anthropic
#   base_url 填主机根即可,/v1 由网关自动补。
#   api_key 明文存储;用 `llmport provider add` 交互输入(不回显)更省事。
#
# providers: []   # 下面是示例,去掉行首 # 启用
#   - name: anthropic
#     protocol: anthropic
#     base_url: https://api.anthropic.com
#     api_key: sk-ant-xxxxx
#   - name: openai
#     protocol: openai
#     base_url: https://api.openai.com
#     api_key: sk-xxxxx

version: 1
gateway:
  host: 127.0.0.1
  port: 11434
providers: []
"""

# First-run models.yaml template.
_MODELS_TEMPLATE = """\
# llmport 模型映射 (公开名 -> 供应商)
# 改完重启生效: llmport restart
#
# key 是客户端请求时填的 model 名,映射到供应商。
# upstream 缺省 = 公开名;多供应商/多 upstream 按顺序 fallback。
#
#   claude-sonnet: anthropic                 # 无别名单供应商
#   gpt-4o:                                   # 无别名多供应商(顺序=优先级)
#     - openai
#     - azure
#   sonnet:                                   # 有别名,供应商后接单个模型名
#     - anthropic: claude-sonnet-4
#   gpt4:                                     # 供应商后接列表(依次 fallback)
#     - openai: gpt-4
#     - azure: [gpt4o-deploy, gpt4o-turbo]

models: {}
"""

# Files from the old single-file layout; deleted on init since the new code
# never reads them.
_LEGACY_FILES = ("config.yaml", "secrets.yaml")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes atomically: temp file in the same dir, then ``os.replace``.

    A crash mid-write leaves the temp file, not a truncated ``path``, so the
    existing config survives. ``os.replace`` is atomic on POSIX when source
    and dest share a filesystem (they do: same dir). Text callers encode to
    UTF-8 bytes first.
    """
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _chmod(path: Path, mode: int) -> None:
    """chmod, swallowing errors on non-POSIX platforms (no chmod support)."""
    try:
        path.chmod(mode)
    except (OSError, AttributeError):
        pass


class ConfigStore:
    """Persists the role-split providers and models config files."""

    def __init__(self, config_dir: str | None = None):
        if config_dir:
            self.dir = Path(config_dir)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            self.dir = Path(xdg) / "llmport"
        self.providers_path = self.dir / "providers.yaml"
        self.models_path = self.dir / "models.yaml"

    # ------------------------------------------------------------------
    # First-run
    # ------------------------------------------------------------------

    def init_first_run(self, config_template: bool = False) -> None:
        """Create the config directory, default files, and clear legacy files.

        When *config_template* is True, write the commented templates (for
        ``llmport setup`` / ``config init``) instead of the bare defaults used
        by the daemon start path. Legacy ``config.yaml`` / ``secrets.yaml``
        from the old single-file layout are deleted -- the new code only reads
        ``providers.yaml`` / ``models.yaml``.
        """
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        if not self.providers_path.exists():
            if config_template:
                self.write_providers_template()
            else:
                self.save_providers_config({
                    "version": 1,
                    "gateway": dict(DEFAULT_GATEWAY),
                    "providers": [],
                })
        if not self.models_path.exists():
            if config_template:
                self.write_models_template()
            else:
                self.save_models_config({"models": {}})

        self._cleanup_legacy_files()

    def _cleanup_legacy_files(self) -> None:
        """Delete legacy ``config.yaml`` / ``secrets.yaml`` from the old layout."""
        for name in _LEGACY_FILES:
            p = self.dir / name
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # providers.yaml (gateway + providers, WITH api_key)
    # ------------------------------------------------------------------

    def load_providers_config(self) -> dict:
        """Load and return the providers config (gateway + providers + keys).

        Raises ``FileNotFoundError`` if the file does not exist yet, and
        ``ValueError`` if it is valid YAML but not a mapping (e.g. a top-level
        list from a copy-paste mistake), so callers can abort cleanly instead
        of crashing on ``.get()`` or overwriting the user's data on the next
        write.
        """
        with self.providers_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(
                f"providers.yaml 顶层必须是字典，得到 {type(data).__name__}"
            )
        return data

    def save_providers_config(self, data: dict) -> None:
        """Write the providers config (gateway + providers + keys, 0600).

        Validates provider base_urls against the SSRF blocklist (see
        :mod:`llmport.config.validation`) so every write path -- CLI and any
        future config editor -- is guarded from one place.
        """
        from llmport.config.validation import validate_providers_config
        validate_providers_config(data)
        self.dir.mkdir(parents=True, exist_ok=True)
        plaintext = yaml.dump(
            data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        _atomic_write_bytes(self.providers_path, plaintext.encode("utf-8"))
        _chmod(self.providers_path, 0o600)
        _chmod(self.dir, 0o700)

    def write_providers_template(self) -> None:
        """Write the first-run providers.yaml with commented examples."""
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(
            self.providers_path, _PROVIDERS_TEMPLATE.encode("utf-8"))
        _chmod(self.providers_path, 0o600)

    # ------------------------------------------------------------------
    # models.yaml (public-name -> provider model mappings)
    # ------------------------------------------------------------------

    def load_models_config(self) -> dict:
        """Load and return the models config (``{models: {...}}``).

        Returns ``{}`` if the file does not exist yet (models are optional at
        first run). Raises ``ValueError`` if it is valid YAML but not a
        mapping.
        """
        if not self.models_path.exists():
            return {}
        data = yaml.safe_load(self.models_path.read_bytes()) or {}
        if not isinstance(data, dict):
            raise ValueError(
                f"models.yaml 顶层必须是字典，得到 {type(data).__name__}"
            )
        return data

    def save_models_config(self, data: dict) -> None:
        """Write the models config (0600)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        plaintext = yaml.dump(
            data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        _atomic_write_bytes(self.models_path, plaintext.encode("utf-8"))
        _chmod(self.models_path, 0o600)
        _chmod(self.dir, 0o700)

    def write_models_template(self) -> None:
        """Write the first-run models.yaml with commented examples."""
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(self.models_path, _MODELS_TEMPLATE.encode("utf-8"))
        _chmod(self.models_path, 0o600)

    # ------------------------------------------------------------------
    # gateway convenience
    # ------------------------------------------------------------------

    def load_gateway(self) -> dict:
        """Return the canonical ``{"host", "port"}`` gateway dict.

        Read from ``providers.yaml``'s ``gateway`` section; falls back to the
        default (``127.0.0.1:11434``) if the file is missing or unreadable so
        gateway resolution never crashes the daemon.
        """
        try:
            data = self.load_providers_config()
        except (FileNotFoundError, ValueError):
            return dict(DEFAULT_GATEWAY)
        gw = data.get("gateway") or {}
        return {
            "host": gw.get("host", DEFAULT_GATEWAY["host"]),
            "port": int(gw.get("port", DEFAULT_GATEWAY["port"])),
        }
