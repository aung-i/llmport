"""Tests for SettingsPane — compose structure and button handlers."""

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# compose()
# ---------------------------------------------------------------------------

class TestCompose:
    """Widget structure produced by compose()."""

    @pytest.mark.asyncio
    async def test_compose_via_mount(self):
        """SettingsPane mounts with export, import, and check-update buttons."""
        from llmport.ui.screens.settings import SettingsPane
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield SettingsPane()

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            # Query buttons by id
            btn_export = app.query_one("#btn-export")
            assert btn_export is not None

            btn_import = app.query_one("#btn-import")
            assert btn_import is not None

            btn_check_update = app.query_one("#btn-check-update")
            assert btn_check_update is not None

    @pytest.mark.asyncio
    async def test_compose_about_section(self):
        """About section shows version info."""
        from llmport.ui.screens.settings import SettingsPane
        from textual.app import App, ComposeResult
        from textual.widgets import Static

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield SettingsPane()

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            # Check version text is rendered
            version_statics = [
                w for w in app.query(Static)
                if "v0.1.0" in str(getattr(w, "content", ""))
            ]
            assert len(version_statics) >= 1


# ---------------------------------------------------------------------------
# Button handlers
# ---------------------------------------------------------------------------

class TestButtonHandlers:
    """Button press event handlers dispatch correct notifications."""

    @pytest.mark.asyncio
    async def test_btn_export_notifies(self):
        """Pressing btn-export notifies about future export feature."""
        from llmport.ui.screens.settings import SettingsPane

        pane = SettingsPane()
        pane.notify = MagicMock()

        btn = MagicMock()
        btn.id = "btn-export"
        event = MagicMock()
        event.button = btn

        await pane.on_button_pressed(event)
        pane.notify.assert_called_once_with(
            "导出功能将在后续版本中提供", title="导出"
        )

    @pytest.mark.asyncio
    async def test_btn_import_notifies(self):
        """Pressing btn-import notifies about future import feature."""
        from llmport.ui.screens.settings import SettingsPane

        pane = SettingsPane()
        pane.notify = MagicMock()

        btn = MagicMock()
        btn.id = "btn-import"
        event = MagicMock()
        event.button = btn

        await pane.on_button_pressed(event)
        pane.notify.assert_called_once_with(
            "导入功能将在后续版本中提供", title="导入"
        )

    @pytest.mark.asyncio
    async def test_btn_check_update_notifies(self):
        """Pressing btn-check-update notifies that it's the latest version."""
        from llmport.ui.screens.settings import SettingsPane

        pane = SettingsPane()
        pane.notify = MagicMock()

        btn = MagicMock()
        btn.id = "btn-check-update"
        event = MagicMock()
        event.button = btn

        await pane.on_button_pressed(event)
        pane.notify.assert_called_once_with(
            "已经是最新版本", title="更新"
        )
