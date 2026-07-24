"""Tests for ModelsPane data-fetching resilience (postmortem).

The production crash at models.py:114 was triggered when `/api/models`
returned ``[]`` (empty list) or ``None`` — the ``or {}`` fallback turned
the falsy value into ``{}``, then ``.get("models")`` produced ``None``,
which was iterated as ``for m in None``.

These tests verify the data-transformation logic in isolation, covering
every edge case for the return value of ``async_get_json()``.
"""

# The two critical expressions that convert the API response into models_list:
#
#   models_data = await async_get_json(...) or {}
#   models_list = models_data.get("models") if isinstance(models_data, dict) else models_data or []
#   for m in models_list:  # <-- crash here when models_list is None

import pytest

# ---- Data transformation helper (mirrors models.py logic) ----


def _to_models_list(api_response: dict | list | None) -> list:
    """Simulate the fixed data transformation in models.py refresh_models()
    and on_button_pressed (btn-detail path).

    Fix applied: (models_data.get("models") or []) instead of bare .get().
    """
    models_data = api_response or {}
    return (
        (models_data.get("models") or [])
        if isinstance(models_data, dict)
        else models_data or []
    )


class TestModelsListTransformation:
    """Unit tests for the API-response-to-models_list conversion."""

    def test_valid_list(self):
        """Happy path: API returns a list → models_list is that list."""
        result = _to_models_list([
            {"id": "gpt5", "provider_count": 2},
        ])
        assert result == [{"id": "gpt5", "provider_count": 2}]

    def test_valid_dict_with_models_key(self):
        """API returns {"models": [...]} (dict format) → models_list extracts .models."""
        result = _to_models_list({
            "models": [{"id": "gpt5", "provider_count": 2}],
        })
        assert result == [{"id": "gpt5", "provider_count": 2}]

    def test_none(self):
        """API returns None (network error) → models_list is [] (safe to iterate)."""
        result = _to_models_list(None)
        # Must not be None; must not crash in for-loop
        assert result == []

    def test_empty_list(self):
        """API returns [] → models_list is [] (not None)."""
        result = _to_models_list([])
        assert result == []

    def test_empty_dict(self):
        """API returns {} → models_list is [] (not None).
        This was the crash path: {} → ".get('models')" → None."""
        result = _to_models_list({})
        assert result == [], f"Expected [], got {result!r}"

    def test_list_then_iteration_does_not_crash(self):
        """Verifies the full pattern: convert → iterate does not raise."""
        for case in [None, [], {}, [{"id": "x"}], {"models": [{"id": "x"}]}]:
            models_list = _to_models_list(case)
            result = [
                {"id": m["id"], "provider_count": m.get("provider_count", 0)}
                for m in models_list
            ]
            # No crash = pass

    def test_dict_without_models_key(self):
        """API returns {"foo": "bar"} → .get("models") is None → models_list is []."""
        result = _to_models_list({"foo": "bar"})
        assert result == []

    def test_dict_with_models_none(self):
        """API returns {"models": None} → .get("models") is None → models_list is []."""
        result = _to_models_list({"models": None})
        assert result == []


class TestButtonDetailTransformation:
    """The btn-detail path (lines 163-176) uses the same pattern but then
    iterates to find a matching model.  Test that path too."""

    def _find_bindings(self, api_response: dict | list | None, target_id: str) -> list:
        """Simulate the btn-detail data path."""
        models_list = _to_models_list(api_response)
        for m in models_list:
            if m["id"] == target_id:
                return m.get("bindings", [])
        return []

    def test_find_with_valid_list(self):
        bindings = self._find_bindings(
            [{"id": "gpt5", "bindings": [{"provider_id": "p1"}]}],
            "gpt5",
        )
        assert bindings == [{"provider_id": "p1"}]

    def test_find_with_none_returns_empty(self):
        """None → no crash, no match → empty list."""
        bindings = self._find_bindings(None, "gpt5")
        assert bindings == []

    def test_find_with_empty_list_returns_empty(self):
        """[] → no crash, no match → empty list."""
        bindings = self._find_bindings([], "gpt5")
        assert bindings == []

    def test_find_with_empty_dict_returns_empty(self):
        """{} → no crash, no match → empty list."""
        bindings = self._find_bindings({}, "gpt5")
        assert bindings == []
