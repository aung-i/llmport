"""First-run onboarding wizard."""

import httpx

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Container
from textual.widgets import Static, Button

from llmgate.config.store import ConfigStore


async def async_get_json(url: str) -> dict | list | None:
    """Helper to fetch JSON from the control API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None


class OnboardingScreen(ModalScreen):
    """First-run setup wizard."""

    CSS = """
    OnboardingScreen {
        align: center middle;
        background: rgba(15, 17, 25, 0.85);
    }
    #onboard-container {
        width: 48;
        height: auto;
        border: double $primary;
        background: $surface;
        padding: 2 3;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="onboard-container"):
            yield Static("[bold $primary]llmgate[/] — Terminal LLM API Gateway", id="onboard-title")
            yield Static("")
            yield Static("检测到你是第一次使用。")
            yield Static("")
            yield Static("[dim]  1. 创建加密密钥 — 本地安全存储 API Key[/]")
            yield Static("[dim]  2. 添加供应商 — 支持 OpenAI / Anthropic[/]")
            yield Static("[dim]  3. 配置模型别名 — 多供应商路由和自动 fallback[/]")
            yield Static("")
            yield Button(" 开始设置", id="btn-start-setup", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start-setup":
            store = ConfigStore()
            store.init_first_run()
            self.app.notify("配置已初始化，请在供应商页添加你的第一个 Provider", title="完成")  # type: ignore
            self.dismiss()
