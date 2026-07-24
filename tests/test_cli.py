"""Tests for the CLI entry point (src/llmport/cli.py).

Avoids clashing with tests/test_cli_commands.py.
"""

import sys

import pytest
from unittest.mock import patch


class TestArgparse:
    """Test argparse argument parsing."""

    def test_version(self):
        """--version prints version info and exits with code 0."""
        with patch.object(sys, "argv", ["llmport", "--version"]):
            with pytest.raises(SystemExit, match="0"):
                from llmport.cli import main

                main()

    def test_daemon_flag_calls_run_daemon(self):
        """--daemon flag causes run_daemon() to be called."""
        with patch.object(sys, "argv", ["llmport", "--daemon"]):
            with patch("llmport.cli.run_daemon") as mock_run:
                from llmport.cli import main

                main()
                mock_run.assert_called_once()

    @pytest.mark.parametrize("action", ["start", "stop", "restart", "status"])
    def test_action_choices_recognized(self, action):
        """All action choices ('start', 'stop', 'restart', 'status') are accepted."""
        with patch.object(sys, "argv", ["llmport", action]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                from llmport.cli import main

                main()
                MockDM.assert_called_once()


class TestStatusCommand:
    """Test the 'status' command."""

    def test_status_when_not_running(self, capsys):
        """status prints 'Gateway is not running.' when daemon is not running."""
        with patch.object(sys, "argv", ["llmport", "status"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                from llmport.cli import main

                main()
                captured = capsys.readouterr()
                assert "Gateway is not running." in captured.out


class TestStartCommand:
    """Test the 'start' command."""

    def test_start_when_not_running(self, capsys):
        """start calls DaemonManager.start() and prints 'Gateway started.'."""
        with patch.object(sys, "argv", ["llmport", "start"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                from llmport.cli import main

                main()
                mock_dm.start.assert_called_once()
                captured = capsys.readouterr()
                assert "Gateway started." in captured.out

    def test_start_when_running(self, capsys):
        """start prints 'Gateway is already running.' when daemon is already running."""
        with patch.object(sys, "argv", ["llmport", "start"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = True
                from llmport.cli import main

                main()
                mock_dm.start.assert_not_called()
                captured = capsys.readouterr()
                assert "Gateway is already running." in captured.out


class TestStopCommand:
    """Test the 'stop' command."""

    def test_stop_when_not_running(self, capsys):
        """stop prints 'Gateway is not running.' when daemon is not running."""
        with patch.object(sys, "argv", ["llmport", "stop"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                from llmport.cli import main

                main()
                mock_dm.stop.assert_not_called()
                captured = capsys.readouterr()
                assert "Gateway is not running." in captured.out

    def test_stop_when_running(self, capsys):
        """stop calls DaemonManager.stop() and prints 'Gateway stopped.'."""
        with patch.object(sys, "argv", ["llmport", "stop"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = True
                from llmport.cli import main

                main()
                mock_dm.stop.assert_called_once()
                captured = capsys.readouterr()
                assert "Gateway stopped." in captured.out
