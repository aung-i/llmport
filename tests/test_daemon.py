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
        """Returns True when PID file exists and the pid is our live daemon."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill") as mock_kill, patch.object(
            dm, "_process_cmdline", return_value="/p/python -m llmport --daemon"
        ):
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

    def test_dead_process_cleans_stale_pid_file(self, tmp_path):
        """A dead pid (stale file) is cleared so later commands ignore it."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill", side_effect=OSError()):
            assert dm.is_running() is False
        assert not pid_file.exists()

    def test_recycled_pid_treated_as_not_running(self, tmp_path):
        """A pid reused by an unrelated process is not our daemon -> not running,
        and the stale pid file is cleared."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline", return_value="/usr/bin/some-other-program"
        ):
            assert dm.is_running() is False
        assert not pid_file.exists()

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
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline", return_value="/p/python -m llmport --daemon"
        ):  # process is "alive" and ours
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

    def test_stop_cleans_pid_file_via_signal(self, tmp_path):
        """stop() shuts down our daemon via SIGTERM (no HTTP control API) and
        removes the PID file."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill") as mock_kill, patch.object(
            dm, "_process_cmdline", return_value="/p/python -m llmport --daemon"
        ), patch.object(dm, "_wait_for_exit", return_value=True), patch(
            "llmport.daemon.httpx.Client"
        ) as MockClient:
            dm.stop()
            # Control is via SIGTERM, not HTTP: no request is made.
            mock_kill.assert_any_call(9999, 15)
            MockClient.return_value.__enter__.return_value.post.assert_not_called()
        assert not pid_file.exists()

    def test_stop_does_not_signal_recycled_pid(self, tmp_path):
        """A pid reused by an unrelated process is NOT signaled -- only the
        stale pid file is cleared, so an unrelated process is never killed."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill") as mock_kill, patch.object(
            dm, "_process_cmdline", return_value="/usr/bin/some-other-program"
        ):
            dm.stop()
            # No signal (15/SIGTERM or 9/SIGKILL) sent to the recycled pid.
            sent = [call.args[1] for call in mock_kill.call_args_list]
            assert 15 not in sent and 9 not in sent
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
        """start() returns True once /health answers 200 and our daemon is alive."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen") as MockPopen, \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch.object(dm, "is_running", side_effect=[False, True]), \
             patch.object(dm, "_port_answers_health", return_value=False):
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            assert dm.start() is True
            MockPopen.assert_called_once()

    def test_start_returns_false_when_orphan_on_port(self, tmp_path):
        """If /health answers but our daemon is not alive (orphan / port held by
        another process), start() must report failure, not false success or a
        duplicate spawn."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen") as MockPopen, \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch.object(dm, "is_running", return_value=False):
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            assert dm.start() is False
            MockPopen.assert_not_called()

    def test_start_returns_false_on_timeout(self, tmp_path):
        """start() returns False when the gateway never becomes ready."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen"), \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch("llmport.daemon.time.monotonic", side_effect=[0, 0, 100]):
            # /health keeps failing -> loop body raises -> never ready
            MockClient.return_value.__enter__.return_value.get.side_effect = Exception
            assert dm.start() is False

    def test_start_when_already_running_is_noop(self, tmp_path):
        """start() returns True without spawning when already running."""
        from llmport.daemon import DaemonManager

        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 11434}))
        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline", return_value="/p/python -m llmport --daemon"
        ), patch("llmport.daemon.subprocess.Popen") as MockPopen:
            assert dm.start() is True
            MockPopen.assert_not_called()

    def test_start_passes_host_port_to_subprocess(self, tmp_path):
        """start(host, port) forwards --host/--port on the daemon subprocess argv
        so the gateway priority chain (CLI > providers.yaml > default) works."""
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.subprocess.Popen") as MockPopen, \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch.object(dm, "is_running", side_effect=[False, True]), \
             patch.object(dm, "_port_answers_health", return_value=False):
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            dm.start(host="127.0.0.1", port=9999)
        cmd = MockPopen.call_args[0][0]
        assert "--host" in cmd and "127.0.0.1" in cmd
        assert "--port" in cmd and "9999" in cmd


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
             patch.object(
                 dm, "_process_cmdline",
                 return_value="/p/python -m llmport --daemon"
             ), \
             patch("llmport.daemon.httpx.Client"):
            dm.stop()
        sent = [call.args[1] for call in mock_kill.call_args_list]
        assert 15 in sent  # SIGTERM
        assert 9 in sent   # SIGKILL
        assert not pid_file.exists()


class TestGatewayPortFallback:
    """_gateway_port falls back to config, then default."""

    def test_falls_back_to_config(self, tmp_path):
        """When the PID file has no port, the providers.yaml gateway port is used."""
        from llmport.daemon import DaemonManager
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        cfg = store.load_config()
        cfg["gateway"] = {"host": "127.0.0.1", "port": 22000}
        store.save_config(cfg)
        dm = DaemonManager(config_dir=str(tmp_path))
        # No PID file -> port comes from providers.yaml.
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
        """get_status merges the /health liveness response when running."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(json.dumps({"pid": 9999, "port": 23456}))
        from llmport.daemon import DaemonManager

        dm = DaemonManager(config_dir=str(tmp_path))
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline", return_value="/p/python -m llmport --daemon"
        ), patch("llmport.daemon.httpx.Client") as MockClient:
            resp = MockClient.return_value.__enter__.return_value.get.return_value
            resp.status_code = 200
            resp.json.return_value = {"status": "ok"}
            status = dm.get_status()
        assert status["running"] is True
        assert status["status"] == "ok"


class TestResolveGateway:
    """resolve_gateway: CLI args > providers.yaml > default."""

    def test_cli_port_overrides_config(self, tmp_path):
        from llmport.daemon import resolve_gateway
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        cfg = store.load_config()
        cfg["gateway"] = {"host": "127.0.0.1", "port": 22000}
        store.save_config(cfg)
        gw = resolve_gateway(store, cli_host=None, cli_port=33000)
        assert gw == {"host": "127.0.0.1", "port": 33000}

    def test_config_overrides_default(self, tmp_path):
        from llmport.daemon import resolve_gateway
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path))
        store.init_first_run()
        cfg = store.load_config()
        cfg["gateway"] = {"host": "127.0.0.1", "port": 22000}
        store.save_config(cfg)
        assert resolve_gateway(store) == {"host": "127.0.0.1", "port": 22000}

    def test_default_when_no_config(self, tmp_path):
        from llmport.daemon import resolve_gateway
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path))  # no providers.yaml yet
        assert resolve_gateway(store) == {"host": "127.0.0.1", "port": 11434}

    def test_corrupt_providers_file_falls_back_to_default(self, tmp_path):
        from llmport.daemon import resolve_gateway
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path))
        store.dir.mkdir(parents=True, exist_ok=True)
        store.providers_path.write_text("- not a dict\n")
        assert resolve_gateway(store) == {"host": "127.0.0.1", "port": 11434}


class TestArgvFlagValue:
    """_argv_flag_value parses --flag value / --flag=value from sys.argv."""

    def test_space_separated(self, monkeypatch):
        from llmport.daemon import _argv_flag_value
        monkeypatch.setattr("sys.argv", ["llmport", "--daemon", "--host", "1.2.3.4"])
        assert _argv_flag_value("--host") == "1.2.3.4"

    def test_equals_separated(self, monkeypatch):
        from llmport.daemon import _argv_flag_value
        monkeypatch.setattr("sys.argv", ["llmport", "--daemon", "--port=9999"])
        assert _argv_flag_value("--port") == "9999"

    def test_missing_flag_returns_none(self, monkeypatch):
        from llmport.daemon import _argv_flag_value
        monkeypatch.setattr("sys.argv", ["llmport", "--daemon"])
        assert _argv_flag_value("--host") is None


class TestPidIsOurDaemon:
    """_pid_is_our_daemon: liveness + command-line identity check."""

    def _dm(self, tmp_path):
        from llmport.daemon import DaemonManager
        return DaemonManager(config_dir=str(tmp_path))

    def test_dead_pid_is_not_ours(self, tmp_path):
        dm = self._dm(tmp_path)
        with patch.object(os, "kill", side_effect=OSError()):
            assert dm._pid_is_our_daemon(9999) is False

    def test_alive_but_cmdline_unavailable_falls_back_to_true(self, tmp_path):
        """ps unavailable -> can't verify -> trust liveness (never worse than old behavior)."""
        dm = self._dm(tmp_path)
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline", return_value=None
        ):
            assert dm._pid_is_our_daemon(9999) is True

    def test_our_daemon_cmdline_is_ours(self, tmp_path):
        dm = self._dm(tmp_path)
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline",
            return_value="/venv/bin/python -m llmport --daemon --port 11434",
        ):
            assert dm._pid_is_our_daemon(9999) is True

    def test_unrelated_cmdline_is_not_ours(self, tmp_path):
        """A recycled pid running an unrelated program is rejected (pid reuse)."""
        dm = self._dm(tmp_path)
        with patch.object(os, "kill"), patch.object(
            dm, "_process_cmdline", return_value="/usr/bin/node server.js"
        ):
            assert dm._pid_is_our_daemon(9999) is False


class TestPortAnswersHealth:
    """_port_answers_health: quick /health probe for orphan detection."""

    def test_true_when_200(self, tmp_path):
        from llmport.daemon import DaemonManager
        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            assert dm._port_answers_health(11434) is True

    def test_false_on_non_200(self, tmp_path):
        from llmport.daemon import DaemonManager
        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 503
            assert dm._port_answers_health(11434) is False

    def test_false_on_connection_error(self, tmp_path):
        from llmport.daemon import DaemonManager
        dm = DaemonManager(config_dir=str(tmp_path))
        with patch("llmport.daemon.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.side_effect = Exception
            assert dm._port_answers_health(11434) is False


class TestRunDaemonApiKeyGuard:
    """run_daemon refuses to serve without an API key (defense-in-depth for
    direct `llmport --daemon` that bypasses the CLI start pre-check)."""

    def test_refuses_without_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from llmport.config.store import ConfigStore

        ConfigStore().init_first_run()  # config exists, but no api_key
        from llmport import daemon

        with pytest.raises(SystemExit) as exc:
            daemon.run_daemon()
        assert exc.value.code == 1


class TestWindowsPaths:
    """Windows daemon lifecycle, exercised on any OS by mocking ``os.name``.

    POSIX-only primitives (``os.kill(pid, 0)``, ``ps``, SIGKILL,
    ``start_new_session``) are replaced on Windows with: /health-pid identity,
    ``TerminateProcess`` via ``os.kill(pid, SIGTERM)``, /health exit-polling,
    and ``CREATE_NEW_PROCESS_GROUP``. These tests pin that branching without
    needing a real Windows run.
    """

    def _dm(self, tmp_path):
        from llmport.daemon import DaemonManager
        return DaemonManager(config_dir=str(tmp_path))

    def test_pid_is_our_daemon_uses_health_pid_on_windows(self, tmp_path, monkeypatch):
        """On Windows, identity = /health self-reported pid (no os.kill/ps)."""
        dm = self._dm(tmp_path)
        monkeypatch.setattr(os, "name", "nt")
        with patch.object(dm, "_health_reports_pid", return_value=True):
            assert dm._pid_is_our_daemon(9999) is True
        with patch.object(dm, "_health_reports_pid", return_value=False):
            assert dm._pid_is_our_daemon(9999) is False

    def test_health_reports_pid_matches(self, tmp_path):
        """_health_reports_pid is True only when /health returns our pid."""
        dm = self._dm(tmp_path)
        (tmp_path / "daemon.pid").write_text(
            json.dumps({"pid": 9999, "port": 23456}))
        with patch("llmport.daemon.httpx.Client") as MockClient:
            resp = MockClient.return_value.__enter__.return_value.get.return_value
            resp.status_code = 200
            resp.json.return_value = {"status": "ok", "pid": 9999}
            assert dm._health_reports_pid(9999) is True
            # a different pid serving on the port -> not ours
            resp.json.return_value = {"status": "ok", "pid": 1234}
            assert dm._health_reports_pid(9999) is False

    def test_health_reports_pid_false_on_non_200(self, tmp_path):
        dm = self._dm(tmp_path)
        with patch("llmport.daemon.httpx.Client") as MockClient:
            resp = MockClient.return_value.__enter__.return_value.get.return_value
            resp.status_code = 503
            resp.json.return_value = {"status": "ok", "pid": 9999}
            assert dm._health_reports_pid(9999) is False

    def test_health_reports_pid_false_when_port_down(self, tmp_path):
        dm = self._dm(tmp_path)
        with patch("llmport.daemon.httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.side_effect = Exception
            assert dm._health_reports_pid(9999) is False

    def test_stop_on_windows_uses_sigterm_no_sigkill(self, tmp_path, monkeypatch):
        """Windows stop: TerminateProcess via os.kill(pid, SIGTERM); no SIGKILL."""
        (tmp_path / "daemon.pid").write_text(
            json.dumps({"pid": 9999, "port": 23456}))
        dm = self._dm(tmp_path)
        monkeypatch.setattr(os, "name", "nt")
        with patch.object(dm, "_pid_is_our_daemon", return_value=True), \
             patch.object(os, "kill") as mock_kill, \
             patch.object(dm, "_wait_for_exit", return_value=True):
            dm.stop()
        sent = [c.args[1] for c in mock_kill.call_args_list]
        assert 15 in sent        # SIGTERM (signal.SIGTERM == 15 on Windows too)
        assert 9 not in sent     # no SIGKILL escalation on Windows
        assert not (tmp_path / "daemon.pid").exists()

    def test_stop_on_windows_skips_when_not_ours(self, tmp_path, monkeypatch):
        """A pid that fails identity (stale/recycled) is cleared, not signaled."""
        (tmp_path / "daemon.pid").write_text(
            json.dumps({"pid": 9999, "port": 23456}))
        dm = self._dm(tmp_path)
        monkeypatch.setattr(os, "name", "nt")
        with patch.object(dm, "_pid_is_our_daemon", return_value=False), \
             patch.object(os, "kill") as mock_kill:
            dm.stop()
        mock_kill.assert_not_called()
        assert not (tmp_path / "daemon.pid").exists()

    def test_start_on_windows_uses_creationflags(self, tmp_path, monkeypatch):
        """Windows start detaches via CREATE_NEW_PROCESS_GROUP, not start_new_session."""
        from llmport.daemon import DaemonManager
        dm = DaemonManager(config_dir=str(tmp_path))
        monkeypatch.setattr(os, "name", "nt")
        with patch("llmport.daemon.subprocess.Popen") as MockPopen, \
             patch("llmport.daemon.httpx.Client") as MockClient, \
             patch("llmport.daemon.time.sleep"), \
             patch.object(dm, "is_running", side_effect=[False, True]), \
             patch.object(dm, "_port_answers_health", return_value=False):
            MockClient.return_value.__enter__.return_value.get.return_value.status_code = 200
            dm.start()
        kwargs = MockPopen.call_args.kwargs
        assert kwargs.get("creationflags") == 0x00000200  # CREATE_NEW_PROCESS_GROUP
        assert "start_new_session" not in kwargs

    def test_wait_for_exit_on_windows_polls_health(self, tmp_path, monkeypatch):
        """On Windows _wait_for_exit returns True once /health stops answering."""
        dm = self._dm(tmp_path)
        monkeypatch.setattr(os, "name", "nt")
        with patch.object(dm, "_port_answers_health", side_effect=[True, False]), \
             patch("llmport.daemon.time.sleep"):
            assert dm._wait_for_exit(9999, 6.0) is True
