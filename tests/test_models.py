"""Tests for data models."""

from llmport.models.provider import ProviderConfig
from llmport.models.model import (
    ModelBinding,
    LogicalModel,
    parse_models_config,
)


def _bindings(models, name):
    """Return the (provider, upstream) tuples for the named model."""
    m = next(x for x in models if x.name == name)
    return [(b.provider, b.upstream) for b in m.bindings]


def test_parse_str_value_single_provider_no_alias():
    # claude-sonnet: anthropic  -> upstream defaults to public name
    models = parse_models_config({"claude-sonnet": "anthropic"})
    assert len(models) == 1
    m = models[0]
    assert m.name == "claude-sonnet"
    assert _bindings(models, "claude-sonnet") == [("anthropic", "claude-sonnet")]


def test_parse_list_of_str_multiple_providers_no_alias():
    models = parse_models_config({"gpt-4o": ["openai", "azure"]})
    assert _bindings(models, "gpt-4o") == [("openai", "gpt-4o"), ("azure", "gpt-4o")]


def test_parse_list_of_dict_single_upstream():
    models = parse_models_config({"sonnet": [{"anthropic": "claude-sonnet-4"}]})
    assert _bindings(models, "sonnet") == [("anthropic", "claude-sonnet-4")]


def test_parse_list_of_dict_upstream_list_fallback():
    models = parse_models_config({
        "gpt4": [
            {"openai": "gpt-4"},
            {"azure": ["gpt4o-deploy", "gpt4o-turbo"]},
        ],
    })
    assert _bindings(models, "gpt4") == [
        ("openai", "gpt-4"),
        ("azure", "gpt4o-deploy"),
        ("azure", "gpt4o-turbo"),
    ]


def test_parse_bare_dict_value_single_provider_alias():
    # sonnet: {anthropic: claude-sonnet-4}  (no enclosing list)
    models = parse_models_config({"sonnet": {"anthropic": "claude-sonnet-4"}})
    assert _bindings(models, "sonnet") == [("anthropic", "claude-sonnet-4")]


def test_parse_upstream_defaults_to_public_name_when_empty():
    models = parse_models_config({"gpt-4o": [{"openai": ""}, "azure"]})
    # empty upstream -> public name
    assert _bindings(models, "gpt-4o") == [("openai", "gpt-4o"), ("azure", "gpt-4o")]


def test_parse_preserves_list_order_for_fallback():
    models = parse_models_config({"m": [{"a": "a1"}, {"b": "b1"}, {"a": "a2"}]})
    assert [b.provider for b in models[0].bindings] == ["a", "b", "a"]


def test_provider_count_counts_distinct_providers():
    models = parse_models_config({"multi": [{"a": ["m1", "m1-b"]}, {"b": "m2"}]})
    m = models[0]
    assert len(m.bindings) == 3
    assert m.provider_count == 2


def test_parse_empty_or_none_returns_empty_list():
    assert parse_models_config(None) == []
    assert parse_models_config({}) == []


def test_parse_non_dict_returns_empty_list():
    assert parse_models_config([]) == []
    assert parse_models_config("nope") == []


def test_parse_skips_empty_provider():
    models = parse_models_config({"ok": ["openai", ""]})  # empty provider skipped
    assert _bindings(models, "ok") == [("openai", "ok")]


def test_parse_skips_model_with_no_bindings():
    # value is a non-str/dict/list -> no bindings -> dropped
    assert parse_models_config({"bad": 123}) == []


def test_provider_config_roundtrip_with_key():
    p = ProviderConfig(
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-secret",
    )
    d = p.to_dict()
    assert d["api_key"] == "sk-secret"
    assert "id" not in d
    assert "models" not in d
    p2 = ProviderConfig.from_dict(d)
    assert p2.name == p.name
    assert p2.protocol == p.protocol
    assert p2.base_url == p.base_url
    assert p2.api_key == p.api_key


def test_provider_config_roundtrip_without_key_masks_secret():
    p = ProviderConfig(
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-secret",
    )
    d = p.to_dict(include_key=False)
    assert d["api_key"] == "***"


def test_provider_config_from_dict_uses_legacy_id_as_name():
    d = {
        "id": "openai",  # legacy id key -> used as name when name absent
        "protocol": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-secret",
        "models": [{"name": "gpt-5"}],  # legacy, ignored
        "health": {"status": "up", "latency_ms": 12.5},
    }
    p = ProviderConfig.from_dict(d)
    assert p.name == "openai"
    assert not hasattr(p, "models")
    assert p.health.status == "up"
    assert p.health.latency_ms == 12.5
