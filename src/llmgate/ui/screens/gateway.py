"""Gateway tab — daemon status, health checks, start/stop."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Label, Button


class GatewayPane(Vertical):
    """Gateway status and control panel."""

    def compose(self) -> ComposeResult:
        yield Static("网关状态", id="gateway-status-title")
        yield Static("加载中...", id="gateway-info")
        yield Static("─" * 40)
        yield Static("供应商健康检查", id="health-title")
        yield Static("加载中...", id="health-list")
        yield Static("─" * 40)
        with Horizontal():
            yield Button("停止网关", id="btn-stop-gateway", variant="error")
            yield Button("重启网关", id="btn-restart-gateway", variant="warning")

    async def on_mount(self) -> None:
        self.set_interval(5.0, self.refresh_status)
        await self.refresh_status()

    async def refresh_status(self) -> None:
        daemon = self.app.daemon  # type: ignore
        status = await daemon.async_get_status()

        if not status.get("running", False):
            self.query_one("#gateway-info", Static).update("状态: 未运行")
            return

        uptime = status.get("uptime", 0)
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        info = (
            f"状态: ● 运行中\n"
            f"运行时长: {hours}h {minutes}m\n"
            f"活跃模型: {status.get('active_model', '无')}\n"
            f"请求数: {status.get('request_count', 0)}\n"
            f"供应商数: {status.get('provider_count', 0)}"
        )
        self.query_one("#gateway-info", Static).update(info)

        providers = status.get("providers", [])
        health_lines = []
        for p in providers:
            icon = {"up": "🟢", "degraded": "🟡", "down": "🔴"}.get(
                p.get("status", "unknown"), "⚪"
            )
            health_lines.append(f"  {icon} {p['name']} · {p.get('latency_ms', 0):.0f}ms")
        self.query_one("#health-list", Static).update("\n".join(health_lines) or "无供应商")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        daemon = self.app.daemon  # type: ignore
        if event.button.id == "btn-stop-gateway":
            daemon.stop()
            self.notify("网关已停止", title="网关")
            await self.refresh_status()
        elif event.button.id == "btn-restart-gateway":
            daemon.restart()
            self.notify("网关已重启", title="网关")
            await self.refresh_status()
