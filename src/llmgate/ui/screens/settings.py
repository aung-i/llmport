"""Settings tab."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Button

from llmgate.ui.widgets import Card, Section


class SettingsPane(Vertical):
    """Settings panel."""

    def compose(self) -> ComposeResult:
        with Section("数据管理"):
            with Horizontal():
                yield Button(" 导出配置", id="btn-export", variant="default")
                yield Button(" 导入配置", id="btn-import", variant="default")
        with Section("关于"):
            yield Static("[bold $primary]llmgate[/]  [dim]v0.1.0[/]")
            yield Static("[dim]Terminal LLM API Gateway[/]")
            yield Static("")
            yield Static("[dim]多供应商路由 · 透明协议转发 · 故障自动切换[/]")
            yield Static("")
            yield Button(" 检查更新", id="btn-check-update", variant="default")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-export":
            self.notify("导出功能将在后续版本中提供", title="导出")
        elif event.button.id == "btn-import":
            self.notify("导入功能将在后续版本中提供", title="导入")
        elif event.button.id == "btn-check-update":
            self.notify("已经是最新版本", title="更新")
