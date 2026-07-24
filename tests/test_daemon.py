"""Tests for daemon lifecycle management (src/llmport/daemon.py)."""

import json
import os
import time
from pathlib import Path

import pytest
from unittest.mock import patch


class TestDaemonManagerInit:
    """DaemonManager.__init__"""

    def test_default_config_dir_without_xdg(self, monkeypatch):
        """Default config_dir falls back to ~/.config/llmport when XDG_CONFIG_HOME is not set."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        from llmport.daemon import DaemonManager

        dm = DaemonManager()
        expected = Path.home() / ".config" / "llmport"
        assert dm.dir == expected
        assert dm.pid_path == expected / "daemon.pid"

    def test_default_config_dir_with_xdg(self, monkeypatch):
        """Default config_dir uses XDG_CONFIG_HOME when set."""
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
        from llmport.daemon import DaemonManager

        dm = DaemonManager()
        assert dm.dir == Path("/custom/xdg/llmport")
        assert dm.pid_path == Path("/custom/xdg/llmport/daemon.pid")

    def test_custom_config_dir(self):
        """Custom config_dir is used when provided."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir="/my/custom/path")
        assert dm.dir == Path("/my/custom/path")
        assert dm.pid_path == Path("/my/custom/path/daemon.pid")


class TestIsRunning:
    """DaemonManager.is_running()"""

    def test_no_pid_file(self, tmp_path):
        """Returns False when PID file does not exist."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.is_running() is False

    def test_valid_running_process(self, tmp_path):
        """Returns True when PID file exists and process responds to os.kill(pid, 0)."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill") as mock_kill:
            result = dm.is_running()
        assert result is True
        mock_kill.assert_called_once_with(9999, 0)

    def test_dead_process(self, tmp_path):
        """Returns False when os.kill raises OSError (process is dead)."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError()):
            result = dm.is_running()
        assert result is False

    def test_malformed_json(self, tmp_path):
        """Returns False when PID file contains malformed JSON."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("not-json")
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.is_running() is False

    def test_null_pid(self, tmp_path):
        """Returns False when PID is null in the PID file."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": None}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.is_running() is False


class TestGetControlPort:
    """DaemonManager.get_control_port()"""

    def test_no_pid_file(self, tmp_path):
        """Returns None when no PID file exists."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.get_control_port() is None

    def test_valid_pid_file(self, tmp_path):
        """Returns the control port from a valid PID file."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "control_port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.get_control_port() == 23456

    def test_malformed_pid_file(self, tmp_path):
        """Returns None when PID file contains malformed JSON."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("not-json")
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.get_control_port() is None


class TestStop:
    """DaemonManager.stop()"""

    def test_cleanup_when_no_port(self, tmp_path):
        """PID file is cleaned up when control port is not present in the file."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError()), patch.object(
            time, "sleep"
        ):
            dm.stop()
        assert not pid_file.exists()

    def test_cleanup_after_api_call(self, tmp_path):
        """PID file is cleaned up after the control API is called."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "control_port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError()), patch.object(
            time, "sleep"
        ), patch("httpx.Client") as MockClient:
            mock_client_instance = MockClient.return_value
            mock_client_cm = mock_client_instance.__enter__.return_value
            dm.stop()
            mock_client_cm.post.assert_called_once_with(
                "http://127.0.0.1:23456/api/daemon/stop"
            )
        assert not pid_file.exists()

    def test_malformed_json(self, tmp_path):
        """Malformed PID file JSON is handled gracefully (no crash, file removed)."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("not-json")
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill") as mock_kill, patch.object(time, "sleep"):
            dm.stop()
            mock_kill.assert_not_called()
        assert not pid_file.exists()
