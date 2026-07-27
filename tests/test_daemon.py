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
        from llmport.config.store import ConfigStore

        dm = DaemonManager()
        expected = Path.home() / ".config" / "llmport"
        assert dm.dir == expected
        assert dm.pid_path == expected / "daemon.pid"
        assert isinstance(dm.store, ConfigStore)

    def test_default_config_dir_with_xdg(self, monkeypatch):
        """Default config_dir uses XDG_CONFIG_HOME when set."""
        monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
        from llmport.daemon import DaemonManager
        from llmport.config.store import ConfigStore

        dm = DaemonManager()
        assert dm.dir == Path("/custom/xdg/llmport")
        assert dm.pid_path == Path("/custom/xdg/llmport/daemon.pid")
        assert isinstance(dm.store, ConfigStore)

    def test_custom_config_dir(self):
        """Custom config_dir is used when provided."""
        from llmport.daemon import DaemonManager
        from llmport.config.store import ConfigStore

        dm = DaemonManager(config_dir="/my/custom/path")
        assert dm.dir == Path("/my/custom/path")
        assert dm.pid_path == Path("/my/custom/path/daemon.pid")
        assert isinstance(dm.store, ConfigStore)


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
        """Returns None when no PID file exists (daemon not running)."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.get_control_port() is None

    def test_valid_pid_file(self, tmp_path):
        """Returns the gateway port from the PID file's 'port' field when running."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"):  # process is "alive"
            assert dm.get_control_port() == 23456

    def test_malformed_pid_file(self, tmp_path):
        """Returns None when PID file contains malformed JSON."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("not-json")
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.get_control_port() is None

    def test_not_running_returns_none(self, tmp_path):
        """Returns None when the daemon is not running (dead process)."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError()):
            assert dm.get_control_port() is None


class TestStop:
    """DaemonManager.stop()"""

    def test_cleanup_after_api_call(self, tmp_path):
        """stop() POSTs to the gateway port's /api/daemon/stop and removes the PID file."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError()), patch.object(
            time, "sleep"
        ), patch("llmport.daemon.httpx.Client") as MockClient:
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


class TestStart:
    """DaemonManager.start()"""

    def test_start_returns_true_when_ready(self, tmp_path):
        """start() returns True once /api/status answers 200 and our daemon is alive."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen") as MockPopen, \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch.object(dm, "is_running", side_effect=[False, True]):
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            assert dm.start() is True
            MockPopen.assert_called_once()

    def test_start_returns_false_when_port_held_by_other_process(self, tmp_path):
        """If /api/status answers but our daemon is not alive (port held by
        another process), start() must report failure, not false success."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen"), \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch.object(dm, "is_running", return_value=False):
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            assert dm.start() is False

    def test_start_returns_false_on_timeout(self, tmp_path):
        """start() returns False when the gateway never becomes ready."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen"), \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch("llmport.daemon.time.monotonic", side_effect=[0, 0, 100]):
            # /api/status keeps failing -> loop body raises -> never ready
            MockClient.return_value.__enter__.return_value.get.side_effect = Exception
            assert dm.start() is False

    def test_start_when_already_running_is_noop(self, tmp_path):
        """start() returns True without spawning when already running."""
        from llmport.daemon import DaemonManager

        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 11434}))
        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"), patch("llmport.daemon.subprocess.Popen") as MockPopen:
            assert dm.start() is True
            MockPopen.assert_not_called()


class TestRestart:
    """DaemonManager.restart()"""

    def test_restart_calls_stop_then_start(self, tmp_path):
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(dm, "stop") as mock_stop, \
             patch.object(dm, "start", return_value=True) as mock_start, \
             patch("llmport.daemon.time.sleep"):
            dm.restart()
            mock_stop.assert_called_once()
            mock_start.assert_called_once()


class TestWaitForExit:
    """DaemonManager._wait_for_exit()"""

    def test_returns_true_when_process_gone(self, tmp_path):
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError), patch.object(time, "sleep"):
            assert dm._wait_for_exit(9999, 1.0) is True

    def test_returns_false_on_timeout(self, tmp_path):
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"), patch.object(time, "sleep"), \
             patch("llmport.daemon.time.monotonic", side_effect=[0, 0, 100]):
            assert dm._wait_for_exit(9999, 1.0) is False


class TestStopEscalation:
    """stop() escalates to SIGTERM / SIGKILL when the process won't exit."""

    def test_escalates_to_sigterm_and_sigkill(self, tmp_path):
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        # _wait_for_exit always "times out" -> full signal escalation.
        with patch.object(dm, "_wait_for_exit", return_value=False), \
             patch.object(os, "kill") as mock_kill, \
             patch("llmport.daemon.httpx.Client"):
            dm.stop()
        sent = [call.args[1] for call in mock_kill.call_args_list]
        assert 15 in sent  # SIGTERM
        assert 9 in sent   # SIGKILL
        assert not pid_file.exists()


class TestGatewayPortFallback:
    """_gateway_port falls back to config, then default."""

    def test_falls_back_to_config(self, tmp_path):
        """When the PID file has no port, the config gateway port is used."""
        from llmport.daemon import DaemonManager
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        store.save_config({
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 22000},
            "providers": [], "models": [],
        })
        dm = DaemonManager(config_dir=str(tmp_path))
        # No PID file -> port comes from config.
        assert dm._gateway_port() == 22000

    def test_falls_back_to_default(self, tmp_path):
        """When no PID file and no config, the default port is used."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm._gateway_port() == 11434


class TestGetStatus:
    """get_status() / async_get_status()"""

    def test_get_status_not_running(self, tmp_path):
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert dm.get_status() == {"running": False}

    @pytest.mark.asyncio
    async def test_async_get_status_not_running(self, tmp_path):
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        assert await dm.async_get_status() == {"running": False}

    def test_get_status_running(self, tmp_path):
        """get_status merges the control API response when running."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"), patch("llmport.daemon.httpx.Client") as MockClient:
            resp = MockClient.return_value.__enter__.return_value.get.return_value
            resp.status_code = 200
            resp.json.return_value = {"request_count": 5, "models": ["gpt-x"]}
            status = dm.get_status()
        assert status["running"] is True
        assert status["request_count"] == 5
        assert status["models"] == ["gpt-x"]
