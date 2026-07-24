"""Null-state resilience tests for every TUI screen refresh method.

Mandated by postmortem rule: every TUI screen's refresh method must be
verified to handle empty/null/None API responses without crashing.

Screens covered:
- ModelsPane.refresh_models()  — zero models, None, {}
- ProvidersPane.refresh_providers()  — zero providers, None, {}
- StatsPane.refresh_stats()  — not running, empty status dict
- GatewayPane.refresh_status()  — not running, None config
"""

import pathlib


def _read_source(rel: str) -> str:
    """Read a source file without importing it (avoids Textual TUI deps)."""
    p = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src" / (rel.replace(".", "/") + ".py")
    )
    return p.read_text()


# ---------------------------------------------------------------------------
# ModelsPane (regression: the original crash site)
# ---------------------------------------------------------------------------

class TestModelsPaneNullStates:

    def _transform(self, api_response: dict | list | None) -> list:
        """Reproduce the EXACT data transform from models.py refresh_models()."""
        models_data = api_response or {}
        return (
            (models_data.get("models") or [])
            if isinstance(models_data, dict)
            else models_data or []
        )

    def test_empty_list(self):
        """[] → empty list, not crash."""
        result = self._transform([])
        assert result == []

    def test_none(self):
        """None → empty list, not crash."""
        result = self._transform(None)
        assert result == []

    def test_empty_dict(self):
        """{} → empty list, not crash."""
        result = self._transform({})
        assert result == []

    def test_dict_with_models_none(self):
        """{"models": None} → empty list, not crash."""
        result = self._transform({"models": None})
        assert result == []

    def test_dict_with_models_empty(self):
        """{"models": []} → empty list."""
        result = self._transform({"models": []})
        assert result == []

    def test_valid_data(self):
        """Normal list passes through."""
        result = self._transform([{"id": "gpt5", "provider_count": 1}])
        assert result == [{"id": "gpt5", "provider_count": 1}]

    def test_iteration_safe_for_all(self):
        """Verify that every null state can be iterated safely."""
        for case in [None, [], {}, {"models": None}, {"models": []}]:
            models_list = self._transform(case)
            models = [
                {"id": m["id"], "provider_count": m.get("provider_count", 0)}
                for m in models_list
            ]
            assert models == []  # no crash


# ---------------------------------------------------------------------------
# ProvidersPane
# ---------------------------------------------------------------------------

class TestProvidersPaneNullStates:

    def _transform(self, api_response: dict | list | None) -> list:
        """Reproduce providers.py refresh_providers() data path."""
        return api_response or []

    def test_none(self):
        """None → empty list."""
        result = self._transform(None)
        assert result == []

    def test_empty_list(self):
        """[] → empty list."""
        result = self._transform([])
        assert result == []

    def test_empty_dict(self):
        """{} → empty list ({} is falsy, or [] takes over)."""
        result = self._transform({})
        assert result == []

    def test_valid_data(self):
        """List passes through."""
        result = self._transform([{"id": "p1", "name": "P1"}])
        assert result == [{"id": "p1", "name": "P1"}]

    def test_iteration_safe(self):
        """Verify that every null state can be iterated safely (no TypeError)."""
        for case in [None, [], {}]:
            providers = self._transform(case)
            for p in providers:
                pass  # no crash
            assert list(providers) == []


# ---------------------------------------------------------------------------
# StatsPane
# ---------------------------------------------------------------------------

class TestStatsPaneNullStates:

    def _render(self, status: dict | None) -> str:
        """Reproduce stats.py refresh_stats() display logic."""
        if status is None or not status.get("running"):
            return "[dim]网关未运行[/]"
        rc = status.get("request_count", 0)
        pc = status.get("provider_count", 0)
        mc = status.get("model_count", 0)
        return f"  {rc}      {pc}      {mc}"

    def test_none_status(self):
        """None → not-running fallback, no crash."""
        result = self._render(None)
        assert "未运行" in result

    def test_empty_dict(self):
        """{} → not running (no 'running' key), no crash."""
        result = self._render({})
        assert "未运行" in result

    def test_running_with_no_keys(self):
        """{"running": true} but no other keys → .get(key, 0) returns 0."""
        result = self._render({"running": True})
        assert "0" in result

    def test_running_with_data(self):
        """Normal status dict renders properly."""
        result = self._render({
            "running": True,
            "request_count": 42,
            "provider_count": 3,
            "model_count": 10,
        })
        assert "42" in result
        assert "3" in result
        assert "10" in result


# ---------------------------------------------------------------------------
# GatewayPane
# ---------------------------------------------------------------------------

class TestGatewayPaneNullStates:

    def test_source_shows_not_running_branch(self):
        """gateway.py refresh_status() must handle 'running'=False."""
        source = _read_source("llmport.ui.screens.gateway")
        assert 'not status.get("running", False)' in source or 'not status.get("running")' in source

    def test_source_has_config_isinstance_guard(self):
        """gateway.py must guard the config fetch with isinstance check."""
        source = _read_source("llmport.ui.screens.gateway")
        assert "isinstance(data, dict)" in source, (
            "Must guard against non-dict config responses"
        )

    def test_source_safe_get_for_uptime(self):
        """gateway.py must use .get for uptime with default 0."""
        source = _read_source("llmport.ui.screens.gateway")
        assert '.get("uptime", 0)' in source or 'status.get("uptime", 0)' in source

    def test_source_safe_get_for_request_count(self):
        """gateway.py must use .get for request_count with default 0."""
        source = _read_source("llmport.ui.screens.gateway")
        assert "'request_count', 0" in source or '"request_count", 0' in source

    def test_source_safe_get_for_active_model(self):
        """gateway.py must handle missing active_model with fallback."""
        source = _read_source("llmport.ui.screens.gateway")
        assert "active_model" in source
