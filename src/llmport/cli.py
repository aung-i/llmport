"""CLI entry point for llmport."""

import argparse
import getpass

from llmport.daemon import DaemonManager, run_daemon

__version__ = "0.1.0"

_DEFAULT_CFG = {
    "version": 1,
    "gateway": {"host": "127.0.0.1", "port": 11434},
    "providers": [],
    "models": [],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llmport",
        description="Terminal LLM API Gateway - a local multi-provider routing proxy.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: run as the gateway daemon
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit",
    )
    sub = parser.add_subparsers(dest="action", metavar="<command>")

    sub.add_parser("setup", help="交互式配置供应商和模型")
    sub.add_parser("start", help="启动网关（需先配置供应商）")
    sub.add_parser("stop", help="停止网关")
    sub.add_parser("restart", help="重启网关")
    sub.add_parser("status", help="查看网关状态")
    sub.add_parser("tui", help="打开 TUI")

    _add_provider_parser(sub)
    _add_model_parser(sub)

    args = parser.parse_args()

    if args.daemon:
        run_daemon()
        return

    if args.action is None:
        parser.print_help()
        return

    if args.action == "tui":
        from llmport.app import LlmPortApp
        LlmPortApp().run()
        return

    dm = DaemonManager()

    if args.action == "setup":
        _cmd_setup(dm)
    elif args.action == "start":
        _cmd_start(dm)
    elif args.action == "stop":
        _cmd_stop(dm)
    elif args.action == "restart":
        _cmd_restart(dm)
    elif args.action == "status":
        _cmd_status(dm)
    elif args.action == "provider":
        _cmd_provider(dm, args)
    elif args.action == "model":
        _cmd_model(dm, args)


# ============================================================================
# provider / model subcommand parsers
# ============================================================================


def _add_provider_parser(sub) -> None:
    p = sub.add_parser("provider", help="管理供应商（API key 加密存储）")
    ps = p.add_subparsers(dest="provider_action", metavar="<subcommand>")
    a = ps.add_parser("add", help="添加/更新供应商")
    a.add_argument("--id", required=True, help="供应商 ID，如 anthropic")
    a.add_argument("--name", default=None, help="显示名称（默认同 id）")
    a.add_argument("--protocol", choices=["openai", "anthropic"], default="openai")
    a.add_argument("--base-url", default=None, help="留空则按 protocol 取默认")
    a.add_argument(
        "--api-key", default=None,
        help="API key；不传时：新增供应商则交互输入（不回显），更新供应商则保留原 key",
    )
    ps.add_parser("list", help="列出已配置的供应商")
    r = ps.add_parser("remove", help="删除供应商")
    r.add_argument("id", help="供应商 ID")


def _add_model_parser(sub) -> None:
    p = sub.add_parser("model", help="管理模型映射（公开名 -> 供应商模型）")
    ms = p.add_subparsers(dest="model_action", metavar="<subcommand>")
    a = ms.add_parser("add", help="添加/更新模型")
    a.add_argument("--name", required=True, help="公开名（客户端请求时填的 model）")
    a.add_argument("--provider", required=True, help="供应商 ID")
    a.add_argument("--upstream", default=None, help="供应商的真实模型名（默认同 name）")
    ms.add_parser("list", help="列出已配置的模型")
    r = ms.add_parser("remove", help="删除模型")
    r.add_argument("name", help="模型公开名")


# ============================================================================
# setup
# ============================================================================


def _cmd_setup(dm: DaemonManager) -> None:
    """Interactive setup: create the config template and add providers/models."""
    store = dm.store
    _ensure_store_init(store)

    cfg = _safe_load_config(store) or dict(_DEFAULT_CFG)
    providers = cfg.get("providers", [])
    models = cfg.get("models", [])
    secrets = store.load_secrets()
    added = False

    print("llmport 设置")
    print(f"配置目录: {store.dir}")
    print("API key 只存到加密的 secrets.enc,不写进 config.yaml。")
    print()

    # Providers
    print("== 供应商 ==")
    while True:
        p = _prompt_provider()
        if p is None:
            break
        api_key = p.pop("_api_key")
        providers = [x for x in providers if x["id"] != p["id"]] + [p]
        if api_key:
            secrets[p["id"]] = api_key
        added = True
        if not _ask_yes_no("再添加一个供应商?", default=False):
            break

    # Models
    if providers:
        print()
        print("== 模型 ==")
        provider_ids = [p["id"] for p in providers]
        while True:
            m = _prompt_model(provider_ids)
            if m is None:
                break
            models = [x for x in models if x["name"] != m["name"]] + [m]
            added = True
            if not _ask_yes_no("再添加一个模型?", default=False):
                break

    if not added:
        print()
        print("未做修改。config.yaml 保留模板,可手编辑后运行 `llmport setup`,或用 "
              "`llmport provider add` / `llmport model add`。")
        return

    cfg["providers"] = providers
    cfg["models"] = models
    store.save_config(cfg)
    store.save_secrets(secrets)

    print()
    print(f"已保存: {store.config_path}")
    print(f"供应商 {len(providers)} 个,模型 {len(models)} 个。")
    print("运行 `llmport start` 启动网关。")


def _prompt_provider() -> dict | None:
    """Prompt for one provider. Returns None if the user skips."""
    pid = _input("供应商 ID (如 anthropic,留空跳过): ")
    if not pid:
        return None
    name = _input(f"显示名称 [{pid}]: ") or pid
    protocol = (_input("协议 openai/anthropic [openai]: ") or "openai").lower()
    if protocol not in ("openai", "anthropic"):
        protocol = "openai"
    default_url = ("https://api.openai.com/v1" if protocol == "openai"
                   else "https://api.anthropic.com")
    base_url = _input(f"base_url [{default_url}]: ") or default_url
    api_key = _input("API key (留空则不设): ")
    return {
        "id": pid,
        "name": name,
        "protocol": protocol,
        "base_url": base_url,
        "_api_key": api_key,
    }


def _prompt_model(provider_ids: list[str]) -> dict | None:
    """Prompt for one model mapping. Returns None if the user skips."""
    name = _input("模型公开名 (客户端请求时填,如 claude-sonnet;留空跳过): ")
    if not name:
        return None
    default_p = provider_ids[0]
    p = _input(f"供应商 ({'/'.join(provider_ids)}) [{default_p}]: ") or default_p
    upstream = _input(f"供应商的真实模型名 [{name}]: ") or name
    return {"name": name, "provider": p, "upstream": upstream}


def _input(prompt: str) -> str:
    """Wrapper around input() so tests can monkeypatch it."""
    return input(prompt).strip()


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    raw = _input(prompt + suffix).lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ============================================================================
# provider add / list / remove
# ============================================================================


def _cmd_provider(dm: DaemonManager, args) -> None:
    if args.provider_action == "add":
        _provider_add(dm, args)
    elif args.provider_action == "list":
        _provider_list(dm)
    elif args.provider_action == "remove":
        _provider_remove(dm, args.id)
    else:
        print("用法: llmport provider {add|list|remove}")


def _provider_add(dm: DaemonManager, args) -> None:
    """Add or update a provider. The API key is encrypted into secrets.enc.

    Updating an existing provider without ``--api-key`` preserves the
    existing key; only a brand-new provider prompts for one.
    """
    from llmport.gateway.ip_utils import validate_public_url

    store = dm.store
    _ensure_store_init(store)

    cfg = _safe_load_config(store) or dict(_DEFAULT_CFG)
    providers = cfg.get("providers", [])
    secrets = store.load_secrets()

    pid = args.id
    existing = next((p for p in providers if p["id"] == pid), None)

    base_url = args.base_url
    if not base_url:
        base_url = ("https://api.openai.com/v1" if args.protocol == "openai"
                    else "https://api.anthropic.com")
    if not validate_public_url(base_url):
        print(f"不允许使用内网/本地地址: {base_url}")
        return

    # Key resolution: explicit flag > prompt (new provider) > keep existing.
    api_key = args.api_key
    if api_key is None:
        if existing is None:
            api_key = getpass.getpass("API key (输入不回显,留空跳过): ").strip()
        else:
            api_key = secrets.get(pid, "")  # keep existing key on update

    name = args.name or pid
    providers = [p for p in providers if p["id"] != pid] + [{
        "id": pid, "name": name, "protocol": args.protocol, "base_url": base_url}]
    if api_key:
        secrets[pid] = api_key

    cfg["providers"] = providers
    store.save_config(cfg)
    store.save_secrets(secrets)

    if existing is None:
        print(f"已添加供应商 {pid} (API key {'已加密存储' if api_key else '未设置'})")
    else:
        if args.api_key:
            print(f"已更新供应商 {pid} (API key 已更新)")
        else:
            print(f"已更新供应商 {pid} (API key 保留原值)")


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


# ============================================================================
# model add / list / remove
# ============================================================================


def _cmd_model(dm: DaemonManager, args) -> None:
    if args.model_action == "add":
        _model_add(dm, args)
    elif args.model_action == "list":
        _model_list(dm)
    elif args.model_action == "remove":
        _model_remove(dm, args.name)
    else:
        print("用法: llmport model {add|list|remove}")


def _model_add(dm: DaemonManager, args) -> None:
    store = dm.store
    _ensure_store_init(store)

    cfg = _safe_load_config(store) or dict(_DEFAULT_CFG)
    provider_ids = [p["id"] for p in cfg.get("providers", [])]
    if args.provider not in provider_ids:
        print(f"未知供应商 {args.provider}。先运行: llmport provider add --id {args.provider}")
        return

    upstream = args.upstream or args.name
    models = cfg.get("models", [])
    existed = any(m.get("name") == args.name for m in models)
    models = [m for m in models if m.get("name") != args.name]
    models.append({"name": args.name, "provider": args.provider, "upstream": upstream})
    cfg["models"] = models
    store.save_config(cfg)
    print(f"{'已更新' if existed else '已添加'}模型 {args.name} -> {args.provider}/{upstream}")


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


# ============================================================================
# daemon control
# ============================================================================


def _cmd_start(dm: DaemonManager) -> None:
    # Require setup first: refuse to start with no providers configured.
    cfg = _safe_load_config(dm.store)
    if not cfg or not cfg.get("providers"):
        print("尚未配置供应商。请先运行: llmport setup")
        return
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
        dm.restart()
        print(f"Gateway restarted on {_url(dm)}")
    else:
        if dm.start():
            print(f"Gateway started on {_url(dm)}")
        else:
            print("Gateway failed to start.")


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
    """Make sure the config dir, key, template config, and vault exist."""
    from llmport.config.crypto import generate_key

    store.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not store.key_path.exists():
        store.key_path.write_bytes(generate_key())
        store.key_path.chmod(0o600)
    if not store.config_path.exists():
        store.write_config_template()
    if not store.secrets_path.exists():
        store.save_secrets({})


def _url(dm: DaemonManager) -> str:
    port = dm.get_control_port()
    if port:
        return f"http://127.0.0.1:{port}"
    return "http://127.0.0.1:11434"


def _safe_load_config(store) -> dict | None:
    """Load config, returning None if it doesn't exist or can't be read."""
    try:
        return store.load_config()
    except Exception:
        return None


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    return f"{s // 3600}h{(s % 3600) // 60}m"
