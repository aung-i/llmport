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

    @pytest.mark.parametrize("action", ["start", "stop", "restart", "status", "tui"])
    def test_action_choices_recognized(self, action):
        """All action choices ('start', 'stop', 'restart', 'status', 'tui') are accepted."""
        with patch.object(sys, "argv", ["llmport", action]):
            with patch("llmport.cli.DaemonManager") as MockDM, patch(
                "llmport.app.LlmPortApp"
            ):
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                from llmport.cli import main

                main()
                if action != "tui":
                    MockDM.assert_called_once()

    def test_bare_command_prints_help(self, capsys):
        """Bare `llmport` (no action) prints help."""
        with patch.object(sys, "argv", ["llmport"]):
            with patch("llmport.cli.DaemonManager"):
                from llmport.cli import main

                main()
                captured = capsys.readouterr()
                assert "usage:" in captured.out


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
        """start calls DaemonManager.start() and prints 'Gateway started'."""
        with patch.object(sys, "argv", ["llmport", "start"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                mock_dm.start.return_value = True
                mock_dm.get_control_port.return_value = 11434
                from llmport.cli import main

                main()
                mock_dm.start.assert_called_once()
                captured = capsys.readouterr()
                assert "Gateway started" in captured.out

    def test_start_when_running(self, capsys):
        """start prints 'Gateway already running' when daemon is already running."""
        with patch.object(sys, "argv", ["llmport", "start"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = True
                mock_dm.get_control_port.return_value = 11434
                from llmport.cli import main

                main()
                mock_dm.start.assert_not_called()
                captured = capsys.readouterr()
                assert "Gateway already running" in captured.out


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


class TestSetup:
    """The `llmport setup` wizard."""

    def test_setup_creates_config_and_secrets(self, tmp_path, monkeypatch, capsys):
        """setup writes providers/models to config.yaml and keys to secrets.enc."""
        from llmport.config.store import ConfigStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        answers = iter([
            "anthropic",      # provider id
            "",               # name (default -> anthropic)
            "anthropic",      # protocol
            "",               # base_url (default)
            "sk-setup-key",   # api key
            "",               # no more providers
            "claude-sonnet",  # model name
            "",               # provider (default)
            "claude-sonnet-4",  # upstream
            "",               # no more models
        ])
        monkeypatch.setattr("builtins.input", lambda _p: next(answers))

        with patch.object(sys, "argv", ["llmport", "setup"]):
            from llmport.cli import main
            main()

        store = ConfigStore(str(tmp_path / "llmport"))
        cfg = store.load_config()
        assert any(p["id"] == "anthropic" for p in cfg["providers"])
        assert any(m["name"] == "claude-sonnet" for m in cfg["models"])
        assert store.load_secrets() == {"anthropic": "sk-setup-key"}
        # API key must not leak into the readable config.
        assert "sk-setup-key" not in (tmp_path / "llmport" / "config.yaml").read_text()

    def test_setup_skip_leaves_template(self, tmp_path, monkeypatch, capsys):
        """If the user skips all prompts, the template config.yaml remains."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # Skip the first provider prompt.
        monkeypatch.setattr("builtins.input", lambda _p: "")
        with patch.object(sys, "argv", ["llmport", "setup"]):
            from llmport.cli import main
            main()
        text = (tmp_path / "llmport" / "config.yaml").read_text()
        # Template comments are present.
        assert "供应商" in text
        assert "providers: []" in text


class TestStartGate:
    """`llmport start` requires setup first."""

    def test_start_refuses_when_config_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(sys, "argv", ["llmport", "start"]):
            from llmport.cli import main
            main()
        out = capsys.readouterr().out
        assert "尚未配置供应商" in out
        assert "llmport setup" in out

    def test_start_refuses_with_empty_providers(self, tmp_path, monkeypatch, capsys):
        from llmport.config.store import ConfigStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        ConfigStore(str(tmp_path / "llmport")).init_first_run()  # empty config
        with patch.object(sys, "argv", ["llmport", "start"]):
            from llmport.cli import main
            main()
        out = capsys.readouterr().out
        assert "尚未配置供应商" in out

    def test_start_proceeds_when_providers_configured(self, tmp_path, monkeypatch, capsys):
        """With a provider configured, start proceeds to dm.start()."""
        from llmport.config.store import ConfigStore

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        store = ConfigStore(str(tmp_path / "llmport"))
        store.init_first_run()
        store.save_config({
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [{"id": "p", "name": "P", "protocol": "openai",
                           "base_url": "https://api.example.com"}],
            "models": [{"name": "m", "provider": "p", "upstream": "m"}],
        })
        with patch.object(sys, "argv", ["llmport", "start"]):
            with patch("llmport.cli.DaemonManager") as MockDM:
                mock_dm = MockDM.return_value
                mock_dm.is_running.return_value = False
                mock_dm.start.return_value = True
                mock_dm.get_control_port.return_value = 11434
                from llmport.cli import main
                main()
                mock_dm.start.assert_called_once()
        out = capsys.readouterr().out
        assert "Gateway started" in out


class TestProviderModelCommands:
    """`llmport provider` and `llmport model` subcommands."""

    def _run(self, argv, tmp_path, monkeypatch, mock_ssrf=True):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # provider add validates base_url against DNS; mock it so CRUD tests
        # don't depend on live DNS (SSRF has its own dedicated tests).
        if mock_ssrf:
            monkeypatch.setattr(
                "llmport.gateway.ip_utils.validate_public_url", lambda _url: True
            )
        with patch.object(sys, "argv", argv):
            from llmport.cli import main
            main()

    def test_provider_add_encrypts_key(self, tmp_path, monkeypatch):
        """provider add stores the key in secrets.enc, never in config.yaml."""
        from llmport.config.store import ConfigStore

        self._run(["llmport", "provider", "add", "--id", "anthropic",
                   "--protocol", "anthropic", "--api-key", "sk-xyz"],
                  tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        cfg = store.load_config()
        assert any(p["id"] == "anthropic" for p in cfg["providers"])
        assert store.load_secrets() == {"anthropic": "sk-xyz"}
        assert "sk-xyz" not in (tmp_path / "llmport" / "config.yaml").read_text()

    def test_provider_add_prompts_for_key(self, tmp_path, monkeypatch):
        """Without --api-key, the key is read via getpass (hidden)."""
        from llmport.config.store import ConfigStore

        monkeypatch.setattr("getpass.getpass", lambda _p: "sk-prompted")
        self._run(["llmport", "provider", "add", "--id", "openai",
                   "--protocol", "openai"], tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        assert store.load_secrets() == {"openai": "sk-prompted"}

    def test_provider_add_rejects_local_url(self, tmp_path, monkeypatch, capsys):
        self._run(["llmport", "provider", "add", "--id", "x",
                   "--base-url", "http://127.0.0.1:5"], tmp_path, monkeypatch,
                  mock_ssrf=False)
        assert "不允许" in capsys.readouterr().out

    def test_provider_list_empty(self, tmp_path, monkeypatch, capsys):
        self._run(["llmport", "provider", "list"], tmp_path, monkeypatch)
        assert "无供应商" in capsys.readouterr().out

    def test_provider_list_shows_added(self, tmp_path, monkeypatch, capsys):
        self._run(["llmport", "provider", "add", "--id", "anthropic",
                   "--protocol", "anthropic", "--api-key", "sk"],
                  tmp_path, monkeypatch)
        capsys.readouterr()
        self._run(["llmport", "provider", "list"], tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert "anthropic" in out

    def test_provider_remove_clears_key(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        self._run(["llmport", "provider", "add", "--id", "p",
                   "--protocol", "openai", "--api-key", "sk"], tmp_path, monkeypatch)
        self._run(["llmport", "provider", "remove", "p"], tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        assert store.load_config()["providers"] == []
        assert store.load_secrets() == {}

    def test_provider_update_preserves_key(self, tmp_path, monkeypatch, capsys):
        """Updating a provider without --api-key keeps the existing key (no prompt)."""
        from llmport.config.store import ConfigStore

        self._run(["llmport", "provider", "add", "--id", "p",
                   "--protocol", "openai", "--api-key", "sk-orig"],
                  tmp_path, monkeypatch)
        capsys.readouterr()
        # Update base_url without --api-key: must not prompt, must keep key.
        self._run(["llmport", "provider", "add", "--id", "p",
                   "--protocol", "openai", "--base-url", "https://api.example.com"],
                  tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert "保留原值" in out
        store = ConfigStore(str(tmp_path / "llmport"))
        assert store.load_config()["providers"][0]["base_url"] == "https://api.example.com"
        assert store.load_secrets() == {"p": "sk-orig"}

    def test_provider_update_with_new_key(self, tmp_path, monkeypatch, capsys):
        """Updating with --api-key replaces the key."""
        from llmport.config.store import ConfigStore

        self._run(["llmport", "provider", "add", "--id", "p",
                   "--protocol", "openai", "--api-key", "sk-old"], tmp_path, monkeypatch)
        capsys.readouterr()
        self._run(["llmport", "provider", "add", "--id", "p",
                   "--protocol", "openai", "--api-key", "sk-new"], tmp_path, monkeypatch)
        assert "已更新" in capsys.readouterr().out
        store = ConfigStore(str(tmp_path / "llmport"))
        assert store.load_secrets() == {"p": "sk-new"}

    def test_model_add_unknown_provider(self, tmp_path, monkeypatch, capsys):
        self._run(["llmport", "model", "add", "--name", "m",
                   "--provider", "nope"], tmp_path, monkeypatch)
        assert "未知供应商" in capsys.readouterr().out

    def test_model_add_list_remove(self, tmp_path, monkeypatch, capsys):
        from llmport.config.store import ConfigStore

        self._run(["llmport", "provider", "add", "--id", "p",
                   "--protocol", "openai", "--api-key", "sk"], tmp_path, monkeypatch)
        capsys.readouterr()
        self._run(["llmport", "model", "add", "--name", "m",
                   "--provider", "p", "--upstream", "m-real"], tmp_path, monkeypatch)
        self._run(["llmport", "model", "list"], tmp_path, monkeypatch)
        out = capsys.readouterr().out
        assert "m-real" in out

        store = ConfigStore(str(tmp_path / "llmport"))
        assert any(m["name"] == "m" for m in store.load_config()["models"])

        self._run(["llmport", "model", "remove", "m"], tmp_path, monkeypatch)
        assert not any(m["name"] == "m"
                       for m in store.load_config()["models"])
