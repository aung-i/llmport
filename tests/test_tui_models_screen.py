"""Comprehensive tests for models.py TUI screens.

Covers ModelDetailScreen (ModalScreen) and ModelsPane (Vertical) with
widget mounting, event handling, and API mocking.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from textual.app import App, ComposeResult
from textual.widgets import ListView, ListItem, Label, Input, Static, Button, LoadingIndicator

from llmport.daemon import DaemonManager
from llmport.ui.screens.models import (
    ModelDetailScreen,
    ModelsPane,
    _to_models_list,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plain(widget: Static | Label) -> str:
    """Return the plain-text content of a Static or Label widget."""
    return str(widget.render())


# ===================================================================
# Helper app classes
# ===================================================================


class _HostApp(App):
    """Minimal Textual app with a daemon attribute.

    Subclasses either compose or push the widget/screen under test.
    """

    def __init__(self, daemon=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.daemon = daemon or MagicMock(spec=DaemonManager)


class _DetailHostApp(_HostApp):
    """App that pushes a ModelDetailScreen on mount."""

    def __init__(self, model, daemon=None, *args, **kwargs):
        super().__init__(daemon=daemon, *args, **kwargs)
        self._model = model

    async def on_mount(self) -> None:
        await self.push_screen(ModelDetailScreen(self._model, self.daemon))


class _PaneHostApp(_HostApp):
    """App that composes a ModelsPane directly."""

    def compose(self) -> ComposeResult:
        yield ModelsPane()


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def mock_daemon():
    """DaemonManager mock that is 'not running' by default."""
    d = MagicMock(spec=DaemonManager)
    d.get_control_port.return_value = None
    d.async_get_status = AsyncMock(return_value={"running": False})
    return d


@pytest.fixture
def sample_model():
    """A realistic model dict with two bindings."""
    return {
        "id": "gpt-4",
        "provider_count": 2,
        "bindings": [
            {
                "priority": 1,
                "provider_id": "openai",
                "model_name": "gpt-4-turbo",
            },
            {
                "priority": 2,
                "provider_id": "azure",
                "model_name": "gpt-4-standard",
            },
        ],
    }


# ===================================================================
# ModelDetailScreen
# ===================================================================


class TestModelDetailScreen:
    """Modal for viewing/editing a model's provider bindings."""

    @pytest.mark.asyncio
    async def test_compose(self, sample_model, mock_daemon):
        """Mount ModelDetailScreen and verify all widgets render."""
        app = _DetailHostApp(sample_model, daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, ModelDetailScreen)

            # -- Title contains model id --
            title = screen.query_one("#detail-title", Label)
            assert "gpt-4" in _plain(title)

            # -- Provider count label --
            labels = list(screen.query(Label))
            count_labels = [l for l in labels if "供应商数" in _plain(l)]
            assert len(count_labels) == 1
            assert "2" in count_labels[0].render().plain

            # -- Binding entries --
            binding_labels = [
                l
                for l in labels
                if "openai" in _plain(l) or "azure" in _plain(l)
            ]
            assert len(binding_labels) == 2

            # -- Buttons --
            buttons = {b.id for b in screen.query(Button)}
            assert "set-active" in buttons
            assert "close" in buttons

            # -- Routing strategy --
            strategy_labels = [
                l for l in labels if "路由策略" in _plain(l)
            ]
            assert len(strategy_labels) == 1

    @pytest.mark.asyncio
    async def test_compose_no_bindings(self, mock_daemon):
        """Model with no bindings should not crash during compose."""
        model = {"id": "claude-3", "provider_count": 0, "bindings": []}
        app = _DetailHostApp(model, daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ModelDetailScreen)

            # No binding labels for openai/azure should exist
            labels = list(screen.query(Label))
            binding_labels = [
                l
                for l in labels
                if "openai" in _plain(l) or "azure" in _plain(l)
            ]
            assert len(binding_labels) == 0

    @pytest.mark.asyncio
    async def test_close_button_dismisses(self, sample_model, mock_daemon):
        """Pressing the close button dismisses the modal."""
        app = _DetailHostApp(sample_model, daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ModelDetailScreen)

            await pilot.click("#close")
            await pilot.pause()

            assert not isinstance(app.screen, ModelDetailScreen)

    @pytest.mark.asyncio
    async def test_set_active_daemon_not_running(self, sample_model, mock_daemon):
        """set-active with port=None shows error notification."""
        mock_daemon.get_control_port.return_value = None

        app = _DetailHostApp(sample_model, daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            with patch.object(app, "notify") as mock_notify:
                await pilot.click("#set-active")
                await pilot.pause()

                mock_notify.assert_called_once_with(
                    "网关未运行",
                    title="模型切换",
                    severity="error",
                    markup=True,
                )

    @pytest.mark.asyncio
    async def test_set_active_success(self, sample_model, mock_daemon):
        """set-active with running daemon posts to API and notifies success."""
        mock_daemon.get_control_port.return_value = 12345

        app = _DetailHostApp(sample_model, daemon=mock_daemon)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            mock_resp = MagicMock()
            mock_resp.status_code = 200

            with (
                patch("httpx.AsyncClient") as mock_client_cls,
                patch.object(app, "notify") as mock_notify,
            ):
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client_cls.return_value = mock_client

                await pilot.click("#set-active")
                await pilot.pause()

                mock_client.post.assert_called_once_with(
                    "http://127.0.0.1:12345/api/models/switch",
                    json={"model_id": "gpt-4"},
                )
                mock_notify.assert_called_once_with(
                    "已切换到: gpt-4",
                    title="模型切换",
                    severity="information",
                    markup=True,
                )

    @pytest.mark.asyncio
    async def test_set_active_http_error(self, sample_model, mock_daemon):
        """set-active when API returns non-200 notifies error."""
        mock_daemon.get_control_port.return_value = 12345

        app = _DetailHostApp(sample_model, daemon=mock_daemon)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            mock_resp = MagicMock()
            mock_resp.status_code = 500

            with (
                patch("httpx.AsyncClient") as mock_client_cls,
                patch.object(app, "notify") as mock_notify,
            ):
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client_cls.return_value = mock_client

                await pilot.click("#set-active")
                await pilot.pause()

                mock_notify.assert_called_once_with(
                    "切换失败",
                    title="模型切换",
                    severity="error",
                    markup=True,
                )


# ===================================================================
# ModelsPane
# ===================================================================


class TestModelsPane:
    """Models list with current active model highlighted."""

    # -- compose -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_compose(self, mock_daemon):
        """ModelsPane composes all expected child widgets."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)

            # loading-indicator (LoadingIndicator) — hidden after refresh_models
            # completes (daemon port=None causes early return which hides it)
            loading = pane.query_one("#loading-indicator", LoadingIndicator)
            assert loading.visible is False

            # active-info (Static) — starts empty
            active_info = pane.query_one("#active-info", Static)
            assert active_info is not None

            # empty-state (Static) — present in compose
            empty_state = pane.query_one("#empty-state", Static)
            assert empty_state is not None

            # model-search (Input)
            search_input = pane.query_one("#model-search", Input)
            assert search_input.placeholder == "搜索模型..."

            # model-list (ListView inside a Section)
            list_view = pane.query_one("#model-list", ListView)
            assert list_view is not None

            # Action buttons
            btn_detail = pane.query_one("#btn-detail", Button)
            assert btn_detail is not None
            btn_add = pane.query_one("#btn-add", Button)
            assert btn_add is not None

    # -- on_input_changed -------------------------------------------------

    @pytest.mark.asyncio
    async def test_on_input_changed_filters_models(self, mock_daemon):
        """Typing a search query filters the model list."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [
                {"id": "gpt-4", "provider_count": 3},
                {"id": "gpt-4o-mini", "provider_count": 1},
                {"id": "claude-sonnet", "provider_count": 2},
            ]
            pane._filtered_models = list(pane.models)

            # Populate list_view with all items
            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            for m in pane.models:
                list_view.append(ListItem(Label(f"  {m['id']}")))
            await pilot.pause()
            assert len(list_view.children) == 3

            # Filter by "gpt" — should match 2 items
            search_input = pane.query_one("#model-search", Input)
            search_input.value = "gpt"
            await pilot.pause()
            assert len(list_view.children) == 2

    @pytest.mark.asyncio
    async def test_on_input_changed_empty_query_shows_all(
        self, mock_daemon
    ):
        """Empty search query shows all models."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [
                {"id": "gpt-4", "provider_count": 3},
                {"id": "claude-sonnet", "provider_count": 2},
            ]

            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            for m in pane.models:
                list_view.append(ListItem(Label(f"  {m['id']}")))
            await pilot.pause()
            assert len(list_view.children) == 2

            # Empty query restores all items
            search_input = pane.query_one("#model-search", Input)
            search_input.value = ""
            await pilot.pause()
            assert len(list_view.children) == 2

    @pytest.mark.asyncio
    async def test_on_input_changed_no_match(self, mock_daemon):
        """Search with no match shows empty-state."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [{"id": "gpt-4", "provider_count": 3}]

            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            list_view.append(ListItem(Label("  gpt-4")))
            await pilot.pause()

            search_input = pane.query_one("#model-search", Input)
            search_input.value = "nonexistent"
            await pilot.pause()

            # list_view hidden, empty-state shown
            empty_state = pane.query_one("#empty-state", Static)
            assert empty_state.visible is True
            assert list_view.visible is False
            assert "无匹配" in _plain(empty_state)

    # -- on_button_pressed -------------------------------------------------

    @pytest.mark.asyncio
    async def test_btn_add_notifies(self, mock_daemon):
        """Pressing btn-add shows a helpful notification."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            btn_add = pane.query_one("#btn-add", Button)

            with patch.object(app, "notify") as mock_notify:
                btn_add.action_press()
                await pilot.pause()

                mock_notify.assert_called_once_with(
                    "在供应商页添加模型别名即可自动关联",
                    title="提示",
                    severity="information",
                    markup=True,
                )

    @pytest.mark.asyncio
    async def test_btn_detail_no_selection(self, mock_daemon):
        """Pressing btn-detail with no selection does not crash."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            list_view = app.query_one("#model-list", ListView)
            assert list_view.index is None

            pane = app.query_one(ModelsPane)
            btn_detail = pane.query_one("#btn-detail", Button)

            # Should not raise
            btn_detail.action_press()
            await pilot.pause()

    @pytest.mark.asyncio
    async def test_btn_detail_pushes_modal_screen(self):
        """Pressing btn-detail with a selection pushes ModelDetailScreen."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True}
        )

        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [{"id": "gpt-4", "provider_count": 2}]
            pane._filtered_models = [{"id": "gpt-4", "provider_count": 2}]

            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            item = ListItem(Label("  gpt-4"))
            list_view.append(item)
            list_view.index = 0
            await pilot.pause()

            with patch(
                "llmport.ui.screens.models.async_get_json"
            ) as mock_get_json:
                mock_get_json.return_value = [
                    {
                        "id": "gpt-4",
                        "provider_count": 2,
                        "bindings": [
                            {
                                "priority": 1,
                                "provider_id": "openai",
                                "model_name": "gpt-4-turbo",
                            }
                        ],
                    }
                ]

                btn_detail = pane.query_one("#btn-detail", Button)
                btn_detail.action_press()
                await pilot.pause()

            assert isinstance(app.screen, ModelDetailScreen)
            detail_screen = app.screen
            assert detail_screen.model["id"] == "gpt-4"
            assert len(detail_screen.model["bindings"]) == 1

    # -- on_list_view_selected -------------------------------------------

    @pytest.mark.asyncio
    async def test_on_list_view_selected_no_port(self, mock_daemon):
        """Selecting a model when the daemon is not running notifies error."""
        mock_daemon.get_control_port.return_value = None

        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [{"id": "test-model", "provider_count": 1}]
            pane._filtered_models = [
                {"id": "test-model", "provider_count": 1}
            ]

            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            item = ListItem(Label("  test-model"))
            list_view.append(item)
            list_view.index = 0
            await pilot.pause()

            with patch.object(app, "notify") as mock_notify:
                list_view.post_message(
                    ListView.Selected(list_view, item, 0)
                )
                await pilot.pause()

                mock_notify.assert_called_once_with(
                    "网关未运行",
                    title="模型切换",
                    severity="error",
                    markup=True,
                )

    @pytest.mark.asyncio
    async def test_on_list_view_selected_success(self):
        """Selecting a model with running daemon calls API and refreshes."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True, "active_model": "test-model"}
        )

        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [{"id": "test-model", "provider_count": 1}]
            pane._filtered_models = [
                {"id": "test-model", "provider_count": 1}
            ]

            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            item = ListItem(Label("  test-model"))
            list_view.append(item)
            list_view.index = 0
            await pilot.pause()

            mock_http_resp = MagicMock()
            mock_http_resp.status_code = 200

            with (
                patch("httpx.AsyncClient") as mock_client_cls,
                patch(
                    "llmport.ui.screens.models.async_get_json"
                ) as mock_get_json,
                patch.object(app, "notify") as mock_notify,
            ):
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post = AsyncMock(return_value=mock_http_resp)
                mock_client_cls.return_value = mock_client

                # refresh_models() is called at the end — need its mocks too
                mock_get_json.side_effect = [
                    {"active_model": "test-model"},  # /api/status
                    [{"id": "test-model", "provider_count": 1}],  # /api/models
                ]

                list_view.post_message(
                    ListView.Selected(list_view, item, 0)
                )
                await pilot.pause()

                mock_client.post.assert_called_once_with(
                    "http://127.0.0.1:9999/api/models/switch",
                    json={"model_id": "test-model"},
                )
                mock_notify.assert_any_call(
                    "已切换到: test-model",
                    title="模型切换",
                    severity="information",
                    markup=True,
                )

    @pytest.mark.asyncio
    async def test_on_list_view_selected_http_error(self):
        """Selecting a model when API returns error notifies failure."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True}
        )

        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [{"id": "test-model", "provider_count": 1}]
            pane._filtered_models = [
                {"id": "test-model", "provider_count": 1}
            ]

            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            item = ListItem(Label("  test-model"))
            list_view.append(item)
            list_view.index = 0
            await pilot.pause()

            mock_http_resp = MagicMock()
            mock_http_resp.status_code = 500

            with (
                patch("httpx.AsyncClient") as mock_client_cls,
                patch.object(app, "notify") as mock_notify,
            ):
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.post = AsyncMock(return_value=mock_http_resp)
                mock_client_cls.return_value = mock_client

                list_view.post_message(
                    ListView.Selected(list_view, item, 0)
                )
                await pilot.pause()

                mock_notify.assert_called_once_with(
                    "切换失败",
                    title="模型切换",
                    severity="error",
                    markup=True,
                )

    # -- refresh_models ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_refresh_models_populates_list(self):
        """refresh_models with valid data populates the list view."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={
                "running": True,
                "active_model": "gpt-4",
            }
        )

        app = _PaneHostApp(daemon=mock_daemon)

        with patch(
            "llmport.ui.screens.models.async_get_json"
        ) as mock_get_json:
            mock_get_json.side_effect = [
                {"active_model": "gpt-4"},  # /api/status
                [
                    {"id": "gpt-4", "provider_count": 3},
                    {"id": "claude-3", "provider_count": 2},
                ],  # /api/models
            ]

            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(ModelsPane)
                list_view = pane.query_one("#model-list", ListView)

                assert len(list_view.children) == 2

                # First item (active) should have the ▶ marker
                first_label = list_view.children[0].query_one(Label)
                assert "▶" in _plain(first_label)
                assert "gpt-4" in _plain(first_label)

                # Second item (inactive) should not have ▶
                second_label = list_view.children[1].query_one(Label)
                assert "▶" not in _plain(second_label)
                assert "claude-3" in _plain(second_label)

                # active-info should show the active model
                active_info = pane.query_one("#active-info", Static)
                assert "gpt-4" in _plain(active_info)

                # _filtered_models should be set
                assert len(pane._filtered_models) == 2

    @pytest.mark.asyncio
    async def test_refresh_models_empty_list(self):
        """refresh_models with empty API list shows empty state."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True}
        )

        app = _PaneHostApp(daemon=mock_daemon)

        with patch(
            "llmport.ui.screens.models.async_get_json"
        ) as mock_get_json:
            mock_get_json.side_effect = [
                {},  # /api/status
                [],  # /api/models
            ]

            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(ModelsPane)
                list_view = pane.query_one("#model-list", ListView)

                # Should show empty-state instead of ListItem in list_view
                empty_state = pane.query_one("#empty-state", Static)
                assert empty_state.visible is True
                assert list_view.visible is False
                assert "暂无模型" in _plain(empty_state)

                # active-info shows "无" when no active_model key exists
                active_info = pane.query_one("#active-info", Static)
                assert "无" in _plain(active_info)

    @pytest.mark.asyncio
    async def test_refresh_models_none_response(self):
        """refresh_models with None API response shows empty state."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True}
        )

        app = _PaneHostApp(daemon=mock_daemon)

        with patch(
            "llmport.ui.screens.models.async_get_json"
        ) as mock_get_json:
            mock_get_json.side_effect = [
                None,  # /api/status returns None
                None,  # /api/models returns None
            ]

            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(ModelsPane)
                list_view = pane.query_one("#model-list", ListView)

                # Should show empty-state
                empty_state = pane.query_one("#empty-state", Static)
                assert empty_state.visible is True
                assert list_view.visible is False
                assert "暂无模型" in _plain(empty_state)

    @pytest.mark.asyncio
    async def test_refresh_models_without_active_model(self):
        """refresh_models when no active model is set."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True}
        )

        app = _PaneHostApp(daemon=mock_daemon)

        with patch(
            "llmport.ui.screens.models.async_get_json"
        ) as mock_get_json:
            mock_get_json.side_effect = [
                {"active_model": None},  # /api/status has no active model
                [{"id": "gpt-4", "provider_count": 1}],  # /api/models
            ]

            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(ModelsPane)
                list_view = pane.query_one("#model-list", ListView)

                assert len(list_view.children) == 1
                # No ▶ marker since no model is active
                label = list_view.children[0].query_one(Label)
                assert "▶" not in _plain(label)

    @pytest.mark.asyncio
    async def test_refresh_models_daemon_not_running(self):
        """refresh_models returns early when daemon port is None."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = None
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": False}
        )

        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            list_view = pane.query_one("#model-list", ListView)

            # No models added, list should be empty and hidden
            assert len(list_view.children) == 0

            # loading-indicator hidden because refresh_models hides it
            loading = pane.query_one("#loading-indicator", LoadingIndicator)
            assert loading.visible is False

            # empty-state shows gateway-not-running message
            empty_state = pane.query_one("#empty-state", Static)
            assert empty_state.visible is True
            assert "网关未运行" in _plain(empty_state)

            # active-info stays empty
            active_info = pane.query_one("#active-info", Static)
            assert _plain(active_info) == ""

    # -- Missing lines 36, 170, 205 ---------------------------------------

    @pytest.mark.asyncio
    async def test_refresh_models_dict_with_null_models(self):
        """refresh_models with {'models': None} hits line 36 (dict fallback)."""
        mock_daemon = MagicMock(spec=DaemonManager)
        mock_daemon.get_control_port.return_value = 9999
        mock_daemon.async_get_status = AsyncMock(
            return_value={"running": True}
        )

        app = _PaneHostApp(daemon=mock_daemon)

        with patch(
            "llmport.ui.screens.models.async_get_json"
        ) as mock_get_json:
            mock_get_json.side_effect = [
                {"active_model": "gpt-4"},  # /api/status
                {"models": None},  # /api/models wrapped in dict, but None
            ]

            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(ModelsPane)
                list_view = pane.query_one("#model-list", ListView)

                empty_state = pane.query_one("#empty-state", Static)
                assert empty_state.visible is True
                assert list_view.visible is False
                assert "暂无模型" in _plain(empty_state)

    @pytest.mark.asyncio
    async def test_on_input_changed_with_active_model(self, mock_daemon):
        """Filtering when an active model is set (hits line 170, the ▶ prefix)."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            pane.models = [
                {"id": "gpt-4", "provider_count": 3},
                {"id": "claude-sonnet", "provider_count": 2},
            ]
            pane._filtered_models = list(pane.models)
            pane.active_model = "gpt-4"

            # Populate list_view with filtered result (like on_input_changed does)
            list_view = pane.query_one("#model-list", ListView)
            await list_view.clear()
            for m in pane.models:
                if m["id"] == pane.active_model:
                    text = f"[green]▶[/] [bold $primary]{m['id']}[/]   [dim]({m['provider_count']} 供应商)[/]"
                else:
                    text = f"  {m['id']}   [dim]({m['provider_count']} 供应商)[/]"
                list_view.append(ListItem(Label(text)))
            await pilot.pause()

            assert len(list_view.children) == 2

            # Trigger input changed to exercise the handler with active_model
            search_input = pane.query_one("#model-search", Input)
            search_input.value = "gpt"
            await pilot.pause()

            assert len(list_view.children) == 1
            active_label = list_view.children[0].query_one(Label)
            # The ▶ indicator should appear for the active model
            assert "▶" in _plain(active_label)
            assert "gpt-4" in _plain(active_label)

    @pytest.mark.asyncio
    async def test_on_list_view_selected_item_none(self, mock_daemon):
        """on_list_view_selected with event.item None returns early (line 205)."""
        app = _PaneHostApp(daemon=mock_daemon)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(ModelsPane)
            list_view = pane.query_one("#model-list", ListView)

            # Create a Selected message and manually set item to None
            item = ListItem(Label("dummy"))
            msg = ListView.Selected(list_view, item, 0)
            msg.item = None  # force the None-item branch

            # Calling the handler directly should hit the early return
            await pane.on_list_view_selected(msg)
            await pilot.pause()

            # If we got here without error, the early return worked

    # -- _to_models_list fallback -----------------------------------------

    def test_to_models_list_unexpected_type(self):
        """_to_models_list fallback for types that are not None/dict/list (line 39)."""
        assert _to_models_list("not-a-dict-or-list") == []
        assert _to_models_list(42) == []
