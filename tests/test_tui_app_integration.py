"""LlmPortApp tab switching and pane-rendering integration tests.

Covers:
- 5 tab pane existence (models, providers, gateway, stats, settings)
- Tab switching (click each header, verify active pane)
- Pane key-widget rendering for all 5 tabs
- Screen push/pop (modal does not break tab state)
- Onboarding skip (pre-configured config -> main UI directly)
- Gateway not-running state (empty-state rendering)
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Static
from textual.widgets import TabbedContent, TabPane
from textual.widgets._tabbed_content import ContentTab


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _preconfigure_config(tmp_dir: str) -> None:
    """Create a pre-configured config in tmp_dir/llmport/ to skip onboarding."""
    from llmport.config.store import ConfigStore
    # ConfigStore() without config_dir uses XDG_CONFIG_HOME/llmport/
    store = ConfigStore(config_dir=os.path.join(tmp_dir, "llmport"))
    store.init_first_run()
    store.save({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {
                "id": "test-provider",
                "name": "Test Provider",
                "protocol": "openai",
                "base_url": "https://api.openai.com",
                "models": [{"name": "gpt-4", "aliases": []}],
            }
        ],
        "active_model": None,
    })


def _plain_text(widget: Static | Label) -> str:
    """Extract plain text from a Static or Label widget."""
    rendered = widget.render()
    if hasattr(rendered, "plain"):
        return rendered.plain
    return str(rendered)


# ---------------------------------------------------------------------------
# Fixture: mock daemon + pre-configured config
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_deps():
    """Patch DaemonManager to prevent real process management,
    and set up a pre-configured config dir to skip onboarding."""
    with (
        patch("llmport.app.DaemonManager") as mock_daemon_cls,
    ):
        mock_daemon = MagicMock()
        mock_daemon.get_control_port.return_value = None
        mock_daemon.async_get_status = AsyncMock(return_value={"running": False})
        mock_daemon_cls.return_value = mock_daemon

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            _preconfigure_config(tmp)

            yield {
                "tmp_dir": tmp,
                "mock_daemon": mock_daemon,
                "mock_daemon_cls": mock_daemon_cls,
            }


TAB_IDS = ["models", "providers", "gateway", "stats", "settings"]
TAB_LABELS = ["模型", "供应商", "网关", "统计", "设置"]

# ===================================================================
# TestLlMPortAppTabStructure
# ===================================================================

class TestLlmPortAppTabStructure:
    """Verify all 5 tabs are present with correct IDs and labels."""

    @pytest.mark.asyncio
    async def test_tabbed_content_exists(self, mock_deps):
        """TabbedContent is mounted on the main screen."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)
            assert tabbed is not None
            assert tabbed.display is True

    @pytest.mark.asyncio
    async def test_all_five_tab_panes_exist(self, mock_deps):
        """All 5 TabPane widgets exist with correct IDs."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            for pane_id in TAB_IDS:
                pane = app.query_one(f"#{pane_id}", TabPane)
                assert pane is not None, f"TabPane #{pane_id} should exist"

    @pytest.mark.asyncio
    async def test_tab_pane_titles(self, mock_deps):
        """Each TabPane has expected title text."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            for pane_id, expected_title in zip(TAB_IDS, TAB_LABELS):
                pane = app.query_one(f"#{pane_id}", TabPane)
                title_text = str(pane._title) if hasattr(pane, "_title") else ""
                assert expected_title in title_text, (
                    f"Pane #{pane_id} title should contain '{expected_title}', "
                    f"got '{title_text}'"
                )

    @pytest.mark.asyncio
    async def test_tab_headers_have_correct_ids(self, mock_deps):
        """Each tab header (ContentTab) has correct prefixed ID."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            for pane_id in TAB_IDS:
                tab_id = ContentTab.add_prefix(pane_id)
                tab = app.query_one(f"#{tab_id}")
                assert tab is not None, f"ContentTab #{tab_id} should exist"


# ===================================================================
# TestLlMPortAppTabSwitching
# ===================================================================

class TestLlmPortAppTabSwitching:
    """Clicking each tab header switches the active pane."""

    @pytest.mark.asyncio
    async def test_default_active_tab_is_models(self, mock_deps):
        """On mount, the models tab is active by default."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)
            assert tabbed.active == "models", (
                f"Expected active tab 'models', got '{tabbed.active}'"
            )

    @pytest.mark.asyncio
    async def test_switch_to_each_tab(self, mock_deps):
        """Clicking each tab header updates the active pane."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)

            for pane_id in TAB_IDS:
                tab_id = ContentTab.add_prefix(pane_id)
                await pilot.click(f"#{tab_id}")
                await pilot.pause()

                assert tabbed.active == pane_id, (
                    f"Expected active tab to be '{pane_id}' after clicking, "
                    f"got '{tabbed.active}'"
                )

    @pytest.mark.asyncio
    async def test_switch_back_and_forth(self, mock_deps):
        """Switching back and forth between tabs works correctly."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)

            # models -> providers -> models -> settings
            for pane_id in ["providers", "models", "settings", "gateway", "stats"]:
                tab_id = ContentTab.add_prefix(pane_id)
                await pilot.click(f"#{tab_id}")
                await pilot.pause()
                assert tabbed.active == pane_id

            # Quick cycle through all
            for pane_id in TAB_IDS:
                tab_id = ContentTab.add_prefix(pane_id)
                await pilot.click(f"#{tab_id}")
                await pilot.pause()
                assert tabbed.active == pane_id


# ===================================================================
# TestLlMPortAppPaneRendering
# ===================================================================

class TestLlmPortAppPaneRendering:
    """Each tab pane renders expected key widgets after switching."""

    @pytest.mark.asyncio
    async def test_models_pane_widgets(self, mock_deps):
        """Models pane has model-list ListView and model-search Input."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            # Switch to models tab
            await pilot.click(f"#{ContentTab.add_prefix('models')}")
            await pilot.pause()

            assert app.query_one("#model-list", ListView) is not None
            assert app.query_one("#model-search", Input) is not None
            assert app.query_one("#active-info", Static) is not None
            assert app.query_one("#btn-detail", Button) is not None
            assert app.query_one("#btn-add", Button) is not None

    @pytest.mark.asyncio
    async def test_providers_pane_widgets(self, mock_deps):
        """Providers pane has provider-list ListView."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('providers')}")
            await pilot.pause()

            assert app.query_one("#provider-list", ListView) is not None
            assert app.query_one("#btn-add-provider", Button) is not None
            assert app.query_one("#btn-delete-provider", Button) is not None

    @pytest.mark.asyncio
    async def test_gateway_pane_widgets(self, mock_deps):
        """Gateway pane has gateway-status Static and action buttons."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('gateway')}")
            await pilot.pause()

            assert app.query_one("#gateway-status", Static) is not None
            assert app.query_one("#btn-start-gateway", Button) is not None
            assert app.query_one("#btn-restart-gateway", Button) is not None
            assert app.query_one("#btn-stop-gateway", Button) is not None
            assert app.query_one("#btn-config", Button) is not None

    @pytest.mark.asyncio
    async def test_stats_pane_widgets(self, mock_deps):
        """Stats pane has stats-content Static."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('stats')}")
            await pilot.pause()

            assert app.query_one("#stats-content", Static) is not None
            assert app.query_one("#stats-chart", Static) is not None

    @pytest.mark.asyncio
    async def test_settings_pane_widgets(self, mock_deps):
        """Settings pane has export/import buttons and about section."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('settings')}")
            await pilot.pause()

            assert app.query_one("#btn-export", Button) is not None
            assert app.query_one("#btn-import", Button) is not None
            assert app.query_one("#btn-check-update", Button) is not None


# ===================================================================
# TestLlMPortAppScreenPushPop
# ===================================================================

class TestLlmPortAppScreenPushPop:
    """Modal screen push/dismiss does not break tab state."""

    @pytest.mark.asyncio
    async def test_modal_push_does_not_change_active_tab(self, mock_deps):
        """Pushing a modal screen preserves the active tab."""
        from llmport.app import LlmPortApp
        from textual.screen import ModalScreen
        from textual.app import ComposeResult
        from textual.containers import Container
        from textual.widgets import Static

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)
            assert tabbed.active == "models"

            # Push a test modal
            class TestModal(ModalScreen):
                def compose(self) -> ComposeResult:
                    with Container():
                        yield Static("test")

            await app.push_screen(TestModal())
            await pilot.pause()

            # Ensure modal is showing
            assert isinstance(app.screen, TestModal)

            # Dismiss the modal
            app.screen.dismiss()
            await pilot.pause()

            # Tab state should be preserved
            assert tabbed.active == "models", (
                f"Active tab should still be 'models' after modal, "
                f"got '{tabbed.active}'"
            )

    @pytest.mark.asyncio
    async def test_tab_state_after_multiple_modals(self, mock_deps):
        """Multiple modal pushes and pops don't corrupt tab state."""
        from llmport.app import LlmPortApp
        from textual.screen import ModalScreen
        from textual.app import ComposeResult
        from textual.containers import Container
        from textual.widgets import Static, Button

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)

            # Switch to providers tab
            await pilot.click(f"#{ContentTab.add_prefix('providers')}")
            await pilot.pause()
            assert tabbed.active == "providers"

            # Push modal A
            class ModalA(ModalScreen):
                def compose(self) -> ComposeResult:
                    with Container():
                        yield Static("A")

            await app.push_screen(ModalA())
            await pilot.pause()
            assert isinstance(app.screen, ModalA)

            # Push modal B on top
            class ModalB(ModalScreen):
                def compose(self) -> ComposeResult:
                    with Container():
                        yield Static("B")

            await app.push_screen(ModalB())
            await pilot.pause()
            assert isinstance(app.screen, ModalB)

            # Dismiss B
            app.screen.dismiss()
            await pilot.pause()

            # Should be back to ModalA
            assert isinstance(app.screen, ModalA)

            # Dismiss A
            app.screen.dismiss()
            await pilot.pause()

            # Tab should still be providers
            assert tabbed.active == "providers", (
                f"Active tab should still be 'providers' after modals, "
                f"got '{tabbed.active}'"
            )

    @pytest.mark.asyncio
    async def test_tab_switching_after_modal_dismiss(self, mock_deps):
        """Tab switching still works after a modal has been dismissed."""
        from llmport.app import LlmPortApp
        from textual.screen import ModalScreen
        from textual.app import ComposeResult
        from textual.containers import Container
        from textual.widgets import Static, Button

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)

            # Push and dismiss a modal
            class TestModal(ModalScreen):
                def compose(self) -> ComposeResult:
                    with Container():
                        yield Static("x")

            await app.push_screen(TestModal())
            await pilot.pause()
            app.screen.dismiss()
            await pilot.pause()

            # Switch to each tab after modal
            for pane_id in TAB_IDS:
                await pilot.click(f"#{ContentTab.add_prefix(pane_id)}")
                await pilot.pause()
                assert tabbed.active == pane_id, (
                    f"Tab switching after modal: expected '{pane_id}', "
                    f"got '{tabbed.active}'"
                )


# ===================================================================
# TestMainUIAfterOnboarding
# ===================================================================

class TestMainUIAfterOnboarding:
    """With pre-configured config, main UI (tabs) shows directly."""

    @pytest.mark.asyncio
    async def test_no_onboarding_screen(self, mock_deps):
        """OnboardingScreen is NOT present in the screen stack."""
        from llmport.app import LlmPortApp
        from llmport.ui.screens.onboarding import OnboardingScreen

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            # Current screen should NOT be OnboardingScreen
            assert not isinstance(app.screen, OnboardingScreen), (
                "Current screen should not be OnboardingScreen"
            )

    @pytest.mark.asyncio
    async def test_tabbed_content_visible(self, mock_deps):
        """TabbedContent is the main view when onboarding is skipped."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)
            assert tabbed is not None
            assert tabbed.active, "TabbedContent should have an active tab"

    @pytest.mark.asyncio
    async def test_app_title_shown(self, mock_deps):
        """App header shows 'llmport'."""
        from llmport.app import LlmPortApp
        from textual.widgets import Header

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            header = app.query_one(Header)
            assert header is not None

            assert app.TITLE == "llmport"


# ===================================================================
# TestGatewayNotRunning
# ===================================================================

class TestGatewayNotRunningState:
    """When the daemon is not running, each tab shows appropriate empty state."""

    @pytest.mark.asyncio
    async def test_gateway_tab_shows_not_running(self, mock_deps):
        """Gateway tab shows '未运行' when daemon is not running."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('gateway')}")
            await pilot.pause()

            status = app.query_one("#gateway-status", Static)
            status_text = _plain_text(status)
            assert "未运行" in status_text, (
                f"Gateway status should say '未运行', got: {status_text!r}"
            )

            # Start button should be visible
            start_btn = app.query_one("#btn-start-gateway", Button)
            assert start_btn is not None

    @pytest.mark.asyncio
    async def test_providers_tab_shows_gateway_not_running(self, mock_deps):
        """Providers tab shows '网关未运行' message when daemon not running."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('providers')}")
            await pilot.pause()

            # The provider-list should be hidden and empty-state shown
            provider_list = app.query_one("#provider-list", ListView)
            assert provider_list.visible is False

            empty_state = app.query_one("#empty-state", Static)
            content = _plain_text(empty_state)
            assert "网关未运行" in content or "请先在网关页启动" in content, (
                f"Expected gateway-not-running message, got: {content!r}"
            )

    @pytest.mark.asyncio
    async def test_stats_tab_shows_gateway_not_running(self, mock_deps):
        """Stats tab shows '网关未运行' when daemon is not running."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            await pilot.click(f"#{ContentTab.add_prefix('stats')}")
            await pilot.pause()

            stats_content = app.query_one("#stats-content", Static)
            content_text = _plain_text(stats_content)
            assert "未运行" in content_text, (
                f"Stats content should show '未运行', got: {content_text!r}"
            )


# ===================================================================
# TestKeyboardBindings (Bug 4)
# ===================================================================

class TestKeyboardBindings:
    """Keyboard shortcuts for tab switching and pane actions."""

    @pytest.mark.asyncio
    async def test_f1_switches_to_models(self, mock_deps):
        """Pressing f1 switches to the models tab."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            tabbed = app.query_one(TabbedContent)

            # Switch to a different tab first
            await pilot.click(f"#{ContentTab.add_prefix('settings')}")
            await pilot.pause()
            assert tabbed.active == "settings"

            # Press f1 to go back to models
            await pilot.press("f1")
            await pilot.pause()
            assert tabbed.active == "models", (
                f"Expected 'models', got '{tabbed.active}'"
            )

    @pytest.mark.asyncio
    async def test_f2_switches_to_providers(self, mock_deps):
        """Pressing f2 switches to the providers tab."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            tabbed = app.query_one(TabbedContent)

            await pilot.press("f2")
            await pilot.pause()
            assert tabbed.active == "providers"

    @pytest.mark.asyncio
    async def test_ctrl_3_switches_to_gateway(self, mock_deps):
        """Pressing f3 switches to the gateway tab."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            tabbed = app.query_one(TabbedContent)

            await pilot.press("f3")
            await pilot.pause()
            assert tabbed.active == "gateway"

    @pytest.mark.asyncio
    async def test_ctrl_4_switches_to_stats(self, mock_deps):
        """Pressing f4 switches to the stats tab."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            tabbed = app.query_one(TabbedContent)

            await pilot.press("f4")
            await pilot.pause()
            assert tabbed.active == "stats"

    @pytest.mark.asyncio
    async def test_ctrl_5_switches_to_settings(self, mock_deps):
        """Pressing f5 switches to the settings tab."""
        from llmport.app import LlmPortApp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            tabbed = app.query_one(TabbedContent)

            await pilot.press("f5")
            await pilot.pause()
            assert tabbed.active == "settings"

    @pytest.mark.asyncio
    async def test_footer_displays_bindings(self, mock_deps):
        """Footer shows binding keys for tab switching."""
        from llmport.app import LlmPortApp
        from textual.widgets import Footer

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            footer = app.query_one(Footer)
            assert footer is not None
            # Check that app-level tab-switching bindings exist
            bindings = app.BINDINGS
            binding_keys = [b.key for b in bindings]
            binding_descriptions = [b.description for b in bindings]
            assert "f1" in binding_keys, "f1 binding should exist for models tab"
            assert "f2" in binding_keys, "f2 binding should exist for providers tab"
            assert any(k in binding_descriptions for k in ("模型", "供应商")), (
                "Tab switching bindings should have Chinese descriptions"
            )
