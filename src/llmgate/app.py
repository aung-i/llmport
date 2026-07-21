"""Main Textual application for llmgate."""

import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane
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
    TabbedContent {
        border: none;
    }
    TabPane {
        border: none;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("left", "focus_previous", "Prev Tab", show=False),
        Binding("right", "focus_next", "Next Tab", show=False),
        Binding("ctrl+q", "quit", "Quit"),
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
        yield Header()
        with TabbedContent():
            with TabPane("模型", id="models"):
                yield ModelsPane()
            with TabPane("供应商", id="providers"):
                yield ProvidersPane()
            with TabPane("网关", id="gateway"):
                yield GatewayPane()
            with TabPane("统计", id="stats"):
                yield StatsPane()
            with TabPane("设置", id="settings"):
                yield SettingsPane()
        yield Footer()
