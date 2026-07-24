"""Main Textual application for llmport."""

try:
    import importlib.metadata as _metadata
    __version__ = _metadata.version("llmport")
except Exception:
    __version__ = "unknown"

from typing import cast

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane
from textual.binding import Binding

from llmport.ui.theme import llmport_theme

from llmport.daemon import DaemonManager
from llmport.ui.screens.models import ModelsPane
from llmport.ui.screens.providers import ProvidersPane
from llmport.ui.screens.gateway import GatewayPane
from llmport.ui.screens.stats import StatsPane
from llmport.ui.screens.settings import SettingsPane
from llmport.ui.screens.onboarding import OnboardingScreen


class LlmPortApp(App):
    """Main llmport TUI application."""

    TITLE = "llmport"
    SUB_TITLE = "LLM API Gateway"

    CSS = """
    Screen { background: $background; }
    Header { dock: top; }
    Footer { dock: bottom; }
    .status-up { color: $success; }
    .status-degraded { color: $warning; }
    .status-down { color: $error; }
    #empty-state {
        text-align: center;
        color: $text-muted;
        text-style: italic;
        padding: 2 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "screenshot", "", show=False),
        Binding("f9", "maximize", "", show=False),
        Binding("ctrl+t", "toggle_dark", "", show=False),
        Binding("ctrl+1", "switch_tab('models')", "模型", show=True),
        Binding("ctrl+2", "switch_tab('providers')", "供应商", show=True),
        Binding("ctrl+3", "switch_tab('gateway')", "网关", show=True),
        Binding("ctrl+4", "switch_tab('stats')", "统计", show=True),
        Binding("ctrl+5", "switch_tab('settings')", "设置", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.daemon: DaemonManager = DaemonManager()
        self.register_theme(llmport_theme)
        self.theme = "llmport"

    async def on_mount(self) -> None:
        """On mount: check for first-run, push onboarding if needed.

        The daemon is NOT auto-started here.  It is started either by the
        onboarding completion flow or when the user explicitly starts it
        via the gateway tab.
        """
        from llmport.config.store import ConfigStore

        store = ConfigStore()
        first_run = False
        try:
            data = store.load()
            if not data.get("providers"):
                first_run = True
        except Exception:
            first_run = True

        if first_run:
            self.push_screen(OnboardingScreen(), self._on_onboarding_done)

    async def _on_onboarding_done(self, _result=None) -> None:
        """Called when the onboarding screen is dismissed.

        Starts the daemon and refreshes the UI.
        """
        if not self.daemon.is_running():
            self.daemon.start()
            import asyncio
            await asyncio.sleep(1.0)
        try:
            await self.query_one(ModelsPane).refresh_models()
        except Exception:
            pass

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to the specified tab by id."""
        self.query_one(TabbedContent).active = tab_id

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent():
            with TabPane("\U0001f916  模型", id="models"):
                yield ModelsPane()
            with TabPane("\U0001f310  供应商", id="providers"):
                yield ProvidersPane()
            with TabPane("⚙  网关", id="gateway"):
                yield GatewayPane()
            with TabPane("\U0001f4ca  统计", id="stats"):
                yield StatsPane()
            with TabPane("\U0001f527  设置", id="settings"):
                yield SettingsPane()
        yield Footer()
