"""Statistics tab — usage data."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class StatsPane(Vertical):
    """Usage statistics panel."""

    def compose(self) -> ComposeResult:
        yield Static("使用统计", id="stats-title")
        yield Static("统计数据将在 v2 中可用", id="stats-placeholder")

    async def on_mount(self) -> None:
        self.set_interval(10.0, self.refresh_stats)
        await self.refresh_stats()

    async def refresh_stats(self) -> None:
        daemon = self.app.daemon  # type: ignore
        status = await daemon.async_get_status()
        if status.get("running"):
            self.query_one("#stats-placeholder", Static).update(
                f"请求数: {status.get('request_count', 0)}\n"
                f"供应商: {status.get('provider_count', 0)}\n"
                f"模型: {status.get('model_count', 0)}\n\n"
                f"详细统计将在后续版本中提供。"
            )
