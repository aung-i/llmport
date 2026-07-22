"""Main Textual application for llmgate."""

import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static, Footer, TabbedContent, TabPane
from textual.binding import Binding

from llmgate.daemon import DaemonManager
from llmgate.ui.screens.models import ModelsPane
from llmgate.ui.screens.providers import ProvidersPane
from llmgate.ui.screens.gateway import GatewayPane
from llmgate.ui.screens.stats import StatsPane
from llmgate.ui.screens.settings import SettingsPane
from llmgate.ui.screens.onboarding import OnboardingScreen


class LlmGateApp(App):
    """Main llmgate TUI application."""

    TITLE = "llmgate"
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
        self.daemon = DaemonManager()

    def on_mount(self) -> None:
        # Check if first run
        config_dir = os.environ.get(
            "XDG_CONFIG_HOME",
            os.path.join(Path.home(), ".config"),
        )
        key_path = Path(config_dir) / "llmgate" / "key"
        if not key_path.exists():
            self.push_screen(OnboardingScreen())

        # Ensure daemon is running
        if not self.daemon.is_running():
            self.daemon.start()

    def compose(self) -> ComposeResult:
        with Horizontal(id="app-header"):
            yield Static("llmgate", classes="")
            yield Static("v0.1.0", classes="version")
        with TabbedContent():
            with TabPane("⚙ 网关", id="gateway"):
                yield GatewayPane()
            with TabPane("\U0001f916 模型", id="models"):
                yield ModelsPane()
            with TabPane("\U0001f310  供应商", id="providers"):
                yield ProvidersPane()
            with TabPane("\U0001f4ca 统计", id="stats"):
                yield StatsPane()
            with TabPane("\U0001f527 设置", id="settings"):
                yield SettingsPane()
        yield Footer()
