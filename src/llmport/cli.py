"""CLI entry point for llmport.

The command surface is built with Typer: a top-level app with the lifecycle
commands (setup/start/stop/restart/status) and three nested sub-apps
(provider/model/config). The Typer command wrappers are thin -- all real work
lives in the ``_cmd_*`` / ``_provider_*`` / ``_model_*`` / ``_config_*``
functions below, which take a :class:`DaemonManager` (and explicit params) so
they stay callable from tests without going through argv parsing.
"""

import getpass
import os
import subprocess
from typing import Literal

import typer

from llmport.daemon import DaemonManager, run_daemon

__version__ = "0.1.0"

_DEFAULT_CFG = {
    "version": 1,
    "gateway": {"host": "127.0.0.1", "port": 11434},
    "providers": [],
    "models": [],
}

# ============================================================================
# Typer app & command wiring
# ============================================================================

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Terminal LLM API Gateway - a local multi-provider routing proxy.",
)
provider_app = typer.Typer(no_args_is_help=True, help="管理供应商（API key 明文存储）")
model_app = typer.Typer(no_args_is_help=True, help="管理模型映射（公开名 -> 供应商模型）")
config_app = typer.Typer(no_args_is_help=True, help="配置文件管理（直接编辑 config.yaml）")

app.add_typer(provider_app, name="provider")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"llmport {__version__}")
        raise typer.Exit()


def _daemon_callback(value: bool) -> None:
    # Eager so it fires during parsing, before ``no_args_is_help`` can short-
    # circuit a bare ``llmport --daemon`` (no subcommand) to the help screen.
    if value:
        run_daemon()
        raise typer.Exit()


@app.callback()
def _main(
    daemon: bool = typer.Option(
        False, "--daemon", hidden=True,
        callback=_daemon_callback, is_eager=True),
    version: bool = typer.Option(
        None, "--version", "-V",
        callback=_version_callback, is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Terminal LLM API Gateway - a local multi-provider routing proxy."""
    # tui 入口暂时移除（后端 app.py/ui/* 保留）；恢复时加 @app.command("tui")
    # 调 from llmport.app import LlmPortApp; LlmPortApp().run()。
    # --daemon / --version are handled by their eager option callbacks above.


# --- lifecycle commands ----------------------------------------------------


@app.command("setup")
def _cli_setup() -> None:
    _cmd_setup(DaemonManager())


@app.command("start")
def _cli_start() -> None:
    _cmd_start(DaemonManager())


@app.command("stop")
def _cli_stop() -> None:
    _cmd_stop(DaemonManager())


@app.command("restart")
def _cli_restart() -> None:
    _cmd_restart(DaemonManager())


@app.command("status")
def _cli_status() -> None:
    _cmd_status(DaemonManager())


# --- provider commands -----------------------------------------------------


@provider_app.command("add")
def _cli_provider_add(
    id: str = typer.Option(..., "--id", help="供应商 ID，如 anthropic"),
    name: str | None = typer.Option(None, "--name", help="显示名称（默认同 id）"),
    protocol: Literal["openai", "anthropic"] = typer.Option(
        "openai", "--protocol", help="openai | anthropic"),
    base_url: str | None = typer.Option(None, "--base-url", help="留空则按 protocol 取默认"),
    api_key: str | None = typer.Option(
        None, "--api-key",
        help="API key；不传时：新增供应商则交互输入（不回显），更新供应商则保留原 key"),
) -> None:
    _provider_add(DaemonManager(), id=id, name=name, protocol=protocol,
                  base_url=base_url, api_key=api_key)


@provider_app.command("list")
def _cli_provider_list() -> None:
    _provider_list(DaemonManager())


@provider_app.command("remove")
def _cli_provider_remove(id: str = typer.Argument(..., help="供应商 ID")) -> None:
    _provider_remove(DaemonManager(), id)


@provider_app.command("test")
def _cli_provider_test(id: str = typer.Argument(..., help="供应商 ID")) -> None:
    _provider_test(DaemonManager(), id)


# --- model commands --------------------------------------------------------


@model_app.command("add")
def _cli_model_add(
    name: str = typer.Option(..., "--name", help="公开名（客户端请求时填的 model）"),
    provider: str = typer.Option(..., "--provider", help="供应商 ID"),
    upstream: str | None = typer.Option(None, "--upstream", help="供应商的真实模型名（默认同 name）"),
) -> None:
    _model_add(DaemonManager(), name=name, provider=provider, upstream=upstream)


@model_app.command("list")
def _cli_model_list() -> None:
    _model_list(DaemonManager())


@model_app.command("remove")
def _cli_model_remove(name: str = typer.Argument(..., help="模型公开名")) -> None:
    _model_remove(DaemonManager(), name)


# --- config commands -------------------------------------------------------


@config_app.command("init")
def _cli_config_init() -> None:
    _config_init(DaemonManager())


@config_app.command("path")
def _cli_config_path() -> None:
    print(str(DaemonManager().store.config_path))


@config_app.command("show")
def _cli_config_show() -> None:
    _config_show(DaemonManager())


@config_app.command("edit")
def _cli_config_edit() -> None:
    _config_edit(DaemonManager())


def main() -> None:
    app()


# ============================================================================
# setup
# ============================================================================


def _cmd_setup(dm: DaemonManager) -> None:
    """Bootstrap the config directory, template config, and empty secrets file.

    Setup does NOT prompt for providers/models -- that's what ``provider add``
    and ``model add`` are for. Setup just lays down the files and points the
    user at the next steps.
    """
    store = dm.store
    _ensure_store_init(store)

    print("llmport 设置")
    print(f"配置目录: {store.dir}")
    print(f"  config.yaml   路由配置 (供应商/模型,不含 API key,可手编辑)")
    print(f"  secrets.yaml  API key 明文存储 (0600,不写进 config.yaml)")
    print()
    print("下一步:")
    print("  llmport provider add --id anthropic --protocol anthropic   # 加供应商")
    print("  llmport model add --name claude-sonnet --provider anthropic  # 加模型映射")
    print("  llmport start                                                # 启动网关")
    print()
    print("提示: provider add 不传 --api-key 时交互输入(不回显)。")


# ============================================================================
# provider add / list / remove
# ============================================================================


def _provider_add(
    dm: DaemonManager, *, id: str, name: str | None, protocol: str,
    base_url: str | None, api_key: str | None,
) -> None:
    """Add or update a provider. The API key is stored in secrets.yaml (plaintext).

    Updating an existing provider without ``--api-key`` preserves the
    existing key; only a brand-new provider prompts for one.
    """
    store = dm.store
    _ensure_store_init(store)

    cfg = _safe_load_config(store) or dict(_DEFAULT_CFG)
    providers = cfg.get("providers", [])
    secrets = store.load_secrets()

    pid = id
    existing = next((p for p in providers if p["id"] == pid), None)

    if not base_url:
        # Host root only; /v1 is added by the path constants on forward.
        base_url = ("https://api.openai.com" if protocol == "openai"
                    else "https://api.anthropic.com")

    # SSRF blocklist (metadata / self-loop). Reject before touching disk so a
    # bad URL never lands in config.yaml. save_config re-checks as a safety net.
    gw = cfg.get("gateway") or {}
    try:
        from llmport.config.validation import validate_provider_base_url
        validate_provider_base_url(
            base_url, gw.get("host", "127.0.0.1"), int(gw.get("port", 11434)))
    except ValueError as e:
        print(f"拒绝保存: {e}")
        return

    # Key resolution: explicit flag > prompt (new provider) > keep existing.
    raw_api_key = api_key  # None when --api-key was not passed
    if api_key is None:
        if existing is None:
            try:
                api_key = getpass.getpass("API key (输入不回显,留空跳过): ").strip()
            except (EOFError, OSError):
                print("无法交互读取 API key（非交互式环境）。请用 --api-key 传入。")
                return
        else:
            api_key = secrets.get(pid, "")  # keep existing key on update

    name = name or pid
    providers = [p for p in providers if p["id"] != pid] + [{
        "id": pid, "name": name, "protocol": protocol, "base_url": base_url}]
    if api_key:
        secrets[pid] = api_key

    cfg["providers"] = providers
    store.save_config(cfg)
    store.save_secrets(secrets)

    if existing is None:
        print(f"已添加供应商 {pid} (API key {'已存储' if api_key else '未设置'})")
    else:
        if raw_api_key:
            print(f"已更新供应商 {pid} (API key 已更新)")
        else:
            print(f"已更新供应商 {pid} (API key 保留原值)")
    _apply_if_running(dm)


def _provider_list(dm: DaemonManager) -> None:
    cfg = _safe_load_config(dm.store)
    if not cfg or not cfg.get("providers"):
        print("无供应商。运行 `llmport provider add --id <id>` 添加。")
        return
    print(f"{'ID':<16} {'协议':<10} {'base_url':<34} 名称")
    for p in cfg["providers"]:
        print(f"{p['id']:<16} {p['protocol']:<10} {p['base_url']:<34} {p.get('name', '')}")


def _provider_remove(dm: DaemonManager, pid: str) -> None:
    store = dm.store
    cfg = _safe_load_config(store)
    if not cfg:
        print("无配置文件。")
        return
    providers = cfg.get("providers", [])
    new = [p for p in providers if p["id"] != pid]
    if len(new) == len(providers):
        print(f"未找到供应商 {pid}。")
        return
    cfg["providers"] = new
    secrets = store.load_secrets()
    secrets.pop(pid, None)
    store.save_config(cfg)
    store.save_secrets(secrets)
    print(f"已删除供应商 {pid}（及其 key）。")
    _apply_if_running(dm)


def _provider_test(dm: DaemonManager, pid: str) -> None:
    """Test a configured provider's connection directly from disk.

    No daemon required: reads config + secrets, then calls the same handler
    functions the control API uses. For OpenAI it lists upstream models
    (handy for picking the ``upstream`` name in a model mapping); for
    Anthropic it sends a minimal 1-token request.
    """
    import asyncio

    store = dm.store
    cfg = _safe_load_config(store)
    if not cfg:
        print("无配置文件。运行 `llmport config init` 或 `llmport provider add`。")
        return
    providers = cfg.get("providers", [])
    entry = next((p for p in providers if p.get("id") == pid), None)
    if not entry:
        ids = ", ".join(p.get("id", "") for p in providers) or "（无）"
        print(f"未找到供应商 {pid}。已配置: {ids}")
        return
    secrets = store.load_secrets()
    api_key = secrets.get(pid, "")
    if not api_key:
        print(f"供应商 {pid} 未设置 API key。")
        print(f"运行 `llmport provider add --id {pid} --api-key <key>` 补上。")
        return

    from llmport.models.provider import ProviderConfig
    provider = ProviderConfig(
        id=pid,
        name=entry.get("name", pid),
        protocol=entry.get("protocol", "openai"),
        base_url=entry.get("base_url", ""),
        api_key=api_key,
    )
    print(f"测试 {pid} ({provider.protocol}, {provider.base_url}) ...")
    result = asyncio.run(_provider_test_async(provider))
    if result["ok"]:
        print(f"✓ 连通 ({result['latency_ms']:.0f}ms)")
        models = result.get("models")
        if models:
            ids = [m.get("id") for m in models
                   if isinstance(m, dict) and m.get("id")]
            print(f"  可用模型 ({len(ids)}):")
            for mid in ids[:20]:
                print(f"    {mid}")
            if len(ids) > 20:
                print(f"    ... 还有 {len(ids) - 20} 个")
    else:
        err = result["error"] or "连接失败（上游无响应或网络错误）"
        print(f"✗ 失败: {err}")
        raise SystemExit(1)


async def _provider_test_async(provider) -> dict:
    """Run the protocol-appropriate connectivity test, return a result dict."""
    import time
    from llmport.gateway import openai_handler, anthropic_handler

    if provider.protocol == "openai":
        t0 = time.monotonic()
        models, error = await openai_handler.list_models(provider)
        latency = (time.monotonic() - t0) * 1000
        return {"ok": models is not None, "latency_ms": latency,
                "error": error, "models": models}
    ok, latency, error = await anthropic_handler.test_connection(provider)
    return {"ok": ok, "latency_ms": latency, "error": error, "models": None}


# ============================================================================
# model add / list / remove
# ============================================================================


def _model_add(
    dm: DaemonManager, *, name: str, provider: str, upstream: str | None,
) -> None:
    store = dm.store
    _ensure_store_init(store)

    cfg = _safe_load_config(store) or dict(_DEFAULT_CFG)
    provider_ids = [p["id"] for p in cfg.get("providers", [])]
    if provider not in provider_ids:
        print(f"未知供应商 {provider}。先运行: llmport provider add --id {provider}")
        return

    upstream = upstream or name
    models = cfg.get("models", [])
    existed = any(m.get("name") == name for m in models)
    models = [m for m in models if m.get("name") != name]
    models.append({"name": name, "provider": provider, "upstream": upstream})
    cfg["models"] = models
    store.save_config(cfg)
    print(f"{'已更新' if existed else '已添加'}模型 {name} -> {provider}/{upstream}")
    _apply_if_running(dm)


def _model_list(dm: DaemonManager) -> None:
    cfg = _safe_load_config(dm.store)
    if not cfg or not cfg.get("models"):
        print("无模型。运行 `llmport model add --name <n> --provider <p>` 添加。")
        return
    print(f"{'公开名':<20} {'供应商':<14} {'upstream':<24}")
    for m in cfg["models"]:
        bindings = m.get("bindings")
        if bindings:
            for b in bindings:
                print(f"{m['name']:<20} {b['provider']:<14} {b['upstream']:<24}")
        else:
            print(f"{m['name']:<20} {m.get('provider', ''):<14} {m.get('upstream', ''):<24}")


def _model_remove(dm: DaemonManager, name: str) -> None:
    store = dm.store
    cfg = _safe_load_config(store)
    if not cfg:
        print("无配置文件。")
        return
    models = cfg.get("models", [])
    new = [m for m in models if m.get("name") != name]
    if len(new) == len(models):
        print(f"未找到模型 {name}。")
        return
    cfg["models"] = new
    store.save_config(cfg)
    print(f"已删除模型 {name}。")
    _apply_if_running(dm)


# ============================================================================
# config init / path / show / edit
# ============================================================================


def _config_init(dm: DaemonManager) -> None:
    """Write the commented config template (refuses to clobber an existing one)."""
    store = dm.store
    if store.config_path.exists():
        print(f"配置文件已存在: {store.config_path}")
        print("如需重新生成模板,请先备份并删除该文件,再运行 `llmport config init`。")
        return
    store.init_first_run(config_template=True)
    print(f"已生成配置模板: {store.config_path}")
    print("编辑该文件填入供应商和模型,然后运行 `llmport start`。")
    print("API key 不写进 config.yaml,用 `llmport provider add` 单独明文存储。")


def _config_show(dm: DaemonManager) -> None:
    """Print config.yaml contents (no secrets live there) + key-status notes."""
    store = dm.store
    if not store.config_path.exists():
        print("尚无配置文件。运行 `llmport config init` 生成模板。")
        return
    text = store.config_path.read_text(encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")

    # Best-effort: annotate which providers have a key in the vault. The config
    # file itself never holds keys, so this is the only way to see key status.
    try:
        cfg = store.load_config()
    except Exception:
        return
    providers = cfg.get("providers", []) if isinstance(cfg, dict) else []
    if not providers:
        return
    try:
        secrets = store.load_secrets()
    except Exception:
        secrets = {}
    print()
    print("# API key 状态（key 存在 secrets.yaml,不在上面的文件里）:")
    for p in providers:
        pid = p.get("id", "")
        status = "已设置" if secrets.get(pid) else "未设置"
        print(f"#   {pid}: {status}")


def _config_edit(dm: DaemonManager) -> None:
    """Open config.yaml in $EDITOR (default vi)."""
    store = dm.store
    if not store.config_path.exists():
        print("尚无配置文件。运行 `llmport config init` 生成模板。")
        return
    editor = os.environ.get("EDITOR") or "vi"
    subprocess.call([editor, str(store.config_path)])


# ============================================================================
# daemon control
# ============================================================================


def _cmd_start(dm: DaemonManager) -> None:
    # Refuse to start with no providers configured.
    cfg = _safe_load_config(dm.store)
    if not cfg or not cfg.get("providers"):
        print("尚未配置供应商。请先运行: llmport provider add --id <id>")
        return
    for w in _validate_config(cfg):
        print(f"警告: {w}")
    if dm.is_running():
        print(f"Gateway already running on {_url(dm)}")
        return
    if dm.start():
        print(f"Gateway started on {_url(dm)}")
        print("  /openai/v1/*    -> OpenAI protocol")
        print("  /anthropic/v1/* -> Anthropic protocol")
        print("  /v1/*           -> SDK aliases")
        print("  /api/*          -> control API")
    else:
        print("Gateway failed to start. Check that the configured port is free.")


def _cmd_stop(dm: DaemonManager) -> None:
    if not dm.is_running():
        print("Gateway is not running.")
        return
    dm.stop()
    print("Gateway stopped.")


def _cmd_restart(dm: DaemonManager) -> None:
    if dm.is_running():
        if dm.restart():
            print(f"Gateway restarted on {_url(dm)}")
        else:
            print("Gateway failed to restart. Check that the configured port "
                  "is free and config.yaml is valid.")
    else:
        if dm.start():
            print(f"Gateway started on {_url(dm)}")
        else:
            print("Gateway failed to start. Check that the configured port "
                  "is free and config.yaml is valid.")


def _cmd_status(dm: DaemonManager) -> None:
    if not dm.is_running():
        print("Gateway is not running.")
        return
    status = dm.get_status()
    if not status.get("running"):
        print("Gateway is not running.")
        return

    gw = status.get("gateway") or {}
    host = gw.get("host", "127.0.0.1")
    port = gw.get("port") or dm.get_control_port()
    print(f"Gateway running on http://{host}:{port}")
    print("  /openai/v1/*    -> OpenAI")
    print("  /anthropic/v1/* -> Anthropic")
    print("  /v1/*           -> SDK aliases")
    print("  /api/*          -> control")
    print()

    models = status.get("models") or []
    if models:
        print(f"  Models ({len(models)}): {', '.join(models)}")
    else:
        print("  Models: none configured")
    print(f"  Uptime:   {_fmt_uptime(status.get('uptime', 0))}")
    print(f"  Requests: {status.get('request_count', 0)}")
    print(f"  Tokens:   {status.get('total_tokens', 0)}")

    providers = status.get("providers") or []
    if providers:
        print(f"  Providers ({len(providers)}):")
        for p in providers:
            latency = p.get("latency_ms", 0) or 0
            print(f"    {p['id']:<16} {p['status']:<10} {latency:.0f}ms")


# ============================================================================
# helpers
# ============================================================================


def _ensure_store_init(store) -> None:
    """Ensure the config dir, key, template config, and vault exist.

    Delegates to ``store.init_first_run`` (single init path, kept in sync)
    with the commented template so ``setup`` users get a reference config.
    """
    store.init_first_run(config_template=True)


def _apply_if_running(dm: DaemonManager) -> None:
    """Restart the running daemon so config changes take effect.

    The daemon reads config at startup, so CLI edits to config.yaml need a
    restart to apply. Loopback gateway, ~1s downtime. No-op if not running.
    """
    if not dm.is_running():
        return
    if dm.restart():
        print("已重启网关使配置生效")
    else:
        print("警告: 网关重启失败,新配置可能未生效。请手动 `llmport restart`。")


def _url(dm: DaemonManager) -> str:
    port = dm.get_control_port()
    if port:
        return f"http://127.0.0.1:{port}"
    return "http://127.0.0.1:11434"


def _safe_load_config(store) -> dict | None:
    """Load config. Return None only if it doesn't exist yet.

    A corrupt/unreadable config is NOT silently replaced with the default --
    that would destroy the user's existing providers/models on the next
    ``provider add`` / ``model add`` / ``setup``. Abort with a message instead
    so the user can fix or back up the file.
    """
    try:
        return store.load_config()
    except FileNotFoundError:
        return None
    except Exception as e:
        # Corrupt YAML, or valid-YAML-but-not-a-dict (load_config raises
        # ValueError for the latter). Refuse rather than fall back to the
        # empty default, which would overwrite the user's existing config.
        print(f"配置文件 {store.config_path} 无法解析: {e}")
        print("已中止,不会覆盖现有配置。请修复后重试,或备份后删除该文件重新配置。")
        raise SystemExit(1)


def _validate_config(cfg) -> list[str]:
    """Return human-readable warnings about malformed config entries.

    The parser (``parse_models_config`` / ``ProviderConfig.from_dict``)
    tolerates missing fields by skipping/degrading instead of crashing, so a
    hand-edit typo can silently drop a model or leave a provider inert. This
    surfaces such cases at ``llmport start`` time -- in the user's terminal,
    not the detached daemon's discarded stderr.
    """
    if not isinstance(cfg, dict):
        return []
    from llmport.models.model import parse_models_config
    from llmport.config.validation import validate_provider_base_url

    gw = cfg.get("gateway") or {}
    gw_host = gw.get("host", "127.0.0.1")
    gw_port = int(gw.get("port", 11434))

    warnings: list[str] = []
    for p in cfg.get("providers", []):
        if not p.get("id"):
            warnings.append("供应商条目缺少 id 字段，将被忽略")
        elif not p.get("base_url"):
            warnings.append(f"供应商 {p['id']} 缺少 base_url，无法转发")
        else:
            try:
                validate_provider_base_url(
                    p["base_url"], gw_host, gw_port)
            except ValueError as e:
                warnings.append(
                    f"供应商 {p['id']} 的 base_url 被拒绝（运行时跳过）: {e}")
    parsed_names = {m.name for m in parse_models_config(cfg.get("models", []))}
    for m in cfg.get("models", []):
        name = m.get("name") or m.get("id")
        if name and name not in parsed_names:
            warnings.append(
                f"模型 {name} 的 binding 缺字段（provider/upstream），将被忽略"
            )
    return warnings


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    return f"{s // 3600}h{(s % 3600) // 60}m"
