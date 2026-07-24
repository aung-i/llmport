"""Comprehensive tests for GatewayConfigScreen and GatewayPane.

Covers:
  GatewayConfigScreen — compose, cancel, save with valid/invalid input,
                        daemon-not-running, API error.
  GatewayPane        — compose, not-running display, running display with
                        full status, empty providers, health-icons,
                        start/stop/restart/config button handlers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static

from llmport.ui.screens.gateway import GatewayConfigScreen, GatewayPane


# =============================================================================
# Helper test apps
# =============================================================================


class GatewayPaneTestApp(App):
    """Minimal Textual app that wraps GatewayPane for testing.

    Exposes a controllable ``daemon`` mock so tests can inject status
    responses, control-port values, etc.
    """

    def __init__(self, status: dict | None = None, port: int | None = None) -> None:
        super().__init__()
        self.daemon = MagicMock()
        self.daemon.async_get_status = AsyncMock(
            return_value=status or {"running": False}
        )
        self.daemon.get_control_port.return_value = port

    def compose(self) -> ComposeResult:
        yield GatewayPane()


class GatewayConfigTestApp(App):
    """Minimal Textual app that pushes a GatewayConfigScreen on mount.

    Includes a GatewayPane in the background so the save handler's
    ``app.query_one(GatewayPane).refresh_status()`` call works without
    extra mocking.
    """

    def __init__(
        self, config: dict | None = None, port: int | None = 12345
    ) -> None:
        super().__init__()
        self.daemon = MagicMock()
        self.daemon.async_get_status = AsyncMock(return_value={"running": False})
        self.daemon.get_control_port.return_value = port
        self.daemon.restart = MagicMock()
        self._config = config or {"host": "127.0.0.1", "port": 11434}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield GatewayPane()

    async def on_mount(self) -> None:
        self.push_screen(GatewayConfigScreen(self._config))


# =============================================================================
# GatewayConfigScreen tests
# =============================================================================


class TestGatewayConfigScreen:
    """Tests for the GatewayConfigScreen modal."""

    @pytest.mark.asyncio
    async def test_compose(self) -> None:
        """Inputs are pre-filled from config dict; both buttons exist."""
        config = {"host": "0.0.0.0", "port": 8080}
        app = GatewayConfigTestApp(config=config, port=12345)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            # The top-most screen should be the modal
            screen = app.screen
            assert isinstance(screen, GatewayConfigScreen), (
                f"Expected GatewayConfigScreen, got {type(screen).__name__}"
            )

            host_input = screen.query_one("#input-host", Input)
            port_input = screen.query_one("#input-port", Input)
            assert host_input.value == "0.0.0.0"
            assert port_input.value == "8080"

            # Buttons
            assert screen.query_one("#btn-save-restart", Button) is not None
            assert screen.query_one("#btn-cancel", Button) is not None

            # Labels rendered (use .content to get text of Label/Static)
            labels = list(screen.query(Label))
            label_texts = [
                str(l.content).strip()
                for l in labels
                if hasattr(l, "content") and l.content
            ]
            assert any("Host" in t for t in label_texts), (
                "Host label should be present"
            )
            assert any("Port" in t for t in label_texts), (
                "Port label should be present"
            )

    @pytest.mark.asyncio
    async def test_cancel_dismisses(self) -> None:
        """Pressing cancel dismisses the modal."""
        app = GatewayConfigTestApp(port=12345)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            assert isinstance(app.screen, GatewayConfigScreen)

            await pilot.click("#btn-cancel")
            await pilot.pause()

            # After dismiss the modal is popped; screen should NOT be the modal
            assert not isinstance(app.screen, GatewayConfigScreen), (
                "Modal should have been dismissed"
            )

    @pytest.mark.asyncio
    async def test_save_valid(self) -> None:
        """Valid host/port -> POST to API, daemon.restart(), dismiss, refresh."""
        app = GatewayConfigTestApp(
            config={"host": "0.0.0.0", "port": 8080}, port=12345
        )

        # Mock httpx.AsyncClient for the POST inside the handler.
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True}
        mock_instance = MagicMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.post = AsyncMock(return_value=mock_response)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, GatewayConfigScreen)

            with patch("httpx.AsyncClient", return_value=mock_instance):
                await pilot.click("#btn-save-restart")
                await pilot.pause()

            # Verify API was called with correct payload
            mock_instance.post.assert_awaited_once_with(
                "http://127.0.0.1:12345/api/gateway/config",
                json={"host": "0.0.0.0", "port": 8080},
            )

            # daemon.restart should have been called
            app.daemon.restart.assert_called_once()

            # Modal should be dismissed
            assert not isinstance(app.screen, GatewayConfigScreen), (
                "Modal should be dismissed after save"
            )

    @pytest.mark.asyncio
    async def test_save_invalid_port(self) -> None:
        """Non-numeric port -> error notify, no HTTP call, no dismiss."""
        app = GatewayConfigTestApp(port=12345)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, GatewayConfigScreen)

            # Change port to something non-numeric
            port_input = screen.query_one("#input-port", Input)
            port_input.value = "not-a-number"

            # Spy on notify
            screen.notify = MagicMock()

            await pilot.click("#btn-save-restart")
            await pilot.pause()

            screen.notify.assert_called_once_with(
                "端口号必须是数字", title="网关配置", severity="error"
            )
            # Should still be on the modal (not dismissed)
            assert isinstance(app.screen, GatewayConfigScreen)
            # No HTTP call was made, no restart
            app.daemon.restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_port_none(self) -> None:
        """get_control_port returns None -> '网关未运行' notify, no API call."""
        app = GatewayConfigTestApp(port=None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, GatewayConfigScreen)

            screen.notify = MagicMock()

            await pilot.click("#btn-save-restart")
            await pilot.pause()

            screen.notify.assert_called_once_with(
                "网关未运行，无法保存配置", title="网关配置", severity="error"
            )
            assert isinstance(app.screen, GatewayConfigScreen)
            app.daemon.restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_api_error(self) -> None:
        """API returns {"ok": False, "error": ...} -> failure notify."""
        app = GatewayConfigTestApp(port=12345)

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "error": "port conflict"}
        mock_instance = MagicMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.post = AsyncMock(return_value=mock_response)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            screen = app.screen
            assert isinstance(screen, GatewayConfigScreen)
            screen.notify = MagicMock()

            with patch("httpx.AsyncClient", return_value=mock_instance):
                await pilot.click("#btn-save-restart")
                await pilot.pause()

            screen.notify.assert_called_once_with(
                "失败: port conflict", title="网关配置", severity="error"
            )
            # Daemon should NOT be restarted when API returns error
            app.daemon.restart.assert_not_called()
            # Modal should NOT be dismissed
            assert isinstance(app.screen, GatewayConfigScreen)


# =============================================================================
# GatewayPane tests
# =============================================================================


class TestGatewayPane:
    """Tests for the GatewayPane widget."""

    @pytest.mark.asyncio
    async def test_compose(self) -> None:
        """All expected widget IDs exist after mount."""
        app = GatewayPaneTestApp(status={"running": False}, port=None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(GatewayPane)

            # Status / info sections
            assert pane.query_one("#gateway-status", Static) is not None
            assert pane.query_one("#gateway-endpoints", Static) is not None
            assert pane.query_one("#gateway-stats", Static) is not None
            assert pane.query_one("#health-list", Static) is not None

            # Four buttons
            assert pane.query_one("#btn-start-gateway", Button) is not None
            assert pane.query_one("#btn-stop-gateway", Button) is not None
            assert pane.query_one("#btn-restart-gateway", Button) is not None
            assert pane.query_one("#btn-config", Button) is not None

            # Actions container
            assert pane.query_one("#gateway-actions", Horizontal) is not None

    @pytest.mark.asyncio
    async def test_not_running(self) -> None:
        """Gateway not running shows 未运行 and hides stop/restart buttons."""
        app = GatewayPaneTestApp(status={"running": False}, port=None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(GatewayPane)

            status_widget = pane.query_one("#gateway-status", Static)
            content = str(status_widget.content)
            assert "未运行" in content
            assert "red]" in content

            # Endpoints / stats show dim placeholder text
            endpoints = pane.query_one("#gateway-endpoints", Static)
            assert "等待启动" in str(endpoints.content)

            stats = pane.query_one("#gateway-stats", Static)
            assert "等待网关启动" in str(stats.content)

            # Start button visible, stop/restart hidden
            assert pane.query_one("#btn-start-gateway", Button).display is True
            assert pane.query_one("#btn-stop-gateway", Button).display is False
            assert pane.query_one("#btn-restart-gateway", Button).display is False

    @pytest.mark.asyncio
    async def test_running_full(self) -> None:
        """Running daemon with full status shows uptime, active model, icons."""
        status = {
            "running": True,
            "uptime": 3661,  # 1h 1m
            "active_model": "gpt-4",
            "request_count": 42,
            "provider_count": 3,
            "providers": [
                {"name": "ProviderA", "status": "up", "latency_ms": 100.0},
                {"name": "ProviderB", "status": "degraded", "latency_ms": 200.5},
                {"name": "ProviderC", "status": "down", "latency_ms": 300.0},
            ],
        }

        with patch("llmport.ui.screens.gateway.async_get_json") as mock_get:
            mock_get.return_value = {"host": "0.0.0.0", "port": 8080}

            app = GatewayPaneTestApp(status=status, port=12345)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(GatewayPane)

                # Status: running
                status_widget = pane.query_one("#gateway-status", Static)
                assert "运行中" in str(status_widget.content)
                assert "green]" in str(status_widget.content)

                # Endpoints
                endpoints = pane.query_one("#gateway-endpoints", Static)
                ep_content = str(endpoints.content)
                assert "http://0.0.0.0:8080" in ep_content
                assert "/openai/v1/" in ep_content
                assert "/anthropic/v1/" in ep_content
                assert "/api/*" in ep_content

                # Stats row
                stats = pane.query_one("#gateway-stats", Static)
                stats_text = str(stats.content)
                assert "1h 1m" in stats_text
                assert "gpt-4" in stats_text
                assert "42" in stats_text  # request_count
                assert "3" in stats_text  # provider_count

                # Button visibility
                assert pane.query_one("#btn-start-gateway", Button).display is False
                assert pane.query_one("#btn-stop-gateway", Button).display is True
                assert pane.query_one("#btn-restart-gateway", Button).display is True

                # Provider health icons
                health = pane.query_one("#health-list", Static)
                health_text = str(health.content)
                assert "🟢" in health_text, "up -> green circle"
                assert "🟡" in health_text, "degraded -> yellow circle"
                assert "🔴" in health_text, "down -> red circle"

                # Verify latency text
                assert "100ms" in health_text
                assert "200ms" in health_text  # 200.5 rounded to 200
                assert "300ms" in health_text

                # Names present
                assert "ProviderA" in health_text
                assert "ProviderB" in health_text
                assert "ProviderC" in health_text

    @pytest.mark.asyncio
    async def test_empty_providers(self) -> None:
        """Empty providers list shows '暂无供应商'."""
        status = {
            "running": True,
            "uptime": 100,
            "active_model": "gpt-4",
            "request_count": 10,
            "provider_count": 0,
            "providers": [],
        }

        with patch("llmport.ui.screens.gateway.async_get_json") as mock_get:
            mock_get.return_value = {"host": "127.0.0.1", "port": 11434}

            app = GatewayPaneTestApp(status=status, port=12345)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(GatewayPane)
                health = pane.query_one("#health-list", Static)
                assert "暂无供应商" in str(health.content)

    @pytest.mark.asyncio
    async def test_health_icons_all_states(self) -> None:
        """Verify icon mapping: up->🟢, degraded->🟡, down->🔴, unknown->⚪."""
        status = {
            "running": True,
            "uptime": 0,
            "active_model": None,
            "request_count": 0,
            "provider_count": 4,
            "providers": [
                {"name": "Alpha", "status": "up", "latency_ms": 5.0},
                {"name": "Beta", "status": "degraded", "latency_ms": 150.0},
                {"name": "Gamma", "status": "down", "latency_ms": 999.0},
                {"name": "Delta", "status": "unknown", "latency_ms": 0.0},
            ],
        }

        with patch("llmport.ui.screens.gateway.async_get_json") as mock_get:
            mock_get.return_value = {"host": "127.0.0.1", "port": 11434}

            app = GatewayPaneTestApp(status=status, port=12345)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                pane = app.query_one(GatewayPane)
                health = pane.query_one("#health-list", Static)
                health_text = str(health.content)

                assert "🟢" in health_text
                assert "🟡" in health_text
                assert "🔴" in health_text
                assert "⚪" in health_text
                assert "Alpha" in health_text
                assert "Beta" in health_text
                assert "Gamma" in health_text
                assert "Delta" in health_text

    @pytest.mark.asyncio
    async def test_start_button(self) -> None:
        """Pressing start calls daemon.start(), notifies, refreshes."""
        app = GatewayPaneTestApp(status={"running": False}, port=None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(GatewayPane)
            pane.notify = MagicMock()

            await pilot.click("#btn-start-gateway")
            await pilot.pause()

            app.daemon.start.assert_called_once()
            pane.notify.assert_called_once_with("网关已启动", title="网关")

    @pytest.mark.asyncio
    async def test_stop_button(self) -> None:
        """Pressing stop calls daemon.stop(), notifies."""
        status = {"running": True, "uptime": 0}
        app = GatewayPaneTestApp(status=status, port=12345)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(GatewayPane)
            pane.notify = MagicMock()

            # Stop button should be visible
            assert pane.query_one("#btn-stop-gateway", Button).display is True

            await pilot.click("#btn-stop-gateway")
            await pilot.pause()

            app.daemon.stop.assert_called_once()
            pane.notify.assert_called_once_with("网关已停止", title="网关")

    @pytest.mark.asyncio
    async def test_restart_button(self) -> None:
        """Pressing restart calls daemon.restart(), notifies."""
        status = {"running": True, "uptime": 0}
        app = GatewayPaneTestApp(status=status, port=12345)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            pane = app.query_one(GatewayPane)
            pane.notify = MagicMock()

            assert pane.query_one("#btn-restart-gateway", Button).display is True

            await pilot.click("#btn-restart-gateway")
            await pilot.pause()

            app.daemon.restart.assert_called_once()
            pane.notify.assert_called_once_with("网关已重启", title="网关")

    @pytest.mark.asyncio
    async def test_config_button(self) -> None:
        """Pressing config pushes GatewayConfigScreen with correct config."""
        status = {"running": True, "uptime": 0}
        with patch("llmport.ui.screens.gateway.async_get_json") as mock_get:
            mock_get.return_value = {"host": "1.2.3.4", "port": 9999}

            app = GatewayPaneTestApp(status=status, port=12345)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()

                mock_push = AsyncMock()
                app.push_screen = mock_push
                await pilot.click("#btn-config")
                await pilot.pause()

                mock_push.assert_awaited_once()
                pushed = mock_push.call_args[0][0]
                assert isinstance(pushed, GatewayConfigScreen)
                assert pushed.config == {"host": "1.2.3.4", "port": 9999}

    @pytest.mark.asyncio
    async def test_config_button_daemon_not_running(self) -> None:
        """Config button with daemon not running uses default config."""
        app = GatewayPaneTestApp(status={"running": False}, port=None)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            mock_push = AsyncMock()
            app.push_screen = mock_push
            await pilot.click("#btn-config")
            await pilot.pause()

            mock_push.assert_awaited_once()
            pushed = mock_push.call_args[0][0]
            assert isinstance(pushed, GatewayConfigScreen)
            # When port is None, default config is used
            assert pushed.config == {"host": "127.0.0.1", "port": 11434}
