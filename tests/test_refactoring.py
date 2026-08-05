"""Tests for structural refactoring (Issues 1, 2, 3, 4, 6, 7).

Issue 1 — server.py 拆分: GatewayState → state.py, control API → control_api.py
Issue 2 — handler_base.py: 公共 forward/stream 抽取
Issue 3 — SDK 路径兼容: /v1/chat/completions + /v1/messages
Issue 4 — run_daemon 整合到 daemon.py
Issue 6 — routing_strategy docstring
Issue 7 — _parse_models 迁移到 models/parser.py
"""

import tempfile

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore


# ──────────────────────────────────────────────
# Issue 1: server.py split (state.py + control_api.py)
# ──────────────────────────────────────────────

class TestIssue1ServerSplit:

    def test_gateway_state_importable_from_state_module(self):
        """GatewayState must be importable from llmport.gateway.state."""
        from llmport.gateway.state import GatewayState
        assert GatewayState is not None

    def test_control_api_importable(self):
        """Control API handlers must be importable from llmport.gateway.control_api."""
        from llmport.gateway.control_api import (
            control_status,
            control_daemon_stop,
            control_daemon_restart,
        )
        # Verify each is a callable (async function)
        for handler in (
            control_status,
            control_daemon_stop,
            control_daemon_restart,
        ):
            assert callable(handler), f"{handler.__name__} is not callable"

    def test_server_no_longer_defines_gateway_state_class(self):
        """server.py should import GatewayState from state.py, not define it."""
        from llmport.gateway import server
        assert "GatewayState" in dir(server), (
            "GatewayState must be accessible from server (imported)"
        )
        from llmport.gateway.state import GatewayState as GS
        assert server.GatewayState is GS, (
            "server.GatewayState must be the same class as state.GatewayState"
        )

    def test_create_app_still_functions_after_split(self):
        """After the split, create_app() must still work correctly.

        The gateway now uses a single-app/single-port design: create_app()
        returns one Starlette app that serves both protocol and /api/* routes.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            from llmport.gateway.server import create_app
            from starlette.applications import Starlette
            app = create_app(store)
            # Single Starlette app, not a tuple/list
            assert isinstance(app, Starlette), (
                f"Expected a single Starlette app, got {type(app)}"
            )
            assert not isinstance(app, (tuple, list)), (
                "create_app() must return a single app, not a tuple/list"
            )
            # The single app serves both protocol and control routes
            paths = {r.path for r in app.routes}
            assert "/openai/v1/chat/completions" in paths
            assert "/api/status" in paths


# ──────────────────────────────────────────────
# Issue 2: handler_base.py shared forward/stream
# ──────────────────────────────────────────────

class TestIssue2HandlerBase:

    def test_handler_base_exists(self):
        """handler_base.py must exist with forward() and stream() functions."""
        from llmport.gateway.handler_base import forward, stream
        assert callable(forward)
        assert callable(stream)

    def test_openai_handler_imports_from_handler_base(self):
        """openai_handler must import forward/stream from handler_base (no
        duplicate httpx code)."""
        import inspect
        from llmport.gateway import openai_handler
        # Check the source references handler_base for core HTTP logic
        source = inspect.getsource(openai_handler)
        assert "from llmport.gateway.handler_base import" in source, (
            "openai_handler must import from handler_base, not duplicate httpx logic"
        )

    def test_anthropic_handler_imports_from_handler_base(self):
        """anthropic_handler must import forward/stream from handler_base."""
        import inspect
        from llmport.gateway import anthropic_handler
        source = inspect.getsource(anthropic_handler)
        assert "from llmport.gateway.handler_base import" in source, (
            "anthropic_handler must import from handler_base, not duplicate httpx logic"
        )


# ──────────────────────────────────────────────
# Issue 3: SDK path compatibility
# ──────────────────────────────────────────────

class TestIssue3SdkPaths:

    def test_v1_chat_completions_route(self):
        """The single app must expose /v1/chat/completions (SDK compat)."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            from llmport.gateway.server import create_app
            app = create_app(store)
            paths = {r.path for r in app.routes}
            assert "/v1/chat/completions" in paths, (
                f"Missing /v1/chat/completions route. Got routes: {paths}"
            )

    def test_v1_messages_route(self):
        """The single app must expose /v1/messages (SDK compat)."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            from llmport.gateway.server import create_app
            app = create_app(store)
            paths = {r.path for r in app.routes}
            assert "/v1/messages" in paths, (
                f"Missing /v1/messages route. Got routes: {paths}"
            )

    def test_old_prefixed_paths_still_exist(self):
        """Old /openai/v1/* and /anthropic/v1/* paths must still work."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            from llmport.gateway.server import create_app
            app = create_app(store)
            paths = {r.path for r in app.routes}
            assert "/openai/v1/chat/completions" in paths
            assert "/anthropic/v1/messages" in paths


# ──────────────────────────────────────────────
# Issue 4: run_daemon unified in daemon.py
# ──────────────────────────────────────────────

class TestIssue4DaemonUnification:

    def test_run_daemon_in_daemon_module(self):
        """run_daemon must be importable from llmport.daemon."""
        from llmport.daemon import run_daemon
        assert callable(run_daemon)

    def test_server_no_longer_defines_run_daemon(self):
        """server.py must not define run_daemon directly (import from daemon)."""
        import llmport.gateway.server as srv
        # If accessible, it should be a re-export, not defined in server
        if hasattr(srv, "run_daemon"):
            import llmport.daemon
            assert srv.run_daemon is llmport.daemon.run_daemon, (
                "server.run_daemon should be the daemon module's function"
            )


# ──────────────────────────────────────────────
# Issue 7: _parse_models → models/parser.py
# ──────────────────────────────────────────────

class TestIssue7ParseModelsMigration:

    def test_parse_models_in_models_parser(self):
        """parse_models must exist in llmport.models.parser."""
        from llmport.models.parser import parse_models
        assert callable(parse_models)

    def test_parse_models_parses_correctly(self):
        """parse_models must handle comma-separated and newline-separated input."""
        from llmport.models.parser import parse_models

        # Normal: multiple lines, each with name and aliases
        result = parse_models("gpt-5,gpt5,chatgpt\nclaude-opus,opus")
        assert len(result) == 2
        assert result[0]["name"] == "gpt-5"
        assert result[0]["aliases"] == ["gpt5", "chatgpt"]
        assert result[1]["name"] == "claude-opus"
        assert result[1]["aliases"] == ["opus"]

        # Empty string
        assert parse_models("") == []

        # Whitespace only
        assert parse_models("  \n  \n") == []

        # Single model, no aliases
        result = parse_models("gpt-5")
        assert len(result) == 1
        assert result[0]["name"] == "gpt-5"
        assert result[0]["aliases"] == []

    def test_providers_screen_imports_from_parser(self):
        """The providers screen must now import parse_models from models.parser,
        not define its own _parse_models."""
        import pathlib
        src = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src" / "llmport" / "ui" / "screens" / "providers.py"
        ).read_text()
        # Must NOT define _parse_models locally
        assert "def _parse_models" not in src, (
            "providers.py must not define _parse_models locally; "
            "it should import from llmport.models.parser"
        )

    def test_parse_models_import_from_parser_works_in_providers(self):
        """Verify the providers module imports parse_models from its new
        location rather than defining its own."""
        # Safe import that doesn't trigger circular dependency
        from llmport.models.parser import parse_models as pm
        # Just verify the models.parser module has the function
        assert callable(pm)
        # And the providers.py source imports it
        import pathlib
        src = (
            pathlib.Path(__file__).resolve().parent.parent
            / "src" / "llmport" / "ui" / "screens" / "providers.py"
        ).read_text()
        assert "from llmport.models.parser import parse_models" in src


# ──────────────────────────────────────────────
# Issue 6: routing_strategy docstring
# ──────────────────────────────────────────────

class TestIssue6RoutingStrategyDocstring:

    def test_routing_strategy_has_documentation(self):
        """The ``routing_strategy`` field must be documented, either via
        dataclass field metadata or in the class/module docstring."""
        from llmport.models.model import LogicalModel

        # Check 1: field-level metadata doc
        f_field = LogicalModel.__dataclass_fields__.get("routing_strategy")
        if f_field is None:
            raise AssertionError("routing_strategy field not found on LogicalModel")

        meta_doc = f_field.metadata.get("doc", "")
        if meta_doc.strip():
            return  # documented via metadata

        # Check 2: class docstring mentions the field
        class_doc = (LogicalModel.__doc__ or "")
        if "routing_strategy" in class_doc:
            return  # documented in class-level docstring

        raise AssertionError(
            "routing_strategy field must be documented via field metadata "
            "or class docstring"
        )
