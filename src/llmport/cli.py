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
import time
import unicodedata
from typing import Literal

import typer

from llmport.daemon import DaemonManager, run_daemon

__version__ = "0.1.0"

_DEFAULT_PROVIDERS = {"providers": []}

_DEFAULT_CONFIG = {
    "version": 1,
    "gateway": {"host": "127.0.0.1", "port": 11434},
    "models": {},
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
api_key_app = typer.Typer(no_args_is_help=True, help="管理 llmport 自己的 API key（客户端访问 llmport 时出示）")

app.add_typer(provider_app, name="provider")
app.add_typer(model_app, name="model")
app.add_typer(config_app, name="config")
app.add_typer(api_key_app, name="api-key")


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
    host: str | None = typer.Option(
        None, "--host", hidden=True, is_eager=True,
        help="网关监听地址(覆盖 config.yaml;由 start 传给 daemon 子进程)"),
    port: int | None = typer.Option(
        None, "--port", hidden=True, is_eager=True,
        help="网关监听端口(覆盖 config.yaml)"),
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
    # --host/--port are declared (hidden) so the daemon subprocess launched as
    # `llmport --daemon --host X --port Y` accepts them; run_daemon reads them
    # from argv directly (the eager --daemon callback exits first).


# --- lifecycle commands ----------------------------------------------------


@app.command("setup")
def _cli_setup() -> None:
    _cmd_setup(DaemonManager())


@app.command("start")
def _cli_start(
    host: str | None = typer.Option(
        None, "--host", help="网关监听地址(覆盖 config.yaml)"),
    port: int | None = typer.Option(
        None, "--port", help="网关监听端口(覆盖 config.yaml)"),
) -> None:
    _cmd_start(DaemonManager(), host=host, port=port)


@app.command("stop")
def _cli_stop() -> None:
    _cmd_stop(DaemonManager())


@app.command("restart")
def _cli_restart(
    host: str | None = typer.Option(
        None, "--host", help="网关监听地址(覆盖 config.yaml)"),
    port: int | None = typer.Option(
        None, "--port", help="网关监听端口(覆盖 config.yaml)"),
) -> None:
    _cmd_restart(DaemonManager(), host=host, port=port)


@app.command("status")
def _cli_status() -> None:
    _cmd_status(DaemonManager())


# --- provider commands -----------------------------------------------------


@provider_app.command("add")
def _cli_provider_add(
    name: str = typer.Option(..., "--name", help="供应商标识(模型映射里用此名引用),如 anthropic"),
    protocol: Literal["openai", "anthropic"] = typer.Option(
        "openai", "--protocol", help="openai | anthropic"),
    base_url: str | None = typer.Option(None, "--base-url", help="留空则按 protocol 取默认"),
    api_key: str | None = typer.Option(
        None, "--api-key",
        help="API key；不传时：新增供应商则交互输入（不回显），更新供应商则保留原 key"),
) -> None:
    _provider_add(DaemonManager(), name=name, protocol=protocol,
                  base_url=base_url, api_key=api_key)


@provider_app.command("list")
def _cli_provider_list() -> None:
    _provider_list(DaemonManager())


@provider_app.command("remove")
def _cli_provider_remove(name: str = typer.Argument(..., help="供应商 name")) -> None:
    _provider_remove(DaemonManager(), name)


# --- model commands --------------------------------------------------------


@model_app.command("add")
def _cli_model_add(
    name: str = typer.Option(..., "--name", help="公开名（客户端请求时填的 model）"),
    provider: str = typer.Option(..., "--provider", help="供应商 name"),
    upstream: str | None = typer.Option(None, "--upstream", help="供应商的真实模型名（默认同 name）"),
) -> None:
    _model_add(DaemonManager(), name=name, provider=provider, upstream=upstream)


@model_app.command("list")
def _cli_model_list() -> None:
    _model_list(DaemonManager())


@model_app.command("remove")
def _cli_model_remove(name: str = typer.Argument(..., help="模型公开名")) -> None:
    _model_remove(DaemonManager(), name)


@model_app.command("test")
def _cli_model_test(
    name: str = typer.Argument(None, help="模型公开名（省略则测全部模型）"),
) -> None:
    _model_test(DaemonManager(), name)


# --- config commands -------------------------------------------------------


@config_app.command("init")
def _cli_config_init() -> None:
    _config_init(DaemonManager())


@config_app.command("path")
def _cli_config_path() -> None:
    store = DaemonManager().store
    print(f"config:    {store.config_path}")
    print(f"providers: {store.providers_path}")


@config_app.command("show")
def _cli_config_show() -> None:
    _config_show(DaemonManager())


@config_app.command("edit")
def _cli_config_edit(
    target: str = typer.Option(
        "config", "--target", "-t", help="编辑哪个文件: config | providers"),
) -> None:
    _config_edit(DaemonManager(), target=target)


# --- api-key commands ------------------------------------------------------


@api_key_app.command("set")
def _cli_api_key_set() -> None:
    _api_key_set(DaemonManager())


@api_key_app.command("show")
def _cli_api_key_show(
    reveal: bool = typer.Option(
        False, "--reveal", help="显示明文（用于复制到客户端 SDK 配置）"),
) -> None:
    _api_key_show(DaemonManager(), reveal=reveal)


@api_key_app.command("clear")
def _cli_api_key_clear() -> None:
    _api_key_clear(DaemonManager())


def main() -> None:
    app()


# ============================================================================
# setup
# ============================================================================


def _cmd_setup(dm: DaemonManager) -> None:
    """Bootstrap the config directory and template files.

    Setup does NOT prompt for providers/models -- that's what ``provider add``
    and ``model add`` are for. Setup just lays down the files and points the
    user at the next steps.
    """
    store = dm.store
    _ensure_store_init(store)

    print("llmport 设置")
    print(f"配置目录: {store.dir}")
    print(f"  config.yaml     网关 + 模型映射 (非敏感, 0644)")
    print(f"  providers.yaml  供应商 + API key (0600)")
    print()
    print("下一步:")
    print("  llmport provider add --name anthropic --protocol anthropic   # 加供应商")
    print("  llmport model add --name claude-sonnet --provider anthropic  # 加模型映射")
    print("  llmport start                                                # 启动网关")
    print()
    print("提示: provider add 不传 --api-key 时交互输入(不回显)。")


# ============================================================================
# provider add / list / remove
# ============================================================================


def _provider_add(
    dm: DaemonManager, *, name: str, protocol: str,
    base_url: str | None, api_key: str | None,
) -> None:
    """Add or update a provider (base_url + api_key together in providers.yaml).

    Updating an existing provider without ``--api-key`` preserves the
    existing key; only a brand-new provider prompts for one.
    """
    store = dm.store
    _ensure_store_init(store)

    pdata = _safe_load_providers(store) or dict(_DEFAULT_PROVIDERS)
    providers = pdata.get("providers", [])

    existing = next((p for p in providers if p.get("name") == name), None)

    if not base_url:
        # Host root only; /v1 is added by the path constants on forward.
        base_url = ("https://api.openai.com" if protocol == "openai"
                    else "https://api.anthropic.com")

    # SSRF blocklist (metadata / self-loop). Reject before touching disk so a
    # bad URL never lands in providers.yaml. save_providers_config re-checks.
    gw = store.load_gateway()
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
            api_key = existing.get("api_key", "")  # keep existing key on update

    entry = {"name": name, "protocol": protocol, "base_url": base_url}
    if api_key:
        entry["api_key"] = api_key
    providers = [p for p in providers if p.get("name") != name] + [entry]

    pdata["providers"] = providers
    store.save_providers_config(pdata)

    if existing is None:
        print(f"已添加供应商 {name} (API key {'已存储' if api_key else '未设置'})")
    else:
        if raw_api_key:
            print(f"已更新供应商 {name} (API key 已更新)")
        else:
            print(f"已更新供应商 {name} (API key 保留原值)")
    _apply_if_running(dm)


def _provider_list(dm: DaemonManager) -> None:
    pdata = _safe_load_providers(dm.store)
    if not pdata or not pdata.get("providers"):
        print("无供应商。运行 `llmport provider add --name <name>` 添加。")
        return
    print(f"{'名称':<16} {'协议':<10} {'base_url':<34}")
    for p in pdata["providers"]:
        print(f"{p.get('name', ''):<16} {p.get('protocol', ''):<10} {p.get('base_url', ''):<34}")


def _provider_remove(dm: DaemonManager, name: str) -> None:
    store = dm.store
    pdata = _safe_load_providers(store)
    if not pdata:
        print("无配置文件。")
        return
    providers = pdata.get("providers", [])
    new = [p for p in providers if p.get("name") != name]
    if len(new) == len(providers):
        print(f"未找到供应商 {name}。")
        return
    pdata["providers"] = new
    # The api_key lives inside the provider entry, so it is removed with it.
    store.save_providers_config(pdata)
    print(f"已删除供应商 {name}（及其 key）。")
    _apply_if_running(dm)


# ============================================================================
# model add / list / remove
# ============================================================================


def _model_add(
    dm: DaemonManager, *, name: str, provider: str, upstream: str | None,
) -> None:
    store = dm.store
    _ensure_store_init(store)

    pdata = _safe_load_providers(store) or dict(_DEFAULT_PROVIDERS)
    provider_names = [p.get("name") for p in pdata.get("providers", [])]
    if provider not in provider_names:
        print(f"未知供应商 {provider}。先运行: llmport provider add --name {provider}")
        return

    mdata = _safe_load_models(store) or {"models": {}}
    models = mdata.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
    existed = name in models
    # 无别名(upstream 缺省或同 name): name -> provider (str)
    # 有别名: name -> {provider: upstream} (dict)
    if upstream and upstream != name:
        models[name] = {provider: upstream}
    else:
        models[name] = provider
    mdata["models"] = models
    store.save_models_config(mdata)
    print(f"{'已更新' if existed else '已添加'}模型 {name} -> {provider}/{upstream or name}")
    _apply_if_running(dm)


def _model_list(dm: DaemonManager) -> None:
    mdata = _safe_load_models(dm.store)
    if not mdata or not mdata.get("models"):
        print("无模型。运行 `llmport model add --name <n> --provider <p>` 添加。")
        return
    from llmport.models.model import parse_models_config
    print(f"{'公开名':<22} 绑定（provider/upstream，-> 为 fallback 顺序）")
    for m in parse_models_config(mdata.get("models")):
        chain = " -> ".join(f"{b.provider}/{b.upstream}" for b in m.bindings)
        print(f"{m.name:<22} {chain}")


def _model_remove(dm: DaemonManager, name: str) -> None:
    store = dm.store
    mdata = _safe_load_models(store)
    if not mdata:
        print("无配置文件。")
        return
    models = mdata.get("models", {})
    if not isinstance(models, dict) or name not in models:
        print(f"未找到模型 {name}。")
        return
    del models[name]
    mdata["models"] = models
    store.save_models_config(mdata)
    print(f"已删除模型 {name}。")
    _apply_if_running(dm)


# ============================================================================
# model test
# ============================================================================


def _model_test(dm: DaemonManager, name: str | None = None) -> None:
    """Test configured model(s) by probing each provider binding.

    With a ``name``, probes that model's bindings. Without one, probes every
    configured model. No daemon required: reads bindings from ``config.yaml``
    and providers/keys from ``providers.yaml``, then sends a short request to
    each via the same handler functions the runtime uses. The upstream model
    name comes straight from the binding -- no --model flag, no hardcoded
    fallback. The status code verifies key + model together: 401/403 = bad
    key, 404 = upstream model not found, 2xx = ok; the prompt asks the model
    to reply "有效", which is shown in the table as proof it actually responded.

    A model with at least one healthy binding is "可用" (matching Router
    semantics). Exits nonzero if any probed model has zero healthy bindings
    (i.e. is unusable); a fully-healthy run exits 0.
    """
    import asyncio

    store = dm.store
    mdata = _safe_load_models(store)
    if not mdata or not mdata.get("models"):
        print("无模型。运行 `llmport model add --name <n> --provider <p>` 添加。")
        return
    from llmport.models.model import parse_models_config
    all_models = parse_models_config(mdata.get("models"))
    if name is not None:
        target = next((m for m in all_models if m.name == name), None)
        if not target:
            names = ", ".join(m.name for m in all_models) or "（无）"
            print(f"未找到模型 {name}。已配置: {names}")
            return
        targets = [target]
    else:
        targets = all_models

    pdata = _safe_load_providers(store)
    providers = (pdata or {}).get("providers", [])
    by_name = {p.get("name"): p for p in providers}

    probed = asyncio.run(_probe_all_models(targets, by_name))
    rows = []
    unusable = 0
    for model, results in probed:
        if not any(r["ok"] for r in results):
            unusable += 1
        for r in results:
            binding = f"{r['provider']}/{r['upstream']}"
            if r["ok"]:
                reply = r.get("reply")
                detail = _trunc(reply) if reply else "（无回复）"
                rows.append((model.name, binding, "✓",
                             f"{r['latency_ms']:.0f}ms", detail))
            else:
                err = r.get("error") or "连接失败（上游无响应或网络错误）"
                rows.append((model.name, binding, "✗", "-", _trunc(err)))
    _print_test_table(rows)
    if len(probed) > 1:
        usable = len(probed) - unusable
        print()
        print(f"汇总: {usable}/{len(probed)} 模型可用")
    if unusable > 0:
        raise SystemExit(1)


def _disp_width(s: str) -> int:
    """Display width of ``s``, counting CJK/fullwidth chars as 2 columns."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in s)


def _pad(s: str, width: int) -> str:
    """Left-justify ``s`` to a display ``width`` with trailing spaces."""
    return s + " " * max(0, width - _disp_width(s))


def _trunc(text: str, limit: int = 40) -> str:
    """Collapse whitespace to single spaces and truncate to ``limit`` chars."""
    one_line = " ".join(text.split())
    if len(one_line) > limit:
        return one_line[:limit] + "…"
    return one_line


def _print_test_table(rows: list[tuple[str, str, str, str, str]]) -> None:
    """Print probe results as a display-width-aligned table.

    Each row is ``(model, binding, status, latency, detail)``. The last column
    is left unpadded (no trailing spaces); the rest are padded so CJK content
    stays aligned.
    """
    headers = ("模型", "绑定", "状态", "延时", "详情")

    def fmt(row):
        left = "  ".join(_pad(row[i], widths[i]) for i in range(len(headers) - 1))
        return left + "  " + row[-1]

    cells = [headers, *rows]
    widths = [max(_disp_width(c[i]) for c in cells) for i in range(len(headers))]
    lines = [fmt(headers)] + [fmt(r) for r in rows]
    print(lines[0])
    print("-" * max(_disp_width(l) for l in lines))
    for line in lines[1:]:
        print(line)


async def _probe_all_models(models, providers_by_name) -> list[tuple]:
    """Probe each model's bindings in order; return ``(model, results)`` per model."""
    return [(m, await _model_test_async(m, providers_by_name)) for m in models]


async def _model_test_async(model, providers_by_name) -> list[dict]:
    """Probe each binding in order; return one result dict per binding.

    A missing provider or key is reported as a failed binding rather than
    skipped, so every binding's status is visible.
    """
    from llmport.models.provider import ProviderConfig
    from llmport.gateway import openai_handler, anthropic_handler

    results = []
    for b in model.bindings:
        entry = providers_by_name.get(b.provider)
        if not entry:
            results.append({"provider": b.provider, "upstream": b.upstream,
                            "ok": False, "latency_ms": 0.0,
                            "error": f"供应商 {b.provider} 未配置", "reply": None})
            continue
        api_key = entry.get("api_key", "")
        if not api_key:
            results.append({"provider": b.provider, "upstream": b.upstream,
                            "ok": False, "latency_ms": 0.0,
                            "error": "未设置 API key", "reply": None})
            continue
        provider = ProviderConfig(
            name=b.provider,
            protocol=entry.get("protocol", "openai"),
            base_url=entry.get("base_url", ""),
            api_key=api_key,
        )
        if provider.protocol == "anthropic":
            ok, latency, error, reply = await anthropic_handler.test_connection(
                provider, b.upstream)
        else:
            ok, latency, error, reply = await openai_handler.test_connection(
                provider, b.upstream)
        results.append({"provider": b.provider, "upstream": b.upstream,
                        "ok": ok, "latency_ms": latency, "error": error,
                        "reply": reply})
    return results


# ============================================================================
# config init / path / show / edit
# ============================================================================


def _config_init(dm: DaemonManager) -> None:
    """Write the commented config/providers templates (refuses to clobber)."""
    store = dm.store
    if store.config_path.exists() or store.providers_path.exists():
        print(f"配置文件已存在: {store.dir}")
        print("如需重新生成模板,请先备份并删除相关文件,再运行 `llmport config init`。")
        return
    store.init_first_run(config_template=True)
    print("已生成配置模板:")
    print(f"  {store.config_path}     (网关 + 模型映射 + llmport api_key, 0644)")
    print(f"  {store.providers_path}  (供应商 + 供应商 API key, 0600)")
    print("编辑这两个文件填入供应商和模型,然后运行 `llmport start`。")
    print("API key 直接写在 providers.yaml 的 provider 条目里(用 `llmport provider add` 更省事)。")


def _config_show(dm: DaemonManager) -> None:
    """Print config.yaml + providers.yaml (api_key masked) + key-status notes."""
    import re

    store = dm.store
    if not store.config_path.exists() and not store.providers_path.exists():
        print("尚无配置文件。运行 `llmport config init` 生成模板。")
        return

    def _mask(text: str) -> str:
        # Mask any ``api_key: <value>`` line: llmport's own key in config.yaml
        # and each provider's key in providers.yaml.
        return re.sub(r"(api_key:\s*).+", r"\1***", text)

    if store.config_path.exists():
        print(f"# === {store.config_path.name} (api_key 已打码) ===")
        text = store.config_path.read_text(encoding="utf-8")
        masked = _mask(text)
        print(masked, end="" if masked.endswith("\n") else "\n")

    if store.providers_path.exists():
        print()
        print(f"# === {store.providers_path.name} (api_key 已打码) ===")
        text = store.providers_path.read_text(encoding="utf-8")
        masked = _mask(text)
        print(masked, end="" if masked.endswith("\n") else "\n")

    # Best-effort: annotate which providers have a key set. The key lives in
    # providers.yaml (masked above); this summarizes its presence per provider.
    try:
        pdata = store.load_providers_config()
    except Exception:
        return
    providers = pdata.get("providers", []) if isinstance(pdata, dict) else []
    if not providers:
        return
    print()
    print("# API key 状态:")
    for p in providers:
        pname = p.get("name", "")
        status = "已设置" if p.get("api_key") else "未设置"
        print(f"#   {pname}: {status}")


def _config_edit(dm: DaemonManager, target: str = "config") -> None:
    """Open config.yaml (or providers.yaml) in $EDITOR (default vi)."""
    store = dm.store
    path = store.providers_path if target == "providers" else store.config_path
    if not path.exists():
        print("尚无配置文件。运行 `llmport config init` 生成模板。")
        return
    editor = os.environ.get("EDITOR") or "vi"
    subprocess.call([editor, str(path)])


# ============================================================================
# api-key (llmport's own API key; client->gateway auth)
# ============================================================================


def _api_key_set(dm: DaemonManager) -> None:
    """Interactively set llmport's API key (no echo)."""
    store = dm.store
    _ensure_store_init(store)
    try:
        key = getpass.getpass("llmport API key (输入不回显): ").strip()
    except (EOFError, OSError):
        print("无法交互读取（非交互式环境）。请直接编辑 config.yaml 的 api_key 字段。")
        return
    if not key:
        print("未输入 key，已取消。")
        return
    store.set_api_key(key)
    _apply_if_running(dm)
    print("已设置 llmport API key。客户端请求需携带其一：")
    print("  OpenAI SDK:    Authorization: Bearer <key>  (或设 OPENAI_API_KEY)")
    print("  Anthropic SDK: x-api-key: <key>              (或设 ANTHROPIC_API_KEY)")


def _api_key_show(dm: DaemonManager, reveal: bool = False) -> None:
    """Show whether llmport's API key is set (masked unless --reveal)."""
    key = dm.store.load_api_key()
    if not key:
        print("未设置 API key（网关不强制鉴权，纯 loopback）。")
        return
    if reveal:
        print(key)
    else:
        masked = (key[:3] + "***" + key[-2:]) if len(key) > 5 else "***"
        print(f"已设置 API key: {masked}  (用 --reveal 查看明文)")


def _api_key_clear(dm: DaemonManager) -> None:
    """Remove llmport's API key (back to loopback-only, no auth)."""
    store = dm.store
    if not store.load_api_key():
        print("未设置 API key，无需清除。")
        return
    store.clear_api_key()
    _apply_if_running(dm)
    print("已清除 llmport API key（网关回到纯 loopback 无鉴权）。")


# ============================================================================
# daemon control
# ============================================================================


def _cmd_start(dm: DaemonManager, host: str | None = None, port: int | None = None) -> None:
    # Migrate any prior two-file layout before reading (start doesn't otherwise
    # init), so an upgrade doesn't silently drop models configured in the old
    # models.yaml.
    dm.store.migrate_layout()
    # Refuse to start with no providers configured.
    pdata = _safe_load_providers(dm.store)
    if not pdata or not pdata.get("providers"):
        print("尚未配置供应商。请先运行: llmport provider add --name <name>")
        return
    for w in _validate_providers_config(pdata, dm.store.load_gateway()):
        print(f"警告: {w}")
    mdata = _safe_load_models(dm.store)
    if mdata:
        for w in _validate_models_config(mdata):
            print(f"警告: {w}")
    if dm.is_running():
        print(f"Gateway already running on {_url(dm)}")
        return
    if dm.start(host=host, port=port):
        print(f"Gateway started on {_url(dm)}")
        print("  /openai/v1/*    -> OpenAI protocol")
        print("  /anthropic/v1/* -> Anthropic protocol")
        print("  /health         -> liveness probe")
    else:
        print("Gateway failed to start. Check that the configured port is free.")


def _cmd_stop(dm: DaemonManager) -> None:
    if not dm.is_running():
        print("Gateway is not running.")
        return
    dm.stop()
    print("Gateway stopped.")


def _cmd_restart(dm: DaemonManager, host: str | None = None, port: int | None = None) -> None:
    if dm.is_running():
        if dm.restart(host=host, port=port):
            print(f"Gateway restarted on {_url(dm)}")
        else:
            print("Gateway failed to restart. Check that the configured port "
                  "is free and providers.yaml is valid.")
    else:
        if dm.start(host=host, port=port):
            print(f"Gateway started on {_url(dm)}")
        else:
            print("Gateway failed to start. Check that the configured port "
                  "is free and providers.yaml is valid.")


def _cmd_status(dm: DaemonManager) -> None:
    if not dm.is_running():
        print("Gateway is not running.")
        return
    status = dm.get_status()
    if not status.get("running"):
        print("Gateway is not running.")
        return

    gw = dm.store.load_gateway()
    host = gw.get("host", "127.0.0.1")
    port = dm.get_control_port() or gw.get("port", 11434)
    print(f"Gateway running on http://{host}:{port}")
    print("  /openai/v1/*    -> OpenAI")
    print("  /anthropic/v1/* -> Anthropic")
    print("  /health         -> liveness probe")
    print()

    # Models / providers are read from the local config files; /health only
    # reports liveness, so the HTTP probe carries no config or stats.
    model_names = _local_model_names(dm.store)
    if model_names:
        print(f"  Models ({len(model_names)}): {', '.join(model_names)}")
    else:
        print("  Models: none configured")

    provider_names = _local_provider_names(dm.store)
    if provider_names:
        print(f"  Providers ({len(provider_names)}): {', '.join(provider_names)}")
    else:
        print("  Providers: none configured")

    started_at = dm.started_at()
    if started_at:
        print(f"  Uptime: {_fmt_uptime(time.time() - started_at)}")


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


def _safe_load_providers(store) -> dict | None:
    """Load providers config. Return None only if it doesn't exist yet.

    A corrupt/unreadable file is NOT silently replaced with the default --
    that would destroy the user's existing providers on the next
    ``provider add`` / ``setup``. Abort with a message instead so the user
    can fix or back up the file.
    """
    try:
        return store.load_providers_config()
    except FileNotFoundError:
        return None
    except Exception as e:
        # Corrupt YAML, or valid-YAML-but-not-a-dict (load_providers_config
        # raises ValueError for the latter). Refuse rather than fall back to
        # the empty default, which would overwrite the user's existing config.
        print(f"配置文件 {store.providers_path} 无法解析: {e}")
        print("已中止,不会覆盖现有配置。请修复后重试,或备份后删除该文件重新配置。")
        raise SystemExit(1)


def _safe_load_models(store) -> dict | None:
    """Load models config. Return None only if it doesn't exist yet.

    A corrupt file aborts rather than being silently replaced.
    """
    try:
        return store.load_models_config()
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"配置文件 {store.config_path} 无法解析: {e}")
        print("已中止,不会覆盖现有配置。请修复后重试,或备份后删除该文件重新配置。")
        raise SystemExit(1)


def _local_model_names(store) -> list[str]:
    """Model public names from config.yaml (for status display).

    Lenient: returns [] if the file is missing or unreadable so a corrupt
    config never crashes ``llmport status``.
    """
    from llmport.models.model import parse_models_config
    try:
        cfg = store.load_config()
    except (FileNotFoundError, ValueError):
        return []
    return [m.name for m in parse_models_config(cfg.get("models") or {})]


def _local_provider_names(store) -> list[str]:
    """Provider names from providers.yaml (for status display)."""
    try:
        pdata = store.load_providers_config()
    except (FileNotFoundError, ValueError):
        return []
    return [p.get("name") for p in pdata.get("providers", []) if p.get("name")]


def _validate_providers_config(pdata, gateway=None) -> list[str]:
    """Return human-readable warnings about malformed provider entries.

    *gateway* (``{"host", "port"}``) supplies the gateway address for the
    self-loop check; the caller reads it from ``config.yaml``.

    The parser (``ProviderConfig.from_dict``) tolerates missing fields by
    degrading instead of crashing, so a hand-edit typo can leave a provider
    inert. This surfaces such cases at ``llmport start`` time -- in the
    user's terminal, not the detached daemon's discarded stderr.
    """
    if not isinstance(pdata, dict):
        return []
    from llmport.config.validation import validate_provider_base_url

    gw = gateway or {}
    gw_host = gw.get("host", "127.0.0.1")
    gw_port = int(gw.get("port", 11434))

    warnings: list[str] = []
    for p in pdata.get("providers", []):
        if not p.get("name"):
            warnings.append("供应商条目缺少 name 字段，将被忽略")
        elif not p.get("base_url"):
            warnings.append(f"供应商 {p['name']} 缺少 base_url，无法转发")
        else:
            try:
                validate_provider_base_url(p["base_url"], gw_host, gw_port)
            except ValueError as e:
                warnings.append(
                    f"供应商 {p['name']} 的 base_url 被拒绝（运行时跳过）: {e}")
    return warnings


def _validate_models_config(mdata) -> list[str]:
    """Return human-readable warnings about malformed model bindings.

    ``parse_models_config`` tolerates missing fields by skipping, so a
    hand-edit typo can silently drop a model. This surfaces it at start time.
    """
    if not isinstance(mdata, dict):
        return []
    from llmport.models.model import parse_models_config

    models = mdata.get("models", {})
    parsed_names = {m.name for m in parse_models_config(models)}
    warnings: list[str] = []
    if isinstance(models, dict):
        for name in models:
            if name not in parsed_names:
                warnings.append(
                    f"模型 {name} 的 binding 缺字段（provider），将被忽略"
                )
    return warnings


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    return f"{s // 3600}h{(s % 3600) // 60}m"
