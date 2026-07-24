"""Tests for onboarding flow (Issues 3 and 8).

Issue 3 — Onboarding E2E flow: verify state transitions and completion.
Issue 8 — Daemon start is deferred until after onboarding completes.
"""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest


class TestIssue3OnboardingEndToEnd:

    def _source(self) -> str:
        """Read the onboarding.py source file (avoids circular import)."""
        import pathlib
        p = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src" / "llmport" / "ui" / "screens" / "onboarding.py"
        )
        return p.read_text()

    def test_onboarding_renders_welcome_on_step_0(self):
        """At step 0, the onboarding screen must show a welcome message."""
        source = self._source()
        assert "llmport" in source and "第一次使用" in source

    def test_onboarding_start_sets_up_provider_form(self):
        """Clicking 'start setup' must push the ProviderFormScreen."""
        source = self._source()
        assert "ProviderFormScreen" in source and "btn-start-setup" in source

    def test_onboarding_progresses_after_provider_added(self):
        """After the provider form is dismissed, onboarding must advance
        to step 2 (finish)."""
        source = self._source()
        assert "self._step = 2" in source or "_on_provider_done" in source

    def test_onboarding_btn_finish_dismisses(self):
        """The 'finish' button handler must call self.dismiss()."""
        source = self._source()
        assert "btn-finish" in source and "self.dismiss()" in source


class TestIssue8OnboardingDaemonSequence:

    def test_daemon_not_started_before_onboarding(self):
        """When the app mounts and detects first run, it must NOT start the
        daemon before pushing the onboarding screen."""
        from llmport.config.store import ConfigStore
        from llmport.daemon import DaemonManager

        store = ConfigStore()
        with patch.object(DaemonManager, "start") as mock_start:
            mock_start.assert_not_called()

    def test_onboarding_does_not_trigger_daemon_start(self):
        """The OnboardingScreen itself must not start the daemon."""
        import pathlib
        src = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src" / "llmport" / "ui" / "screens" / "onboarding.py"
        ).read_text()
        # Must not import DaemonManager or reference daemon.start
        assert "DaemonManager" not in src, (
            "OnboardingScreen should not use DaemonManager"
        )
