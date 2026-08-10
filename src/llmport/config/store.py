"""Config store: two files under ``~/.config/llmport/``, split by secrecy.

Layout::

    config.yaml       # gateway + models + llmport api_key, 0600
    providers.yaml    # upstream providers (WITH their api_key), 0600

Both files hold credentials and are locked to 0600 (owner read/write only):
``providers.yaml`` holds the upstream providers' keys, and ``config.yaml``
holds the gateway listen address, the public-name -> provider model mappings,
and llmport's own ``api_key`` (the credential a client presents to use the
gateway). The config dir itself is 0700. ``config show`` masks the ``api_key``
line so it isn't leaked to the terminal.

Providers are self-contained: ``base_url`` and ``api_key`` live together in
``providers.yaml``; ``llmport model test`` reads both files (models from
``config.yaml``, keys from ``providers.yaml``). llmport's own ``api_key``
authenticates *clients of* the gateway -- unrelated to the upstream providers'
keys, which is why it lives in ``config.yaml`` and has its own CLI
(``llmport api-key``).

Legacy ``secrets.yaml`` from the old vault layout is deleted on init. An
ancient single-file ``config.yaml`` (one containing a ``providers`` key) is
backed up to ``config.yaml.bak`` rather than misread as the new non-secret
config -- see :meth:`_migrate_layout`.
"""

import os
import secrets
from pathlib import Path

import yaml

DEFAULT_GATEWAY = {"host": "127.0.0.1", "port": 11434}

# Prefix for llmport's own API keys (the credential a client presents to use
# the gateway). Matches the ``sk-`` convention of OpenAI/Anthropic keys so it
# reads naturally in SDK config.
_API_KEY_PREFIX = "sk-llmport-"


def generate_api_key() -> str:
    """Generate a fresh, random llmport API key.

    ``secrets.token_urlsafe(32)`` yields ~43 chars of URL-safe entropy; the
    ``sk-llmport-`` prefix marks it as llmport's own key (not an upstream
    provider key). Used by ``llmport setup`` so a fresh install always has a
    key -- auth is mandatory, never optional.
    """
    return _API_KEY_PREFIX + secrets.token_urlsafe(32)


# First-run config.yaml template (gateway + models; api_key added by setup).
# Commented examples guide the user; the real config stays empty so the gateway
# starts clean. Parsed as {version, gateway, models: {}} (comments are ignored).
_CONFIG_TEMPLATE = """\
# llmport 配置 (网关地址 + 模型映射 + llmport api_key, 0600)
# 改完重启生效: llmport restart
#
# gateway: 网关监听地址。始终强制回环(0.0.0.0 等会被改为 127.0.0.1);
#   也可用 `llmport start --host/--port` 覆盖(优先级: CLI > 此文件 > 默认)。
#
# api_key: llmport 自己的 API key,客户端访问网关时必须出示(鉴权是强制的,
#   不存在不鉴权模式)。`llmport setup` 会自动生成一个;此处仅作示例。
#   `config show` 会打码;用 `llmport api-key show --reveal` 查看明文。
# api_key: sk-llmport-xxxxx
#
# models: 公开名 -> 供应商映射。key 是客户端请求时填的 model 名。
#   upstream 缺省 = 公开名;多供应商/多 upstream 按顺序 fallback。
#
# models: {}   # 下面是示例,去掉行首 # 启用
#   claude-sonnet: anthropic                 # 无别名单供应商
#   gpt-4o:                                   # 无别名多供应商(顺序=优先级)
#     - openai
#     - azure
#   sonnet:                                   # 有别名,供应商后接单个模型名
#     - anthropic: claude-sonnet-4
#   gpt4:                                     # 供应商后接列表(依次 fallback)
#     - openai: gpt-4
#     - azure: [gpt4o-deploy, gpt4o-turbo]

version: 1
gateway:
  host: 127.0.0.1
  port: 11434
models: {}
"""

# First-run providers.yaml template (secrets: upstream providers' api_keys).
_PROVIDERS_TEMPLATE = """\
# llmport 供应商配置 (含供应商 API key, 0600, 勿提交/分享)
# 改完重启生效: llmport restart
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

providers: []
"""

# Files from the old vault layout; deleted on init since the new code never
# reads them. (config.yaml is NOT here -- it is the real non-secret config.)
_LEGACY_FILES = ("secrets.yaml",)


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
    """Persists the secret-split config and providers files."""

    def __init__(self, config_dir: str | None = None):
        if config_dir:
            self.dir = Path(config_dir)
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            self.dir = Path(xdg) / "llmport"
        self.config_path = self.dir / "config.yaml"
        self.providers_path = self.dir / "providers.yaml"

    # ------------------------------------------------------------------
    # First-run
    # ------------------------------------------------------------------

    def init_first_run(self, config_template: bool = False) -> None:
        """Create the config directory, default files, and clear legacy files.

        When *config_template* is True, write the commented templates (for
        ``llmport setup`` / ``config init``) instead of the bare defaults used
        by the daemon start path. Migrates the prior two-file layout
        (``providers.yaml`` with gateway + ``models.yaml``) into the current
        ``config.yaml`` + ``providers.yaml`` split first, then fills in any
        missing file. Legacy ``secrets.yaml`` is deleted.
        """
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        self.migrate_layout()

        if not self.config_path.exists():
            if config_template:
                self.write_config_template()
            else:
                self.save_config({
                    "version": 1,
                    "gateway": dict(DEFAULT_GATEWAY),
                    "models": {},
                })
        if not self.providers_path.exists():
            if config_template:
                self.write_providers_template()
            else:
                self.save_providers_config({"providers": []})

        self._cleanup_legacy_files()

    def migrate_layout(self) -> None:
        """Migrate the prior layout to the current config.yaml + providers.yaml
        split.

        Prior two-file layout (``providers.yaml`` holding gateway + ``models.yaml``)
        -> lift gateway/version out of providers.yaml and models out of
        models.yaml into a new non-secret ``config.yaml``; rewrite
        providers.yaml to ``{providers}`` only; delete ``models.yaml``.
        Idempotent -- a no-op once ``config.yaml`` exists in the new shape, and
        a no-op on a fresh install (nothing to migrate).

        An ancient single-file ``config.yaml`` (one containing a ``providers``
        key, from before the two-file split) is backed up to
        ``config.yaml.bak`` rather than misread as the new non-secret config;
        its contents are not auto-migrated (the prior commits already moved
        those installs to the two-file layout).
        """
        if self.config_path.exists():
            try:
                existing = yaml.safe_load(self.config_path.read_bytes()) or {}
            except Exception:
                existing = {}
            if isinstance(existing, dict) and "providers" in existing:
                # Ancient single-file layout; back it up, fall through.
                try:
                    os.replace(
                        self.config_path,
                        self.config_path.with_suffix(".yaml.bak"),
                    )
                except OSError:
                    pass
            else:
                return  # already new-shape config.yaml

        providers_existed = self.providers_path.exists()
        models_existed = self.dir.joinpath("models.yaml").exists()
        if not (providers_existed or models_existed):
            return  # fresh install; init_first_run lays down defaults

        cfg = {"version": 1, "gateway": dict(DEFAULT_GATEWAY), "models": {}}
        if providers_existed:
            try:
                pdata = yaml.safe_load(self.providers_path.read_bytes()) or {}
            except Exception:
                pdata = {}
            if isinstance(pdata, dict):
                if pdata.get("gateway"):
                    cfg["gateway"] = pdata["gateway"]
                if pdata.get("version") is not None:
                    cfg["version"] = pdata["version"]
                # Strip providers.yaml to {providers} only; re-validate the
                # base_urls against the migrated gateway.
                self.save_providers_config(
                    {"providers": pdata.get("providers", [])}, cfg["gateway"])
        if models_existed:
            models_path = self.dir / "models.yaml"
            try:
                mdata = yaml.safe_load(models_path.read_bytes()) or {}
            except Exception:
                mdata = {}
            if isinstance(mdata, dict) and isinstance(mdata.get("models"), dict):
                cfg["models"] = mdata["models"]
            try:
                models_path.unlink()
            except OSError:
                pass
        self.save_config(cfg)

    def _cleanup_legacy_files(self) -> None:
        """Delete legacy ``secrets.yaml`` from the old vault layout."""
        for name in _LEGACY_FILES:
            p = self.dir / name
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # config.yaml (gateway + models + llmport api_key, 0600 -- holds a credential)
    # ------------------------------------------------------------------

    def load_config(self) -> dict:
        """Load and return config.yaml (``{version, gateway, models, api_key?}``).

        Raises ``FileNotFoundError`` if the file does not exist yet, and
        ``ValueError`` if it is valid YAML but not a mapping, so callers can
        abort cleanly instead of crashing on ``.get()``.
        """
        if not self.config_path.exists():
            raise FileNotFoundError(self.config_path)
        data = yaml.safe_load(self.config_path.read_bytes())
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(
                f"config.yaml 顶层必须是字典，得到 {type(data).__name__}"
            )
        return data

    def save_config(self, data: dict) -> None:
        """Write config.yaml (0600 -- it holds the llmport api_key credential)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        plaintext = yaml.dump(
            data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        _atomic_write_bytes(self.config_path, plaintext.encode("utf-8"))
        _chmod(self.config_path, 0o600)
        _chmod(self.dir, 0o700)

    def write_config_template(self) -> None:
        """Write the first-run config.yaml with commented examples."""
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(
            self.config_path, _CONFIG_TEMPLATE.encode("utf-8"))
        _chmod(self.config_path, 0o600)

    # ------------------------------------------------------------------
    # providers.yaml (providers WITH api_key, 0600)
    # ------------------------------------------------------------------

    def load_providers_config(self) -> dict:
        """Load and return the providers config (providers + keys).

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

    def save_providers_config(self, data: dict, gateway: dict | None = None) -> None:
        """Write the providers config (providers + keys, 0600).

        Validates provider base_urls against the SSRF blocklist (see
        :mod:`llmport.config.validation`) so every write path -- CLI and any
        future config editor -- is guarded from one place. *gateway* supplies
        the host:port for the self-loop check; when omitted it is read from
        ``config.yaml`` (defaulting to ``127.0.0.1:11434`` if absent).
        """
        from llmport.config.validation import validate_providers_config
        if gateway is None:
            gateway = self.load_gateway()
        validate_providers_config(data, gateway)
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
    # llmport API key (clients' access key; field in config.yaml)
    # ------------------------------------------------------------------
    #
    # This is llmport's OWN api key -- the credential a client presents to use
    # the gateway (analogous to an OpenAI/Anthropic api key for their service).
    # It lives as the ``api_key`` field in ``config.yaml``, alongside the
    # gateway address and model mappings. It is unrelated to the upstream
    # providers' ``api_key`` (in providers.yaml), which authenticates the
    # gateway to each upstream.

    def load_api_key(self) -> str:
        """Return llmport's own API key (``api_key`` field in config.yaml).

        Returns ``""`` when unset. An empty key is a *misconfiguration*, not
        an open mode: ``llmport setup`` generates one, the CLI ``start``
        refuses without one, ``run_daemon`` refuses to serve, and the
        middleware answers 503 to every non-/health route. Tolerates a missing
        or unreadable config.yaml so daemon startup never crashes.
        """
        try:
            cfg = self.load_config()
        except (FileNotFoundError, ValueError):
            return ""
        key = cfg.get("api_key")
        return key if isinstance(key, str) else ""

    def set_api_key(self, key: str) -> None:
        """Write llmport's API key to config.yaml (preserving gateway/models)."""
        try:
            cfg = self.load_config()
        except (FileNotFoundError, ValueError):
            cfg = {"version": 1, "gateway": dict(DEFAULT_GATEWAY), "models": {}}
        cfg["api_key"] = key
        self.save_config(cfg)

    def clear_api_key(self) -> None:
        """Remove llmport's API key from config.yaml (no-op if absent)."""
        try:
            cfg = self.load_config()
        except (FileNotFoundError, ValueError):
            return
        if "api_key" in cfg:
            del cfg["api_key"]
            self.save_config(cfg)

    # ------------------------------------------------------------------
    # models convenience (lives in config.yaml's ``models`` section)
    # ------------------------------------------------------------------

    def load_models_config(self) -> dict:
        """Load and return ``{"models": {...}}`` from ``config.yaml``.

        Returns ``{"models": {}}`` if the file does not exist yet (models are
        optional at first run). Raises ``ValueError`` if ``config.yaml`` is
        valid YAML but not a mapping.
        """
        try:
            data = self.load_config()
        except FileNotFoundError:
            return {"models": {}}
        return {"models": data.get("models") or {}}

    def save_models_config(self, data: dict) -> None:
        """Write the ``models`` section of ``config.yaml`` (0600).

        Preserves the existing gateway/version; only the ``models`` key is
        replaced. Creates ``config.yaml`` with defaults if it does not exist.
        """
        try:
            cfg = self.load_config()
        except FileNotFoundError:
            cfg = {"version": 1, "gateway": dict(DEFAULT_GATEWAY), "models": {}}
        cfg["models"] = data.get("models", {})
        self.save_config(cfg)

    # ------------------------------------------------------------------
    # gateway convenience
    # ------------------------------------------------------------------

    def load_gateway(self) -> dict:
        """Return the canonical ``{"host", "port"}`` gateway dict.

        Read from ``config.yaml``'s ``gateway`` section; falls back to the
        default (``127.0.0.1:11434``) if the file is missing or unreadable so
        gateway resolution never crashes the daemon.
        """
        try:
            data = self.load_config()
        except (FileNotFoundError, ValueError):
            return dict(DEFAULT_GATEWAY)
        gw = data.get("gateway") or {}
        return {
            "host": gw.get("host", DEFAULT_GATEWAY["host"]),
            "port": int(gw.get("port", DEFAULT_GATEWAY["port"])),
        }
