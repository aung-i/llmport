"""Statistics tab — usage data."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from llmport.ui.widgets import Card, Section


class StatsPane(Vertical):
    """Usage statistics panel."""

    def compose(self) -> ComposeResult:
        with Section("使用统计"):
            yield Static("[dim]加载中...[/]", id="stats-content")
        with Card("请求趋势"):
            yield Static("[dim]统计数据将在后续版本中提供[/]", id="stats-chart")

    async def on_mount(self) -> None:
        self.set_interval(10.0, self.refresh_stats)
        await self.refresh_stats()

    async def refresh_stats(self) -> None:
        daemon = self.app.daemon  # type: ignore
        status = await daemon.async_get_status()
        if not status.get("running"):
            self.query_one("#stats-content", Static).update("[dim]网关未运行[/]")
            return

        self.query_one("#stats-content", Static).update(
            f"  [bold $primary]{status.get('request_count', 0)}[/]      "
            f"[bold $primary]{status.get('provider_count', 0)}[/]      "
            f"[bold $primary]{status.get('model_count', 0)}[/]\n"
            f"  [dim]请求数         供应商         模型[/]"
        )
