"""Tests for the CLI entry point (src/llmport/cli.py).

The CLI is a Typer app; tests drive it through ``typer.testing.CliRunner``,
which captures stdout/stderr and exit codes (Typer exits after every command
in standalone mode, so direct ``main()`` calls would raise ``SystemExit``).
"""

import inspect

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from typer.testing import CliRunner

from llmport.cli import app

_runner = CliRunner()


def invoke(argv, tmp_path, monkeypatch):
    """Run the CLI (``argv`` includes the leading ``llmport``) under an
    isolated XDG_CONFIG_HOME. Returns the CliRunner result."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return _runner.invoke(app, argv[1:])


# ===========================================================================
# top-level options / parsing
# ===========================================================================


class TestTopLevel:
    def test_version(self, tmp_path, monkeypatch):
        """--version prints version info and exits with code 0."""
        result = invoke(["llmport", "--version"], tmp_path, monkeypatch)
        assert result.exit_code == 0
        assert "llmport" in result.stdout
        assert "0.1.0" in result.stdout

    def test_daemon_flag_calls_run_daemon(self, tmp_path, monkeypatch):
        """--daemon flag causes run_daemon() to be called."""
        with patch("llmport.cli.run_daemon") as mock_run:
            invoke(["llmport", "--daemon"], tmp_path, monkeypatch)
        mock_run.assert_called_once()

    @pytest.mark.parametrize("action", ["start", "stop", "restart", "status"])
    def test_action_choices_recognized(self, action, tmp_path, monkeypatch):
        """All lifecycle actions are accepted and construct a DaemonManager."""
        with patch("llmport.cli.DaemonManager") as MockDM:
            MockDM.return_value.is_running.return_value = False
            invoke(["llmport", action], tmp_path, monkeypatch)
        MockDM.assert_called_once()

    def test_bare_command_prints_help(self, tmp_path, monkeypatch):
        """Bare `llmport` (no command) prints help."""
        result = invoke(["llmport"], tmp_path, monkeypatch)
        assert "usage" in result.stdout.lower()
        assert "provider" in result.stdout  # subcommand groups listed


# ===========================================================================
# lifecycle commands
# ===========================================================================


class TestStatusCommand:
    def test_status_when_not_running(self, tmp_path, monkeypatch):
        with patch("llmport.cli.DaemonManager") as MockDM:
            MockDM.return_value.is_running.return_value = False
            result = invoke(["llmport", "status"], tmp_path, monkeypatch)
        assert "Gateway is not running." in result.stdout


class TestStartCommand:
    def test_start_when_not_running(self, tmp_path, monkeypatch):
        with patch("llmport.cli.DaemonManager") as MockDM:
            mock_dm = MockDM.return_value
            mock_dm.is_running.return_value = False
            mock_dm.start.return_value = True
            mock_dm.get_control_port.return_value = 11434
            result = invoke(["llmport", "start"], tmp_path, monkeypatch)
            mock_dm.start.assert_called_once()
        assert "Gateway started" in result.stdout

    def test_start_when_running(self, tmp_path, monkeypatch):
        with patch("llmport.cli.DaemonManager") as MockDM:
            mock_dm = MockDM.return_value
            mock_dm.is_running.return_value = True
            mock_dm.get_control_port.return_value = 11434
            result = invoke(["llmport", "start"], tmp_path, monkeypatch)
            mock_dm.start.assert_not_called()
        assert "Gateway already running" in result.stdout


class TestStopCommand:
    def test_stop_when_not_running(self, tmp_path, monkeypatch):
        with patch("llmport.cli.DaemonManager") as MockDM:
            mock_dm = MockDM.return_value
            mock_dm.is_running.return_value = False
            result = invoke(["llmport", "stop"], tmp_path, monkeypatch)
            mock_dm.stop.assert_not_called()
        assert "Gateway is not running." in result.stdout

    def test_stop_when_running(self, tmp_path, monkeypatch):
        with patch("llmport.cli.DaemonManager") as MockDM:
            mock_dm = MockDM.return_value
            mock_dm.is_running.return_value = True
            result = invoke(["llmport", "stop"], tmp_path, monkeypatch)
            mock_dm.stop.assert_called_once()
        assert "Gateway stopped." in result.stdout


class TestSetup:
    """The `llmport setup` command bootstraps files and points at next steps."""

    def test_setup_creates_providers_and_models(self, tmp_path, monkeypatch):
        """setup lays down providers.yaml + models.yaml templates, no prompts."""
        from llmport.config.store import ConfigStore

        result = invoke(["llmport", "setup"], tmp_path, monkeypatch)

        store = ConfigStore(str(tmp_path / "llmport"))
        # Template providers + models files created; legacy files absent.
        ptext = (tmp_path / "llmport" / "providers.yaml").read_text()
        assert "供应商" in ptext
        assert "providers: []" in ptext
        assert (tmp_path / "llmport" / "models.yaml").exists()
        assert store.load_providers_config()["providers"] == []
        # No legacy single-file layout or Fernet key.
        assert not (tmp_path / "llmport" / "config.yaml").exists()
        assert not (tmp_path / "llmport" / "secrets.yaml").exists()
        assert not (tmp_path / "llmport" / "key").exists()
        # Output points the user at the dedicated commands, not an inline wizard.
        assert "provider add" in result.stdout
        assert "model add" in result.stdout

    def test_setup_does_not_prompt(self, tmp_path, monkeypatch):
        """setup must not read stdin -- it bootstraps and exits."""
        calls = []
        monkeypatch.setattr("builtins.input", lambda _p: calls.append(_p) or "")
        invoke(["llmport", "setup"], tmp_path, monkeypatch)
        assert calls == []


class TestStartGate:
    """`llmport start` requires setup first."""

    def test_start_refuses_when_config_missing(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "start"], tmp_path, monkeypatch)
        assert "尚未配置供应商" in result.stdout
        assert "provider add" in result.stdout

    def test_start_refuses_with_empty_providers(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        ConfigStore(str(tmp_path / "llmport")).init_first_run()  # empty config
        result = invoke(["llmport", "start"], tmp_path, monkeypatch)
        assert "尚未配置供应商" in result.stdout

    def test_start_proceeds_when_providers_configured(self, tmp_path, monkeypatch):
        """With a provider configured, start proceeds to dm.start()."""
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path / "llmport"))
        store.init_first_run()
        store.save_providers_config({
            "version": 1,
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [{"name": "p", "protocol": "openai",
                           "base_url": "https://api.example.com", "api_key": "sk"}],
        })
        store.save_models_config({"models": {"m": "p"}})
        with patch("llmport.cli.DaemonManager") as MockDM:
            mock_dm = MockDM.return_value
            mock_dm.is_running.return_value = False
            mock_dm.start.return_value = True
            mock_dm.get_control_port.return_value = 11434
            result = invoke(["llmport", "start"], tmp_path, monkeypatch)
            mock_dm.start.assert_called_once()
        assert "Gateway started" in result.stdout

    def test_validate_config_warns_on_malformed_entries(self):
        """_validate_providers_config / _validate_models_config surface missing
        name/base_url and dropped models."""
        from llmport.cli import _validate_providers_config, _validate_models_config
        pdata = {
            "providers": [
                {"name": "p", "base_url": "https://api.openai.com/v1"},
                {"id": "no-name"},            # missing name
                {"name": "p3"},               # missing base_url
            ],
        }
        mdata = {"models": {
            "good": {"p": "u"},              # valid binding
            "bad": {},                        # no provider -> ignored
        }}
        warnings = _validate_providers_config(pdata) + _validate_models_config(mdata)
        assert any("缺少 name" in w for w in warnings)
        assert any("p3" in w and "base_url" in w for w in warnings)
        assert any("bad" in w for w in warnings)
        assert not any("good" in w for w in warnings)


# ===========================================================================
# provider / model subcommands
# ===========================================================================


class TestProviderModelCommands:
    """`llmport provider` and `llmport model` subcommands."""

    def test_provider_add_stores_key_in_providers_yaml(self, tmp_path, monkeypatch):
        """provider add stores the key inline in providers.yaml (self-contained)."""
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "anthropic",
                "--protocol", "anthropic", "--api-key", "sk-xyz"],
               tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        pdata = store.load_providers_config()
        p = next(p for p in pdata["providers"] if p["name"] == "anthropic")
        assert p["api_key"] == "sk-xyz"
        # No separate secrets file in the new layout.
        assert not (tmp_path / "llmport" / "secrets.yaml").exists()

    def test_provider_add_prompts_for_key(self, tmp_path, monkeypatch):
        """Without --api-key, the key is read via getpass (hidden)."""
        from llmport.config.store import ConfigStore

        monkeypatch.setattr("getpass.getpass", lambda _p: "sk-prompted")
        invoke(["llmport", "provider", "add", "--name", "openai",
                "--protocol", "openai"], tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        p = store.load_providers_config()["providers"][0]
        assert p["api_key"] == "sk-prompted"

    def test_provider_list_empty(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "provider", "list"], tmp_path, monkeypatch)
        assert "无供应商" in result.stdout

    def test_provider_list_shows_added(self, tmp_path, monkeypatch):
        invoke(["llmport", "provider", "add", "--name", "anthropic",
                "--protocol", "anthropic", "--api-key", "sk"],
               tmp_path, monkeypatch)
        result = invoke(["llmport", "provider", "list"], tmp_path, monkeypatch)
        assert "anthropic" in result.stdout

    def test_provider_remove_clears_key(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "p",
                "--protocol", "openai", "--api-key", "sk"], tmp_path, monkeypatch)
        invoke(["llmport", "provider", "remove", "p"], tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        # Provider (and its key, which lived in the entry) is gone.
        assert store.load_providers_config()["providers"] == []

    def test_provider_update_preserves_key(self, tmp_path, monkeypatch):
        """Updating a provider without --api-key keeps the existing key (no prompt)."""
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "p",
                "--protocol", "openai", "--api-key", "sk-orig"], tmp_path, monkeypatch)
        # Update base_url without --api-key: must not prompt, must keep key.
        result = invoke(["llmport", "provider", "add", "--name", "p",
                         "--protocol", "openai", "--base-url", "https://api.example.com"],
                        tmp_path, monkeypatch)
        assert "保留原值" in result.stdout
        store = ConfigStore(str(tmp_path / "llmport"))
        p = store.load_providers_config()["providers"][0]
        assert p["base_url"] == "https://api.example.com"
        assert p["api_key"] == "sk-orig"

    def test_provider_update_with_new_key(self, tmp_path, monkeypatch):
        """Updating with --api-key replaces the key."""
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "p",
                "--protocol", "openai", "--api-key", "sk-old"], tmp_path, monkeypatch)
        result = invoke(["llmport", "provider", "add", "--name", "p",
                         "--protocol", "openai", "--api-key", "sk-new"],
                        tmp_path, monkeypatch)
        assert "已更新" in result.stdout
        store = ConfigStore(str(tmp_path / "llmport"))
        assert store.load_providers_config()["providers"][0]["api_key"] == "sk-new"

    def test_model_add_unknown_provider(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "model", "add", "--name", "m",
                         "--provider", "nope"], tmp_path, monkeypatch)
        assert "未知供应商" in result.stdout

    def test_model_add_list_remove(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "p",
                "--protocol", "openai", "--api-key", "sk"], tmp_path, monkeypatch)
        invoke(["llmport", "model", "add", "--name", "m",
                "--provider", "p", "--upstream", "m-real"], tmp_path, monkeypatch)
        result = invoke(["llmport", "model", "list"], tmp_path, monkeypatch)
        assert "m-real" in result.stdout

        store = ConfigStore(str(tmp_path / "llmport"))
        assert "m" in store.load_models_config()["models"]

        invoke(["llmport", "model", "remove", "m"], tmp_path, monkeypatch)
        assert "m" not in store.load_models_config()["models"]

    def test_openai_default_base_url_has_no_v1(self, tmp_path, monkeypatch):
        """OpenAI default base_url is the host root; /v1 is added by path constants."""
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "o",
                "--protocol", "openai", "--api-key", "sk"], tmp_path, monkeypatch)
        base = ConfigStore(str(tmp_path / "llmport")).load_providers_config()["providers"][0]["base_url"]
        assert base == "https://api.openai.com"

    def test_anthropic_default_base_url(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        invoke(["llmport", "provider", "add", "--name", "a",
                "--protocol", "anthropic", "--api-key", "sk"], tmp_path, monkeypatch)
        base = ConfigStore(str(tmp_path / "llmport")).load_providers_config()["providers"][0]["base_url"]
        assert base == "https://api.anthropic.com"


# ===========================================================================
# config subcommands
# ===========================================================================


class TestConfigCommands:
    """`llmport config` subcommand group."""

    def test_init_writes_template(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        result = invoke(["llmport", "config", "init"], tmp_path, monkeypatch)
        store = ConfigStore(str(tmp_path / "llmport"))
        ptext = store.providers_path.read_text(encoding="utf-8")
        assert "providers" in ptext  # commented template
        assert "api_key" in ptext
        # The OpenAI example must use the host root (no double /v1).
        assert "https://api.openai.com/v1" not in ptext
        assert store.models_path.exists()
        assert "已生成配置模板" in result.stdout

    def test_init_does_not_clobber(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        store = ConfigStore(str(tmp_path / "llmport"))
        store.init_first_run(config_template=True)
        before = store.providers_path.read_text(encoding="utf-8")
        result = invoke(["llmport", "config", "init"], tmp_path, monkeypatch)
        assert store.providers_path.read_text(encoding="utf-8") == before
        assert "已存在" in result.stdout

    def test_path_prints_config_paths(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "config", "path"], tmp_path, monkeypatch)
        assert "providers.yaml" in result.stdout
        assert "models.yaml" in result.stdout

    def test_show_prints_content_and_key_status(self, tmp_path, monkeypatch):
        invoke(["llmport", "provider", "add", "--name", "p",
                "--protocol", "openai", "--api-key", "sk"], tmp_path, monkeypatch)
        result = invoke(["llmport", "config", "show"], tmp_path, monkeypatch)
        assert "p" in result.stdout
        assert "已设置" in result.stdout  # key-status annotation

    def test_show_without_file(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "config", "show"], tmp_path, monkeypatch)
        assert "尚无配置文件" in result.stdout

    def test_edit_opens_editor(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        ConfigStore(str(tmp_path / "llmport")).init_first_run(config_template=True)
        monkeypatch.setenv("EDITOR", "myed")
        with patch("llmport.cli.subprocess.call") as mock_call:
            invoke(["llmport", "config", "edit"], tmp_path, monkeypatch)
        mock_call.assert_called_once()
        cmd = mock_call.call_args[0][0]
        assert cmd[0] == "myed"
        assert cmd[1].endswith("llmport/providers.yaml")

    def test_edit_without_file(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "config", "edit"], tmp_path, monkeypatch)
        assert "尚无配置文件" in result.stdout


# ===========================================================================
# provider test
# ===========================================================================


class TestProviderTestCommand:
    """`llmport provider test <name>` -- daemon-independent connectivity test."""

    def _add(self, tmp_path, monkeypatch, pid="p", protocol="openai", key="sk"):
        invoke(["llmport", "provider", "add", "--name", pid,
                "--protocol", protocol, "--api-key", key], tmp_path, monkeypatch)

    def test_openai_success_lists_models(self, tmp_path, monkeypatch):
        self._add(tmp_path, monkeypatch)
        with patch("llmport.gateway.openai_handler.list_models",
                   new_callable=AsyncMock) as mock_lm:
            mock_lm.return_value = ([{"id": "gpt-5"}, {"id": "gpt-4"}], None)
            result = invoke(["llmport", "provider", "test", "p"], tmp_path, monkeypatch)
        assert "连通" in result.stdout
        assert "gpt-5" in result.stdout and "gpt-4" in result.stdout

    def test_anthropic_success(self, tmp_path, monkeypatch):
        self._add(tmp_path, monkeypatch, pid="a", protocol="anthropic")
        with patch("llmport.gateway.anthropic_handler.test_connection",
                   new_callable=AsyncMock) as mock_tc:
            mock_tc.return_value = (True, 123.0, None)
            result = invoke(["llmport", "provider", "test", "a"], tmp_path, monkeypatch)
        assert "连通" in result.stdout

    def test_not_found(self, tmp_path, monkeypatch):
        self._add(tmp_path, monkeypatch)
        result = invoke(["llmport", "provider", "test", "nope"], tmp_path, monkeypatch)
        assert "未找到" in result.stdout

    def test_no_key(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        self._add(tmp_path, monkeypatch)
        # Clear the key from the provider entry (key lives in providers.yaml now).
        store = ConfigStore(str(tmp_path / "llmport"))
        pdata = store.load_providers_config()
        pdata["providers"][0]["api_key"] = ""
        store.save_providers_config(pdata)
        result = invoke(["llmport", "provider", "test", "p"], tmp_path, monkeypatch)
        assert "未设置 API key" in result.stdout

    def test_no_config(self, tmp_path, monkeypatch):
        result = invoke(["llmport", "provider", "test", "p"], tmp_path, monkeypatch)
        assert "无配置文件" in result.stdout

    def test_openai_failure_exits_nonzero(self, tmp_path, monkeypatch):
        self._add(tmp_path, monkeypatch)
        with patch("llmport.gateway.openai_handler.list_models",
                   new_callable=AsyncMock) as mock_lm:
            mock_lm.return_value = (None, "401 Unauthorized")
            result = invoke(["llmport", "provider", "test", "p"], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "失败" in result.stdout

    def test_empty_error_falls_back_to_generic_message(self, tmp_path, monkeypatch):
        """An empty error string (e.g. a connection exception with no message)
        still yields a useful failure line instead of a bare '✗ 失败: '."""
        self._add(tmp_path, monkeypatch)
        with patch("llmport.gateway.openai_handler.list_models",
                   new_callable=AsyncMock) as mock_lm:
            mock_lm.return_value = (None, "")
            result = invoke(["llmport", "provider", "test", "p"], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "失败" in result.stdout
        assert "连接失败" in result.stdout  # generic fallback, not a bare colon


# ===========================================================================
# base_url SSRF validation + auto-reload
# ===========================================================================


class TestConfigValidationAndReload:
    """base_url blocklist in the CLI write path + restart-on-config-change."""

    def test_provider_add_rejects_metadata_base_url(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        result = invoke(["llmport", "provider", "add", "--name", "bad",
                         "--protocol", "openai",
                         "--base-url", "http://169.254.169.254",
                         "--api-key", "sk"], tmp_path, monkeypatch)
        assert "拒绝保存" in result.stdout
        pdata = ConfigStore(str(tmp_path / "llmport")).load_providers_config()
        assert not any(p.get("name") == "bad" for p in pdata.get("providers", []))

    def test_provider_add_rejects_self_loop_base_url(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        result = invoke(["llmport", "provider", "add", "--name", "loop",
                         "--protocol", "openai",
                         "--base-url", "http://127.0.0.1:11434",
                         "--api-key", "sk"], tmp_path, monkeypatch)
        assert "拒绝保存" in result.stdout
        pdata = ConfigStore(str(tmp_path / "llmport")).load_providers_config()
        assert not any(p.get("name") == "loop" for p in pdata.get("providers", []))

    def test_provider_add_allows_local_base_url(self, tmp_path, monkeypatch):
        from llmport.config.store import ConfigStore

        result = invoke(["llmport", "provider", "add", "--name", "ollama",
                         "--protocol", "openai",
                         "--base-url", "http://127.0.0.1:11435",
                         "--api-key", "sk"], tmp_path, monkeypatch)
        assert "已添加" in result.stdout
        pdata = ConfigStore(str(tmp_path / "llmport")).load_providers_config()
        assert any(p.get("name") == "ollama" for p in pdata["providers"])

    def test_validate_providers_config_warns_on_bad_base_url(self):
        from llmport.cli import _validate_providers_config
        pdata = {
            "gateway": {"host": "127.0.0.1", "port": 11434},
            "providers": [{"name": "bad", "base_url": "http://169.254.169.254"}],
        }
        warnings = _validate_providers_config(pdata)
        assert any("bad" in w and "拒绝" in w for w in warnings)

    def test_apply_if_running_restarts(self, capsys):
        from llmport.cli import _apply_if_running
        dm = MagicMock()
        dm.is_running.return_value = True
        dm.restart.return_value = True
        _apply_if_running(dm)
        dm.restart.assert_called_once()
        assert "已重启" in capsys.readouterr().out

    def test_apply_if_running_noop_when_not_running(self):
        from llmport.cli import _apply_if_running
        dm = MagicMock()
        dm.is_running.return_value = False
        _apply_if_running(dm)
        dm.restart.assert_not_called()


# ===========================================================================
# source-level sanity (start/restart dispatch still wired)
# ===========================================================================


class TestStartRestartWiring:
    """The start/restart commands still dispatch to _cmd_start/_cmd_restart,
    which call dm.start()/dm.restart(). Verified via help output + source."""

    def _help(self):
        return _runner.invoke(app, ["--help"]).stdout

    def test_start_subcommand_exists(self):
        assert "start" in self._help()

    def test_restart_subcommand_exists(self):
        assert "restart" in self._help()

    def test_start_calls_daemon_start(self):
        from llmport.cli import _cmd_start
        assert "dm.start" in inspect.getsource(_cmd_start)

    def test_restart_calls_daemon_restart(self):
        from llmport.cli import _cmd_restart
        assert "dm.restart" in inspect.getsource(_cmd_restart)

    def test_help_lists_start_restart(self):
        h = self._help()
        assert "start" in h and "restart" in h

    def test_stop_and_status_still_listed(self):
        h = self._help()
        assert "stop" in h and "status" in h
