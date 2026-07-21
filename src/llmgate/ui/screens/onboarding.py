"""First-run onboarding wizard."""

import httpx
from pathlib import Path
import os

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
    }
    #onboard-container {
        width: 50;
        height: 20;
        border: thick $success;
        background: $surface;
        padding: 2 3;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="onboard-container"):
            yield Static("🎉 欢迎使用 llmgate!")
            yield Static("")
            yield Static("检测到你是第一次使用。")
            yield Static("我们将为你:")
            yield Static("  1. 创建加密密钥")
            yield Static("  2. 引导添加第一个供应商")
            yield Static("  3. 启动网关")
            yield Static("")
            yield Button("开始设置", id="btn-start-setup", variant="primary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start-setup":
            store = ConfigStore()
            store.init_first_run()
            self.app.notify("配置已初始化，请在供应商页添加你的第一个 Provider", title="完成")  # type: ignore
            self.dismiss()
