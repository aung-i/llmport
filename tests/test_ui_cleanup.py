"""Tests for UI cleanup issues (2, 4, 7, 8).

Issue 2 — gateway.py dead code (warning branch removal)
Issue 4 — Provider key clearing on edit form
Issue 7 — TUI type: ignore -> cast replacement
Issue 8 — async_get_json migration to ui/__init__.py
"""

import pathlib


def _source(rel_module: str) -> str:
    """Read source file directly (avoids circular imports from UI modules)."""
    p = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / (rel_module.replace(".", "/") + ".py")
    )
    return p.read_text()


# ──────────────────────────────────────────────
# Issue 2: gateway.py dead code -- warning branch
# ──────────────────────────────────────────────

class TestGatewayDeadCode:

    def test_no_warning_check_in_save_restart(self):
        """GatewayConfigScreen.on_button_pressed must not check for a
        'warning' field in the response (the control API no longer returns
        one -- non-loopback hosts are rejected outright)."""
        source = _source("llmport.ui.screens.gateway")
        assert 'result.get("warning")' not in source, (
            "Dead code: control API no longer returns 'warning'; "
            "remove the warning branch from GatewayConfigScreen"
        )


# ──────────────────────────────────────────────
# Issue 4: Provider key clearing on edit form
# ──────────────────────────────────────────────

class TestProviderKeyClearing:

    def test_edit_mode_key_field_starts_empty(self):
        """When editing a provider, the API key input must start empty
        (the placeholder instructs the user to leave blank to keep the
        existing key)."""
        source = _source("llmport.ui.screens.providers")
        # Key field value must be '' (empty, not pre-filled) when editing
        assert 'value=""' in source, (
            "Edit-mode API key field should start empty"
        )

    def test_empty_key_sends_preserve_sentinel(self):
        """When the key field is empty in edit mode, the form must send
        '***' to signal the server to preserve the existing key."""
        source = _source("llmport.ui.screens.providers")
        assert '"***"' in source, (
            "Form must send '***' when key field is empty in edit mode"
        )


# ──────────────────────────────────────────────
# Issue 7: TUI type: ignore -> cast replacement
# ──────────────────────────────────────────────

class TestTypeIgnoreCleanup:

    def test_gateway_screen_no_type_ignore(self):
        """gateway.py must not contain # type: ignore."""
        source = _source("llmport.ui.screens.gateway")
        assert "# type: ignore" not in source

    def test_models_screen_no_type_ignore(self):
        """models.py must not contain # type: ignore."""
        source = _source("llmport.ui.screens.models")
        assert "# type: ignore" not in source

    def test_onboarding_screen_no_type_ignore(self):
        """onboarding.py must not contain # type: ignore."""
        source = _source("llmport.ui.screens.onboarding")
        assert "# type: ignore" not in source

    def test_providers_screen_no_type_ignore(self):
        """providers.py must not contain # type: ignore."""
        source = _source("llmport.ui.screens.providers")
        assert "# type: ignore" not in source

    def test_stats_screen_no_type_ignore(self):
        """stats.py must not contain # type: ignore."""
        source = _source("llmport.ui.screens.stats")
        assert "# type: ignore" not in source


# ──────────────────────────────────────────────
# Issue 8: async_get_json migration to ui/__init__.py
# ──────────────────────────────────────────────

class TestAsyncGetJsonMigration:

    def test_async_get_json_importable_from_ui(self):
        """async_get_json must be importable from llmport.ui (this import
        is safe -- no circular dependency)."""
        from llmport.ui import async_get_json
        assert callable(async_get_json)

    def test_onboarding_imports_from_ui(self):
        """onboarding.py must import async_get_json from llmport.ui, not
        define it locally."""
        source = _source("llmport.ui.screens.onboarding")
        assert "async def async_get_json" not in source, (
            "onboarding.py should not define async_get_json; "
            "it should import from llmport.ui"
        )
        assert "from llmport.ui import" in source, (
            "onboarding.py must import async_get_json from llmport.ui"
        )

    def test_gateway_imports_from_ui(self):
        """gateway.py must import async_get_json from llmport.ui."""
        source = _source("llmport.ui.screens.gateway")
        assert "from llmport.ui.screens.onboarding import" not in source, (
            "gateway.py should import async_get_json from llmport.ui, "
            "not from onboarding"
        )
        assert "from llmport.ui import" in source, (
            "gateway.py must import async_get_json from llmport.ui"
        )

    def test_models_imports_from_ui(self):
        """models.py must import async_get_json from llmport.ui."""
        source = _source("llmport.ui.screens.models")
        assert "from llmport.ui.screens.onboarding import" not in source
        assert "from llmport.ui import" in source, (
            "models.py must import async_get_json from llmport.ui"
        )

    def test_providers_imports_from_ui(self):
        """providers.py must import async_get_json from llmport.ui."""
        source = _source("llmport.ui.screens.providers")
        assert "from llmport.ui.screens.onboarding import" not in source
        assert "from llmport.ui import" in source, (
            "providers.py must import async_get_json from llmport.ui"
        )
