"""Gateway tab — daemon status, health checks, configuration."""

from typing import TYPE_CHECKING, cast

from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal, Container
from textual.widgets import Static, Button, Input, Label
from textual.screen import ModalScreen

from llmport.ui.widgets import Card, Section
from llmport.ui import async_get_json

if TYPE_CHECKING:
    from llmport.app import LlmPortApp


class GatewayConfigScreen(ModalScreen):
    """Modal for editing gateway port."""

    CSS = """
    GatewayConfigScreen {
        align: center middle;
    }
    #gw-config-container {
        width: 40;
        height: auto;
        border: solid $primary;
        background: $surface;
        padding: 2 3;
    }
    Input {
        width: 100%;
        margin: 1 0;
    }
    """

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    def compose(self) -> ComposeResult:
        with Container(id="gw-config-container"):
            yield Static("[bold $primary]网关地址[/]")
            yield Static("[dim]修改后自动重启[/]")
            yield Label("")
            yield Label("[dim]Host[/]")
            yield Input(
                value=str(self.config.get("host", "127.0.0.1")),
                placeholder="127.0.0.1",
                id="input-host",
            )
            yield Label("[dim]Port[/]")
            yield Input(
                value=str(self.config.get("port", 11434)),
                placeholder="11434",
                id="input-port",
            )
            yield Label("")
            with Horizontal():
                yield Button(" 保存并重启", id="btn-save-restart", variant="primary")
                yield Button(" 取消", id="btn-cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss()
            return

        if event.button.id == "btn-save-restart":
            import httpx
            _app = cast("LlmPortApp", self.app)
            daemon = _app.daemon
            port = daemon.get_control_port()
            if port is None:
                self.notify("网关未运行，无法保存配置", title="网关配置", severity="error")
                return
            new_host = self.query_one("#input-host", Input).value.strip()
            port_str = self.query_one("#input-port", Input).value.strip()
            try:
                new_port = int(port_str)
            except ValueError:
                self.notify("端口号必须是数字", title="网关配置", severity="error")
                return
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/api/gateway/config",
                    json={"host": new_host, "port": new_port},
                )
                result = resp.json()
                if not result.get("ok"):
                    self.notify(f"失败: {result.get('error', '')}", title="网关配置", severity="error")
                    return
                self.notify(f"地址已更新: {new_host}:{new_port}", title="网关配置")
            daemon.restart()
            self.dismiss()
            await cast("LlmPortApp", self.app).query_one(GatewayPane).refresh_status()


class GatewayPane(Vertical):
    """Gateway status and control panel."""

    DEFAULT_CSS = """
    GatewayPane {
        overflow-y: auto;
    }
    #gateway-actions {
        dock: bottom;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        with Section("网关状态"):
            yield Static("加载中...", id="gateway-status")
        with Card("接口地址"):
            yield Static("加载中...", id="gateway-endpoints")
        with Card(""):
            yield Static("加载中...", id="gateway-stats")
        with Section("供应商健康"):
            yield Static("加载中...", id="health-list")
        with Horizontal(id="gateway-actions"):
            yield Button(" 启动网关", id="btn-start-gateway", variant="success")
            yield Button(" 重启网关", id="btn-restart-gateway", variant="warning")
            yield Button(" 停止网关", id="btn-stop-gateway", variant="error")
            yield Button(" 端口配置", id="btn-config", variant="default")

    async def on_mount(self) -> None:
        self.set_interval(5.0, self.refresh_status)
        await self.refresh_status()

    async def refresh_status(self) -> None:
        _app = cast("LlmPortApp", self.app)
        daemon = _app.daemon
        status = await daemon.async_get_status()
        port = daemon.get_control_port()

        if not status.get("running", False):
            self.query_one("#gateway-status", Static).update("[red]● 网关未运行[/]")
            self.query_one("#gateway-endpoints", Static).update("[dim]等待启动...[/]")
            self.query_one("#gateway-stats", Static).update("[dim]等待网关启动...[/]")
            self.query_one("#health-list", Static).update("")
            self.query_one("#btn-start-gateway", Button).display = True
            self.query_one("#btn-stop-gateway", Button).display = False
            self.query_one("#btn-restart-gateway", Button).display = False
            return

        config = {"host": "127.0.0.1", "port": 11434}
        if port:
            data = await async_get_json(f"http://127.0.0.1:{port}/api/gateway/config")
            if data and isinstance(data, dict) and "host" in data:
                config = data

        uptime = status.get("uptime", 0)
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        base = f"http://{config['host']}:{config['port']}"

        self.query_one("#gateway-status", Static).update("[green]● 运行中[/]")
        self.query_one("#btn-start-gateway", Button).display = False
        self.query_one("#btn-stop-gateway", Button).display = True
        self.query_one("#btn-restart-gateway", Button).display = True

        self.query_one("#gateway-endpoints", Static).update(
            f"  [bold]{base}[/]\n"
            f"  [dim]/openai/v1/*    → OpenAI[/]\n"
            f"  [dim]/anthropic/v1/* → Anthropic[/]\n"
            f"  [dim]/api/*          → Control[/]"
        )

        self.query_one("#gateway-stats", Static).update(
            f"  [bold $primary]{hours}h {minutes}m[/]     "
            f"[bold $primary]{status.get('active_model') or '—'}[/]     "
            f"[bold $primary]{status.get('request_count', 0)}[/]     "
            f"[bold $primary]{status.get('provider_count', 0)}[/]\n"
            f"  [dim]运行时长         活跃模型         请求数         供应商[/]"
        )

        providers = status.get("providers", [])
        if not providers:
            self.query_one("#health-list", Static).update("[dim]暂无供应商[/]")
        else:
            lines = []
            for p in providers:
                icon = {"up": "🟢", "degraded": "🟡", "down": "🔴"}.get(
                    p.get("status", "unknown"), "⚪"
                )
                lat = p.get("latency_ms", 0)
                lines.append(f"  {icon} [bold]{p['name']}[/] · {lat:.0f}ms")
            self.query_one("#health-list", Static).update("\n".join(lines))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        daemon = cast("LlmPortApp", self.app).daemon
        if event.button.id == "btn-start-gateway":
            daemon.start()
            self.notify("网关已启动", title="网关")
            await self.refresh_status()
        elif event.button.id == "btn-stop-gateway":
            daemon.stop()
            self.notify("网关已停止", title="网关")
            await self.refresh_status()
        elif event.button.id == "btn-restart-gateway":
            daemon.restart()
            self.notify("网关已重启", title="网关")
            await self.refresh_status()
        elif event.button.id == "btn-config":
            port = daemon.get_control_port()
            config = {"host": "127.0.0.1", "port": 11434}
            if port:
                data = await async_get_json(f"http://127.0.0.1:{port}/api/gateway/config")
                if data and isinstance(data, dict) and "host" in data:
                    config = data
            await cast("LlmPortApp", self.app).push_screen(GatewayConfigScreen(config))
