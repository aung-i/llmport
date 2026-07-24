"""TUI integration tests using Textual run_test."""

import os
import tempfile
import pytest


@pytest.mark.asyncio
async def test_provider_form_screen_composes_without_error():
    """ProviderFormScreen mounts without InvalidSelectValueError.

    Regression test: the Select widget for protocol used wrong tuple
    ordering (value, label) instead of (label, value), causing
    'Illegal select value' crash when the screen mounted.
    """
    from llmport.app import LlmPortApp

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CONFIG_HOME"] = tmp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            # Wait for daemon start + onboarding
            await pilot.pause(2.0)

            # Find and click the onboarding "开始设置" button
            from llmport.ui.screens.onboarding import OnboardingScreen
            onboard = None
            for s in app.screen_stack:
                if isinstance(s, OnboardingScreen):
                    onboard = s
                    break
            assert onboard is not None, "OnboardingScreen should be showing"

            # Click "开始设置" — this starts the daemon and pushes ProviderFormScreen
            await pilot.click("#btn-start-setup")
            # Allow time for daemon start (1s sleep in onboarding) + push
            await pilot.pause(2.0)

            # ProviderFormScreen should now be in the screen stack
            from llmport.ui.screens.providers import ProviderFormScreen
            form = None
            for s in app.screen_stack:
                if isinstance(s, ProviderFormScreen):
                    form = s
                    break
            assert form is not None, "ProviderFormScreen should be pushed after clicking 开始设置"


@pytest.mark.asyncio
async def test_onboarding_flow_completes_without_error():
    """Full onboarding flow: start → click 开始设置 → ProviderFormScreen appears."""
    from llmport.app import LlmPortApp
    from llmport.ui.screens.onboarding import OnboardingScreen
    from llmport.ui.screens.providers import ProviderFormScreen

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CONFIG_HOME"] = tmp

        app = LlmPortApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(2.0)

            # Onboarding should be showing
            onboard = None
            for s in app.screen_stack:
                if isinstance(s, OnboardingScreen):
                    onboard = s
                    break
            assert onboard is not None

            # Click 开始设置 — daemon start + ProviderFormScreen push
            await pilot.click("#btn-start-setup")
            # Allow time for daemon start (1s sleep in onboarding) + push
            await pilot.pause(2.5)

            # ProviderFormScreen should be mounted without crash
            form_found = False
            for s in app.screen_stack:
                if isinstance(s, ProviderFormScreen):
                    form_found = True
                    break
            assert form_found, (
                "ProviderFormScreen should be in screen stack after clicking 开始设置. "
                "If it crashed, the app._exception would surface here."
            )
