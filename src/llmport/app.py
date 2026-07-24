"""Main Textual application for llmport."""

try:
    import importlib.metadata as _metadata
    __version__ = _metadata.version("llmport")
except Exception:
    __version__ = "unknown"

from typing import cast

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static, Footer, TabbedContent, TabPane
from textual.binding import Binding

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
    /* ===== Theme ===== */
    $primary: #00d4aa;
    $primary-darken-1: #00b894;
    $primary-darken-2: #0d7377;
    $secondary: #7c3aed;
    $secondary-lighten-1: #a78bfa;
    $accent: #06b6d4;
    $success: #22c55e;
    $warning: #f59e0b;
    $error: #ef4444;
    $surface: #1a1b2e;
    $surface-darken-1: #141525;
    $panel: #212340;
    $text: #e2e8f0;
    $text-muted: #64748b;

    Screen {
        background: $surface-darken-1;
    }

    /* ===== Header ===== */
    #app-header {
        background: $surface;
        color: $primary;
        text-style: bold;
        dock: top;
        height: 3;
        border-bottom: solid $primary-darken-2;
        padding: 0 2;
    }
    #app-header > Static {
        color: $primary;
        text-style: bold;
    }
    #app-header > .version {
        color: $text-muted;
        text-style: none;
    }

    /* ===== Footer ===== */
    Footer {
        background: $surface;
        color: $text-muted;
        dock: bottom;
        height: 1;
        border-top: solid $primary-darken-2;
    }
    Footer > .footer--highlight {
        color: $primary;
    }
    Footer > .footer--key {
        color: $text;
        text-style: bold;
    }

    /* ===== Tabs ===== */
    TabbedContent {
        border: none;
        padding: 0 1;
    }
    TabbedContent > .tabs {
        background: $surface;
        border-bottom: solid $primary-darken-2;
        padding: 0 1;
    }
    TabbedContent Tab {
        background: $surface;
        color: $text-muted;
        padding: 1 2;
        border: none;
    }
    TabbedContent Tab.-active {
        color: $primary;
        text-style: bold;
        border-bottom: solid $primary;
    }
    TabbedContent Tab:focus {
        color: $primary;
    }
    TabbedContent Tab:hover {
        color: $primary;
    }
    TabPane {
        border: none;
        padding: 1;
        background: $surface-darken-1;
    }

    /* ===== Common ===== */
    Label {
        color: $text;
    }
    Static {
        color: $text;
    }

    .dim {
        color: $text-muted;
        text-opacity: 70;
    }
    .accent {
        color: $primary;
        text-style: bold;
    }
    .title {
        text-style: bold;
        color: $primary;
    }
    .heading {
        text-style: bold;
        color: $secondary-lighten-1;
        padding: 1 0;
    }
    .separator {
        color: $primary-darken-2;
    }
    .empty {
        color: $text-muted;
        text-style: italic;
        text-align: center;
        padding: 2;
    }

    /* ===== Button ===== */
    Button {
        margin: 1 0;
    }
    Button:hover {
        border: solid $primary;
    }

    /* ===== Input ===== */
    Input {
        margin: 0;
        background: $surface-darken-1;
        border: solid $primary-darken-2;
        color: $text;
    }
    Input:focus {
        border: solid $primary;
    }

    /* ===== Select ===== */
    Select {
        margin: 0;
        background: $surface-darken-1;
        border: solid $primary-darken-2;
        color: $text;
    }
    Select:focus {
        border: solid $primary;
    }

    /* ===== ListView ===== */
    ListView {
        background: $surface;
        border: solid $primary-darken-2;
        padding: 1 0;
    }
    ListView:focus {
        border: solid $primary;
    }
    ListView > ListItem {
        padding: 0 2;
        color: $text;
    }
    ListView > ListItem.-highlight {
        background: $panel;
        color: $primary;
        text-style: bold;
    }

    /* ===== Status colors ===== */
    .status-up { color: $success; }
    .status-degraded { color: $warning; }
    .status-down { color: $error; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+p", "screenshot", "", show=False),
        Binding("f9", "maximize", "", show=False),
        Binding("ctrl+t", "toggle_dark", "", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.daemon: DaemonManager = DaemonManager()

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

    def compose(self) -> ComposeResult:
        with Horizontal(id="app-header"):
            yield Static("llmport", classes="")
            yield Static(f"v{__version__}", classes="version")
        with TabbedContent():
            with TabPane("\U0001f916 模型", id="models"):
                yield ModelsPane()
            with TabPane("\U0001f310 供应商", id="providers"):
                yield ProvidersPane()
            with TabPane("⚙ 网关", id="gateway"):
                yield GatewayPane()
            with TabPane("\U0001f4ca 统计", id="stats"):
                yield StatsPane()
            with TabPane("\U0001f527 设置", id="settings"):
                yield SettingsPane()
        yield Footer()
