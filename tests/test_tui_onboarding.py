"""Tests for OnboardingScreen — step transitions, rendering, and button handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# compose()
# ---------------------------------------------------------------------------

class TestCompose:
    """Widget structure produced by compose()."""

    @pytest.mark.asyncio
    async def test_compose_via_mount(self):
        """OnboardingScreen mounts and renders expected DOM structure."""
        from llmport.ui.screens.onboarding import OnboardingScreen
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield OnboardingScreen()

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen

            # Verify the DOM structure
            container = screen.query_one("#onboard-container")
            assert container is not None

            content = screen.query_one("#onboard-content")
            assert content is not None

            buttons = screen.query_one("#onboard-buttons")
            assert buttons is not None

            btn_start = screen.query_one("#btn-start-setup")
            assert btn_start is not None
            assert btn_start.visible  # visible in step 0

            btn_finish = screen.query_one("#btn-finish")
            assert btn_finish is not None
            assert not btn_finish.visible  # hidden in step 0


# ---------------------------------------------------------------------------
# on_mount / _render_step
# ---------------------------------------------------------------------------

class TestRenderStep:
    """Step rendering via _render_step()."""

    @pytest.mark.asyncio
    async def test_on_mount_shows_step_0_welcome(self):
        """on_mount should render step 0 (welcome) by default."""
        from llmport.ui.screens.onboarding import OnboardingScreen

        screen = OnboardingScreen()
        content_mock = MagicMock()
        btn_start = MagicMock()
        btn_finish = MagicMock()

        def query_one_side_effect(selector, *args, **kwargs):
            if selector == "#onboard-content":
                return content_mock
            if selector == "#btn-start-setup":
                return btn_start
            if selector == "#btn-finish":
                return btn_finish
            raise AssertionError(f"Unexpected selector: {selector}")

        screen.query_one = MagicMock(side_effect=query_one_side_effect)
        screen._render_step = AsyncMock()
        await screen.on_mount()
        screen._render_step.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_render_step_0_shows_welcome_hides_finish(self):
        """Step 0: btn-start-setup visible, btn-finish hidden, welcome text."""
        from llmport.ui.screens.onboarding import OnboardingScreen

        screen = OnboardingScreen()
        screen._step = 0

        content_mock = MagicMock()
        btn_start = MagicMock()
        btn_finish = MagicMock()
        btn_start.visible = True
        btn_finish.visible = False

        def query_one_side_effect(selector, *args, **kwargs):
            if selector == "#onboard-content":
                return content_mock
            if selector == "#btn-start-setup":
                return btn_start
            if selector == "#btn-finish":
                return btn_finish
            raise AssertionError(f"Unexpected selector: {selector}")

        screen.query_one = MagicMock(side_effect=query_one_side_effect)

        await screen._render_step()

        assert btn_start.visible is True
        assert btn_finish.visible is False
        content_mock.update.assert_called_once()
        assert "llmport" in content_mock.update.call_args[0][0]
        assert "第一次使用" in content_mock.update.call_args[0][0]

    @pytest.mark.asyncio
    async def test_render_step_2_shows_finish_hides_start(self):
        """Step 2: btn-start-setup hidden, btn-finish visible, completion text."""
        from llmport.ui.screens.onboarding import OnboardingScreen

        screen = OnboardingScreen()
        screen._step = 2

        content_mock = MagicMock()
        btn_start = MagicMock()
        btn_finish = MagicMock()
        btn_start.visible = True
        btn_finish.visible = False

        def query_one_side_effect(selector, *args, **kwargs):
            if selector == "#onboard-content":
                return content_mock
            if selector == "#btn-start-setup":
                return btn_start
            if selector == "#btn-finish":
                return btn_finish
            raise AssertionError(f"Unexpected selector: {selector}")

        screen.query_one = MagicMock(side_effect=query_one_side_effect)

        await screen._render_step()

        assert btn_start.visible is False
        assert btn_finish.visible is True
        content_mock.update.assert_called_once()
        assert "设置完成" in content_mock.update.call_args[0][0]


# ---------------------------------------------------------------------------
# Button handlers
# ---------------------------------------------------------------------------

class TestButtonHandlers:
    """Button press event handlers."""

    @pytest.mark.asyncio
    async def test_btn_finish_dismisses(self):
        """Pressing btn-finish should dismiss the modal screen."""
        from llmport.ui.screens.onboarding import OnboardingScreen

        screen = OnboardingScreen()
        screen.dismiss = MagicMock()

        btn = MagicMock()
        btn.id = "btn-finish"
        event = MagicMock()
        event.button = btn

        await screen.on_button_pressed(event)
        screen.dismiss.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_provider_done_sets_step_2_and_renders(self):
        """_on_provider_done should set step=2 and call _render_step."""
        from llmport.ui.screens.onboarding import OnboardingScreen

        screen = OnboardingScreen()
        screen._step = 0
        screen._render_step = AsyncMock()

        await screen._on_provider_done(None)

        assert screen._step == 2
        screen._render_step.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_btn_start_setup_init_first_run(self):
        """Pressing btn-start-setup calls init_first_run on ConfigStore
        and pushes ProviderFormScreen."""
        from llmport.ui.screens.onboarding import OnboardingScreen
        from textual.app import App, ComposeResult
        from textual.widgets import Button

        class TestApp(App):
            def __init__(self):
                super().__init__()
                self.daemon = MagicMock()
                self.daemon.is_running.return_value = False
                self.daemon.start.return_value = None

            def compose(self) -> ComposeResult:
                yield OnboardingScreen()

        with (
            patch("llmport.ui.screens.onboarding.ConfigStore") as mock_store_cls,
            patch("llmport.ui.screens.providers.ProviderFormScreen") as mock_pfs_cls,
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store
            mock_pfs = MagicMock()
            mock_pfs_cls.return_value = mock_pfs

            app = TestApp()
            async with app.run_test(size=(80, 24)) as pilot:
                # Allow app to mount fully
                await pilot.pause()

                # Mock push_screen to invoke the callback immediately
                async def push_screen_and_invoke_callback(_screen, callback=None):
                    if callback:
                        await callback(None)

                app.push_screen = AsyncMock(
                    side_effect=push_screen_and_invoke_callback
                )

                # Get the mounted screen and directly invoke the handler
                onboard = app.query_one(OnboardingScreen)
                btn = onboard.query_one("#btn-start-setup", Button)
                event = Button.Pressed(btn)
                await onboard.on_button_pressed(event)
                await pilot.pause()

                # Should have called init_first_run
                mock_store.init_first_run.assert_called_once()
                # Should have started the daemon
                app.daemon.start.assert_called_once()
                # After callback, step should be 2
                assert onboard._step == 2
