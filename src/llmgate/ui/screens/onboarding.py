"""First-run onboarding wizard — 3-step setup flow."""

import httpx

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.containers import Container, Horizontal
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
    """First-run setup wizard — 3 steps.

    Step 0: welcome screen, user clicks "开始设置" -> init_first_run.
    Step 1: ProviderFormScreen is pushed; when dismissed, advance to step 2.
    Step 2: finish screen, user clicks "开始使用" -> dismiss.
    """

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

    def __init__(self):
        super().__init__()
        self._step = 0

    def compose(self) -> ComposeResult:
        with Container(id="onboard-container"):
            yield Static(id="onboard-content")
            yield Static("")
            with Horizontal(id="onboard-buttons"):
                yield Button(" 开始设置", id="btn-start-setup", variant="primary")
                yield Button(" 开始使用", id="btn-finish", variant="primary")

    async def on_mount(self) -> None:
        await self._render_step()

    async def _render_step(self) -> None:
        content = self.query_one("#onboard-content", Static)
        self.query_one("#btn-start-setup").visible = self._step == 0
        self.query_one("#btn-finish").visible = self._step == 2

        if self._step == 0:
            content.update(
                "[bold $primary]llmgate[/] — Terminal LLM API Gateway\n\n"
                "检测到你是第一次使用。\n\n"
                "[dim]本向导将帮助你快速完成初始配置。[/]"
            )
        elif self._step == 2:
            content.update(
                "[bold $primary]设置完成！[/]\n\n"
                "你已成功添加供应商。现在可以开始使用了。"
            )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-start-setup":
            store = ConfigStore()
            store.init_first_run()
            self._step = 1
            # Import lazily to avoid circular dependency
            from llmgate.ui.screens.providers import ProviderFormScreen

            self.app.notify("配置已初始化，请添加你的第一个 Provider", title="第一步")  # type: ignore
            await self.app.push_screen(  # type: ignore
                ProviderFormScreen(self.app.daemon),  # type: ignore
                self._on_provider_done,
            )
        elif event.button.id == "btn-finish":
            self.dismiss()

    async def _on_provider_done(self, _result) -> None:
        self._step = 2
        await self._render_step()
