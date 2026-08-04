"""Tests for data models."""

from llmport.models.provider import ProviderConfig
from llmport.models.model import (
    ModelBinding,
    LogicalModel,
    parse_models_config,
)


def test_parse_shorthand_entry_creates_single_binding():
    models = parse_models_config([
        {"name": "claude-sonnet", "provider": "anthropic", "upstream": "claude-sonnet-4"},
    ])
    assert len(models) == 1
    m = models[0]
    assert m.name == "claude-sonnet"
    assert m.routing_strategy == "priority_fallback"
    assert len(m.bindings) == 1
    b = m.bindings[0]
    assert b.provider == "anthropic"
    assert b.upstream == "claude-sonnet-4"
    assert b.priority == 1


def test_parse_shorthand_entry_with_priority():
    models = parse_models_config([
        {"name": "x", "provider": "p", "upstream": "u", "priority": 3},
    ])
    assert models[0].bindings[0].priority == 3


def test_parse_full_entry_multiple_bindings_fallback_order():
    models = parse_models_config([
        {
            "name": "gpt-4o",
            "bindings": [
                {"provider": "openai", "upstream": "gpt-4o", "priority": 2},
                {"provider": "azure-openai", "upstream": "gpt4o-deploy", "priority": 1},
            ],
        },
    ])
    assert len(models) == 1
    m = models[0]
    assert m.name == "gpt-4o"
    assert len(m.bindings) == 2
    # bindings_sorted returns bindings ordered by priority ascending (fallback order)
    assert m.bindings_sorted[0].provider == "azure-openai"
    assert m.bindings_sorted[0].upstream == "gpt4o-deploy"
    assert m.bindings_sorted[1].provider == "openai"
    assert m.bindings_sorted[1].upstream == "gpt-4o"


def test_parse_full_entry_default_priority_is_one():
    models = parse_models_config([
        {
            "name": "x",
            "bindings": [
                {"provider": "p", "upstream": "u"},
            ],
        },
    ])
    assert models[0].bindings[0].priority == 1


def test_provider_count_counts_distinct_providers():
    models = parse_models_config([
        {
            "name": "multi",
            "bindings": [
                {"provider": "a", "upstream": "m1", "priority": 1},
                {"provider": "a", "upstream": "m1-b", "priority": 2},
                {"provider": "b", "upstream": "m2", "priority": 3},
            ],
        },
    ])
    assert len(models) == 1
    m = models[0]
    assert len(m.bindings) == 3
    assert m.provider_count == 2


def test_parse_empty_or_none_returns_empty_list():
    assert parse_models_config(None) == []
    assert parse_models_config([]) == []


def test_parse_skips_entries_without_name():
    models = parse_models_config([
        {"provider": "p", "upstream": "u"},  # no name -> skipped
        {"name": "ok", "provider": "p", "upstream": "u"},
    ])
    assert len(models) == 1
    assert models[0].name == "ok"


def test_parse_skips_malformed_bindings_instead_of_crashing():
    """A binding missing provider/upstream (hand-edit typo) is skipped, and a
    model left with no usable bindings is dropped -- no KeyError."""
    # all bindings malformed -> model dropped entirely
    assert parse_models_config([
        {"name": "bad", "bindings": [{"provider": "p"}, {"upstream": "u"}]},
    ]) == []
    # one good + one bad -> only the good binding survives
    models = parse_models_config([{
        "name": "mixed",
        "bindings": [
            {"provider": "p1", "upstream": "u1"},
            {"provider": "p2"},  # missing upstream -> skipped
        ],
    }])
    assert len(models) == 1
    assert [(b.provider, b.upstream) for b in models[0].bindings] == [("p1", "u1")]


def test_parse_accepts_legacy_id_field_as_name():
    models = parse_models_config([
        {"id": "legacy", "provider": "p", "upstream": "u"},
    ])
    assert len(models) == 1
    assert models[0].name == "legacy"


def test_bindings_sorted_property_sorts_by_priority():
    m = LogicalModel(
        name="test",
        bindings=[
            ModelBinding(provider="b", upstream="m2", priority=2),
            ModelBinding(provider="a", upstream="m1", priority=1),
            ModelBinding(provider="c", upstream="m3", priority=3),
        ],
    )
    assert [b.provider for b in m.bindings_sorted] == ["a", "b", "c"]


def test_provider_config_roundtrip_with_key():
    p = ProviderConfig(
        id="openai",
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-secret",
    )
    d = p.to_dict()
    assert d["api_key"] == "sk-secret"
    assert "models" not in d
    p2 = ProviderConfig.from_dict(d)
    assert p2.id == p.id
    assert p2.name == p.name
    assert p2.protocol == p.protocol
    assert p2.base_url == p.base_url
    assert p2.api_key == p.api_key


def test_provider_config_roundtrip_without_key_masks_secret():
    p = ProviderConfig(
        id="openai",
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-secret",
    )
    d = p.to_dict(include_key=False)
    assert d["api_key"] == "***"


def test_provider_config_from_dict_ignores_legacy_models_field():
    d = {
        "id": "openai",
        "name": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com",
        "api_key": "sk-secret",
        "models": [{"name": "gpt-5", "aliases": ["gpt5"]}],  # legacy, ignored
        "health": {"status": "up", "latency_ms": 12.5},
    }
    p = ProviderConfig.from_dict(d)
    assert p.id == "openai"
    assert not hasattr(p, "models")
    assert p.health.status == "up"
    assert p.health.latency_ms == 12.5
