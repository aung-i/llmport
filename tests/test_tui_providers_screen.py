"""Comprehensive tests for ProviderFormScreen and ProvidersPane.

Tests mount real Textual screens and interact with widgets to verify
compose output, button handlers, HTTP interactions, and edge cases.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Static

from llmport.daemon import DaemonManager
from llmport.ui.screens.providers import ProviderFormScreen, ProvidersPane


# ---------------------------------------------------------------------------
# Helper: extract label text for assertions.
# In Textual 8.x, Label.renderable returns "" before the first render pass,
# so we use .render() and extract the plain text.
# ---------------------------------------------------------------------------

def _label_text(label: Label) -> str:
    """Return the plain text content of a Label/Static widget."""
    rendered = label.render()
    if hasattr(rendered, "plain"):
        return rendered.plain
    return str(rendered)


# ===================================================================
# Helper: minimal test app that hosts ProviderFormScreen as a modal
# ===================================================================

class _FormApp(App):
    """Minimal Textual app used to host ProviderFormScreen."""

    def __init__(self, daemon_mock: DaemonManager | None = None):
        super().__init__()
        self.daemon = daemon_mock or MagicMock(spec=DaemonManager)
        self._providers_pane = MagicMock(spec=ProvidersPane)
        self._providers_pane.refresh_providers = AsyncMock()

    def compose(self) -> ComposeResult:
        yield Static("dummy")

    def query_one(self, filter_type, *args, **kwargs):
        if filter_type is ProvidersPane:
            return self._providers_pane
        return super().query_one(filter_type, *args, **kwargs)


# ===================================================================
# Helper: minimal test app that hosts ProvidersPane inline
# ===================================================================

class _PaneApp(App):
    """Minimal Textual app containing ProvidersPane."""

    def __init__(self, daemon_mock: DaemonManager | None = None):
        super().__init__()
        self.daemon = daemon_mock or MagicMock(spec=DaemonManager)
        self.pushed_screens: list = []

    def compose(self) -> ComposeResult:
        yield ProvidersPane()

    async def push_screen(self, screen, *args, **kwargs):
        self.pushed_screens.append(screen)
        return screen


# ===================================================================
# ProviderFormScreen tests
# ===================================================================

_FORM_SIZE = (80, 40)  # form has min-height 28; needs room for buttons

# -- compose ---------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_form_compose_add_mode():
    """ADD mode: title says '添加供应商', all inputs have correct placeholders."""
    daemon = MagicMock(spec=DaemonManager)
    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        # Title says 添加供应商
        container_children = list(form.query_one("#form-container").children)
        title_text = str(container_children[0].render())
        assert "添加供应商" in title_text

        # Name input
        name_input = form.query_one("#input-name", Input)
        assert name_input.placeholder == "Anthropic"
        assert name_input.value == ""

        # Key input
        key_input = form.query_one("#input-key", Input)
        assert key_input.placeholder == "sk-ant-api03-..."
        assert key_input.password is True

        # URL input
        url_input = form.query_one("#input-url", Input)
        assert url_input.placeholder == "https://api.anthropic.com"

        # Protocol select
        select = form.query_one("#select-protocol", Select)
        assert select.value == "openai"

        # Models input
        model_input = form.query_one("#input-models", Input)
        assert model_input.placeholder == "claude-opus-4-8,claude-opus,opus"
        assert model_input.value == ""

        # All buttons present
        assert form.query_one("#btn-cancel", Button)
        assert form.query_one("#btn-clear-key", Button)
        assert form.query_one("#btn-test", Button)
        assert form.query_one("#btn-fetch", Button)
        assert form.query_one("#btn-save", Button)


@pytest.mark.asyncio
async def test_provider_form_compose_edit_mode():
    """EDIT mode: title says '编辑供应商', values pre-filled from provider dict."""
    daemon = MagicMock(spec=DaemonManager)
    provider = {
        "name": "My Anthropic",
        "base_url": "https://custom.anthropic.com",
        "protocol": "anthropic",
        "models": [
            {"name": "claude-opus-4-8", "aliases": ["opus", "claude-opus"]},
            {"name": "claude-sonnet-4-8", "aliases": ["sonnet"]},
        ],
    }

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon, provider))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        # Title says 编辑供应商
        container_children = list(form.query_one("#form-container").children)
        title_text = str(container_children[0].render())
        assert "编辑供应商" in title_text
        assert "My Anthropic" in title_text

        # Inputs pre-filled
        name_input = form.query_one("#input-name", Input)
        assert name_input.value == "My Anthropic"

        # Key input: edit-mode placeholder hints at keeping existing
        key_input = form.query_one("#input-key", Input)
        assert "保留原值" in key_input.placeholder

        url_input = form.query_one("#input-url", Input)
        assert url_input.value == "https://custom.anthropic.com"

        select = form.query_one("#select-protocol", Select)
        assert select.value == "anthropic"

        model_input = form.query_one("#input-models", Input)
        expected_models = "claude-opus-4-8,opus,claude-opus\nclaude-sonnet-4-8,sonnet"
        assert model_input.value == expected_models


# -- cancel ---------------------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_cancel():
    """Cancel button dismisses the form."""
    daemon = MagicMock(spec=DaemonManager)
    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        assert isinstance(pilot.app.screen, ProviderFormScreen)

        await pilot.click("#btn-cancel")
        await pilot.pause()

        # Form should have been dismissed
        assert not isinstance(pilot.app.screen, ProviderFormScreen)


# -- clear-key ------------------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_clear_key():
    """Clear-key button empties the key input and sets _key_cleared flag."""
    daemon = MagicMock(spec=DaemonManager)
    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        key_input = form.query_one("#input-key", Input)
        key_input.value = "some-secret"

        # Invoke the handler directly because the button is partially off-screen
        # (the sibling Input has CSS width:100%, pushing the button outside
        #  the visible content area, making pilot.click unreliable).
        clear_btn = form.query_one("#btn-clear-key", Button)
        await form.on_button_pressed(Button.Pressed(clear_btn))
        await pilot.pause()

        assert key_input.value == ""
        assert form._key_cleared is True


# -- test-connection -------------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_test_success():
    """Test-connection button: success path notifies with latency."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"
        form.query_one("#input-models", Input).value = "model-a,alias-a"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True, "latency_ms": 42.0}
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "notify") as mock_notify:
                await pilot.click("#btn-test")
                await pilot.pause()

                mock_notify.assert_called_once()
                msg = mock_notify.call_args[0][0]
                assert "连接成功" in msg
                assert "42" in msg

                # Verify correct URL was called
                mock_client.post.assert_called_once()
                call_url = mock_client.post.call_args[0][0]
                assert "/api/providers/test" in call_url


@pytest.mark.asyncio
async def test_on_button_pressed_btn_test_error():
    """Test-connection button: error path notifies failure."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": False, "error": "Connection refused"}
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "notify") as mock_notify:
                await pilot.click("#btn-test")
                await pilot.pause()

                mock_notify.assert_called_once()
                msg, kwargs = mock_notify.call_args[0][0], mock_notify.call_args[1]
                assert "失败" in msg
                assert kwargs.get("severity") == "error"


# -- fetch models ----------------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_fetch_success():
    """Fetch-models button: populates models input from API response."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "models": [
                    {"id": "claude-opus-4-8"},
                    {"id": "claude-sonnet-4-8"},
                    {"id": "claude-haiku"},
                ],
            }
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "notify") as mock_notify:
                await pilot.click("#btn-fetch")
                await pilot.pause()

                # Models input should be populated
                model_input = form.query_one("#input-models", Input)
                assert "claude-opus-4-8" in model_input.value
                assert "claude-sonnet-4-8" in model_input.value
                assert "claude-haiku" in model_input.value

                mock_notify.assert_called_once()
                msg = mock_notify.call_args[0][0]
                assert "找到" in msg

                # Verify URL
                mock_client.post.assert_called_once()
                call_url = mock_client.post.call_args[0][0]
                assert "/api/providers/models" in call_url


@pytest.mark.asyncio
async def test_on_button_pressed_btn_fetch_empty():
    """Fetch-models button: empty models list triggers error notification."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.json.return_value = {"models": None}
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "notify") as mock_notify:
                await pilot.click("#btn-fetch")
                await pilot.pause()

                # Models input should not be modified
                model_input = form.query_one("#input-models", Input)
                assert model_input.value == ""

                mock_notify.assert_called_once()
                msg = mock_notify.call_args[0][0]
                assert "获取失败" in msg
                assert mock_notify.call_args[1]["severity"] == "error"


@pytest.mark.asyncio
async def test_on_button_pressed_btn_fetch_none_stays_open():
    """Fetch-models button: when models is None (empty dict), input stays unchanged."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.json.return_value = {}  # no "models" key at all
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "notify") as mock_notify:
                await pilot.click("#btn-fetch")
                await pilot.pause()

                assert form.query_one("#input-models", Input).value == ""
                mock_notify.assert_called_once()
                assert "获取失败" in mock_notify.call_args[0][0]


# -- save -----------------------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_save_success():
    """Save button: success dismisses form and calls refresh_providers."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "OK"
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "dismiss") as mock_dismiss:
                await pilot.click("#btn-save")
                await pilot.pause()

                mock_dismiss.assert_called_once()
                # refresh_providers should have been called on the mock pane
                app._providers_pane.refresh_providers.assert_awaited_once()

                # Verify the save URL
                call_url = mock_client.post.call_args[0][0]
                assert "/api/providers" in call_url
                assert "/test" not in call_url
                assert "/models" not in call_url


@pytest.mark.asyncio
async def test_on_button_pressed_btn_save_error():
    """Save button: API error notifies and does NOT dismiss."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = "Bad Request"
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "dismiss") as mock_dismiss:
                with patch.object(form, "notify") as mock_notify:
                    await pilot.click("#btn-save")
                    await pilot.pause()

                    mock_dismiss.assert_not_called()
                    mock_notify.assert_called_once()
                    msg = mock_notify.call_args[0][0]
                    assert "保存失败" in msg
                    assert mock_notify.call_args[1].get("severity") == "error"


@pytest.mark.asyncio
async def test_on_button_pressed_btn_save_after_clear_key():
    """Save button after clearing key sends empty key to API (line 122)."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-key", Input).value = "sk-xxx"

        # Clear the key first
        clear_btn = form.query_one("#btn-clear-key", Button)
        await form.on_button_pressed(Button.Pressed(clear_btn))
        await pilot.pause()
        assert form._key_cleared is True

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "OK"
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "dismiss"):
                await pilot.click("#btn-save")
                await pilot.pause()

                # The API should receive key=""
                sent_body = mock_client.post.call_args[1]["json"]
                assert sent_body["api_key"] == ""


@pytest.mark.asyncio
async def test_on_button_pressed_btn_save_edit_empty_key():
    """Save in edit mode with empty key sends '***' to keep existing key (line 126)."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    provider = {
        "name": "Existing",
        "base_url": "https://example.com",
        "protocol": "openai",
        "models": [],
    }

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon, provider))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        # Key input is empty (user didn't enter anything)
        # _key_cleared is False (user didn't press clear-key)
        assert form._key_cleared is False
        assert form.query_one("#input-key", Input).value == ""

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "OK"
            mock_client.post = AsyncMock(return_value=mock_resp)

            with patch.object(form, "dismiss"):
                await pilot.click("#btn-save")
                await pilot.pause()

                # The API should receive key="***" (keep existing)
                sent_body = mock_client.post.call_args[1]["json"]
                assert sent_body["api_key"] == "***"


# -- port=None (gateway not running) ---------------------------------

@pytest.mark.asyncio
async def test_buttons_with_port_none():
    """Save/test/fetch buttons all notify '网关未运行' when port is None."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = None

    app = _FormApp(daemon)
    async with app.run_test(size=_FORM_SIZE) as pilot:
        await pilot.app.push_screen(ProviderFormScreen(daemon))
        await pilot.pause()

        form = pilot.app.screen
        assert isinstance(form, ProviderFormScreen)

        form.query_one("#input-name", Input).value = "TestProv"
        form.query_one("#input-url", Input).value = "https://example.com"
        form.query_one("#input-key", Input).value = "sk-xxx"

        # btn-test with port=None
        with patch.object(form, "notify") as mock_notify:
            await pilot.click("#btn-test")
            await pilot.pause()
            mock_notify.assert_called_once()
            assert "网关未运行" in mock_notify.call_args[0][0]
            assert mock_notify.call_args[1]["severity"] == "error"

        # btn-fetch with port=None
        with patch.object(form, "notify") as mock_notify2:
            await pilot.click("#btn-fetch")
            await pilot.pause()
            mock_notify2.assert_called_once()
            assert "网关未运行" in mock_notify2.call_args[0][0]

        # btn-save with port=None
        with patch.object(form, "notify") as mock_notify3:
            await pilot.click("#btn-save")
            await pilot.pause()
            mock_notify3.assert_called_once()
            assert "网关未运行" in mock_notify3.call_args[0][0]


# ===================================================================
# ProvidersPane tests
# ===================================================================

# Most pane tests fit in a compact window; only button-clicking tests
# may need more height.  We use _PANEL_SIZE for those.
_PANEL_SIZE = (80, 24)

# -- compose ---------------------------------------------------------

@pytest.mark.asyncio
async def test_providers_pane_compose():
    """ProvidersPane compose has provider-list, add, and delete buttons."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = None  # prevent HTTP calls

    app = _PaneApp(daemon)
    async with app.run_test(size=_PANEL_SIZE) as pilot:
        await pilot.pause()

        pane = app.query_one(ProvidersPane)

        list_view = pane.query_one("#provider-list", ListView)
        assert list_view is not None

        add_btn = pane.query_one("#btn-add-provider", Button)
        assert add_btn is not None

        delete_btn = pane.query_one("#btn-delete-provider", Button)
        assert delete_btn is not None


# -- refresh_providers -----------------------------------------------

@pytest.mark.asyncio
async def test_refresh_providers_valid():
    """refresh_providers with valid provider list populates list view."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    providers_data = [
        {
            "id": "p1",
            "name": "Provider One",
            "base_url": "https://one.example.com",
            "models": [{"name": "m1"}, {"name": "m2"}],
            "health": {"status": "up"},
        },
        {
            "id": "p2",
            "name": "Provider Two",
            "base_url": "https://two.example.com",
            "models": [{"name": "m3"}],
            "health": {"status": "down"},
        },
    ]

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = providers_data

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)
            assert pane.providers == providers_data

            list_view = pane.query_one("#provider-list", ListView)
            assert list_view.children is not None

            items = list(list_view.query(ListItem))
            assert len(items) == 2

            first_label = items[0].query_one(Label)
            rendered = first_label.render()
            rendered_text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "Provider One" in rendered_text

            second_label = items[1].query_one(Label)
            rendered2 = second_label.render()
            rendered_text2 = rendered2.plain if hasattr(rendered2, "plain") else str(rendered2)
            assert "Provider Two" in rendered_text2


@pytest.mark.asyncio
async def test_refresh_providers_empty():
    """refresh_providers with empty list shows '暂无供应商'."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = []

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)
            assert pane.providers == []

            list_view = pane.query_one("#provider-list", ListView)
            items = list(list_view.query(ListItem))
            assert len(items) == 1

            empty_label = items[0].query_one(Label)
            rendered = empty_label.render()
            rendered_text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "暂无供应商" in rendered_text


@pytest.mark.asyncio
async def test_refresh_providers_none():
    """refresh_providers when async_get_json returns None (network error)."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = None

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)
            assert pane.providers == []

            list_view = pane.query_one("#provider-list", ListView)
            items = list(list_view.query(ListItem))
            assert len(items) == 1

            label = items[0].query_one(Label)
            rendered = label.render()
            rendered_text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
            assert "暂无供应商" in rendered_text


@pytest.mark.asyncio
async def test_refresh_providers_no_port():
    """refresh_providers with no daemon port shows '网关未运行'."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = None

    app = _PaneApp(daemon)
    async with app.run_test(size=_PANEL_SIZE) as pilot:
        await pilot.pause()

        pane = app.query_one(ProvidersPane)
        assert pane.providers == []

        list_view = pane.query_one("#provider-list", ListView)
        items = list(list_view.query(ListItem))
        assert len(items) == 1

        label = items[0].query_one(Label)
        rendered = label.render()
        rendered_text = rendered.plain if hasattr(rendered, "plain") else str(rendered)
        assert "网关未运行" in rendered_text


# -- add-provider button --------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_add_provider():
    """Add-provider button pushes ProviderFormScreen with daemon."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = []

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            await pilot.click("#btn-add-provider")
            await pilot.pause()

            assert len(app.pushed_screens) == 1
            screen = app.pushed_screens[0]
            assert isinstance(screen, ProviderFormScreen)
            assert screen.provider is None
            assert screen.is_edit is False
            assert screen.daemon is daemon


# -- delete-provider button ------------------------------------------

@pytest.mark.asyncio
async def test_on_button_pressed_btn_delete_provider_no_selection():
    """Delete button with no selection does not crash."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = [
            {"id": "p1", "name": "P1", "base_url": "", "models": [], "health": {}},
        ]

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            await pilot.click("#btn-delete-provider")
            await pilot.pause()

            # Should not crash — nothing to assert beyond no exception


@pytest.mark.asyncio
async def test_on_button_pressed_btn_delete_provider_valid():
    """Delete button with valid selection calls API and refreshes."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    providers_data = [
        {"id": "p1", "name": "Provider One", "base_url": "", "models": [], "health": {}},
    ]

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = providers_data

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)
            list_view = pane.query_one("#provider-list", ListView)

            # Set the selection via index so the delete handler finds a provider
            assert len(list(list_view.query(ListItem))) > 0
            list_view.index = 0
            await pilot.pause()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_client.delete = AsyncMock(return_value=mock_resp)

                with patch.object(pane, "notify") as mock_notify:
                    await pilot.click("#btn-delete-provider")
                    await pilot.pause()

                    mock_client.delete.assert_called_once()
                    call_url = mock_client.delete.call_args[0][0]
                    assert "/api/providers" in call_url
                    call_json = mock_client.delete.call_args[1]["json"]
                    assert call_json["id"] == "p1"

                    mock_notify.assert_called_once()
                    assert "已删除" in mock_notify.call_args[0][0]


@pytest.mark.asyncio
async def test_on_button_pressed_btn_delete_provider_no_port():
    """Delete button when port is None notifies error."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = None

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = []

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)

            # Force-set providers so a selection will find a match
            pane.providers = [{"id": "p1", "name": "P1"}]
            list_view = pane.query_one("#provider-list", ListView)
            list_view.index = 0
            await pilot.pause()

            with patch.object(pane, "notify") as mock_notify:
                await pilot.click("#btn-delete-provider")
                await pilot.pause()

                mock_notify.assert_called_once()
                msg = mock_notify.call_args[0][0]
                assert "网关未运行" in msg
                assert mock_notify.call_args[1]["severity"] == "error"


@pytest.mark.asyncio
async def test_on_button_pressed_btn_delete_provider_http_error():
    """Delete provider with HTTP error notifies failure (line 269)."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    providers_data = [
        {"id": "p1", "name": "FailProv", "base_url": "", "models": [], "health": {}},
    ]

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = providers_data

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)
            list_view = pane.query_one("#provider-list", ListView)
            assert len(list(list_view.query(ListItem))) > 0
            list_view.index = 0
            await pilot.pause()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__aenter__.return_value = mock_client

                mock_resp = MagicMock()
                mock_resp.status_code = 500
                mock_resp.text = "Server Error"
                mock_client.delete = AsyncMock(return_value=mock_resp)

                with patch.object(pane, "notify") as mock_notify:
                    await pilot.click("#btn-delete-provider")
                    await pilot.pause()

                    mock_notify.assert_called_once()
                    assert "删除失败" in mock_notify.call_args[0][0]
                    assert mock_notify.call_args[1]["severity"] == "error"


# -- list view selection (edit mode) ---------------------------------

@pytest.mark.asyncio
async def test_on_list_view_selected():
    """Selecting a provider from the list pushes ProviderFormScreen in edit mode."""
    daemon = MagicMock(spec=DaemonManager)
    daemon.get_control_port.return_value = 12345

    providers_data = [
        {
            "id": "p1",
            "name": "Provider One",
            "base_url": "https://one.example.com",
            "protocol": "openai",
            "models": [{"name": "gpt-5", "aliases": []}],
            "health": {"status": "up"},
        },
    ]

    with patch(
        "llmport.ui.screens.providers.async_get_json",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = providers_data

        app = _PaneApp(daemon)
        async with app.run_test(size=_PANEL_SIZE) as pilot:
            await pilot.pause()

            pane = app.query_one(ProvidersPane)
            list_view = pane.query_one("#provider-list", ListView)

            # Set the index so the handler can look up the provider, then
            # fire the event (setting index alone does not trigger Selected).
            list_items = list(list_view.query(ListItem))
            assert len(list_items) == 1
            list_view.index = 0
            list_view.post_message(ListView.Selected(list_view, list_items[0], index=0))
            await pilot.pause()

            assert len(app.pushed_screens) == 1
            screen = app.pushed_screens[0]
            assert isinstance(screen, ProviderFormScreen)
            assert screen.is_edit is True
            assert screen.provider is not None
            assert screen.provider["id"] == "p1"
            assert screen.provider["name"] == "Provider One"
            assert screen.daemon is daemon
