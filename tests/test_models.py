"""Tests for data models."""

from llmport.models.provider import ProviderConfig, ProviderModel
from llmport.models.model import (
    ModelBinding,
    LogicalModel,
    merge_aliases_into_logical_models,
)


def make_provider(id: str, models: list[tuple[str, list[str]]]) -> ProviderConfig:
    return ProviderConfig(
        id=id,
        name=id.title(),
        protocol="openai",
        base_url=f"https://api.{id}.com",
        api_key="sk-test",
        models=[ProviderModel(name=n, aliases=a) for n, a in models],
    )


def test_merge_single_provider_creates_models_from_aliases():
    providers = [
        make_provider("openai", [
            ("gpt-5", ["gpt5", "gpt"]),
            ("gpt-4o", ["gpt4"]),
        ]),
    ]
    models = merge_aliases_into_logical_models(providers)
    assert len(models) == 3
    ids = {m.id for m in models}
    assert ids == {"gpt5", "gpt", "gpt4"}


def test_same_alias_on_two_providers_merges():
    providers = [
        make_provider("anthropic", [("claude-opus-4-8", ["claude-opus"])]),
        make_provider("openai", [("claude-opus-4-8", ["claude-opus"])]),
    ]
    models = merge_aliases_into_logical_models(providers)
    assert len(models) == 1
    assert models[0].id == "claude-opus"
    assert models[0].provider_count == 2
    assert len(models[0].bindings) == 2


def test_model_without_alias_uses_name():
    providers = [
        make_provider("openai", [("gpt-5", [])]),
    ]
    models = merge_aliases_into_logical_models(providers)
    assert models[0].id == "gpt-5"


def test_bindings_are_sorted_by_priority():
    m = LogicalModel(
        id="test",
        bindings=[
            ModelBinding(provider_id="b", model_name="m2", priority=2),
            ModelBinding(provider_id="a", model_name="m1", priority=1),
        ],
    )
    assert m.bindings_sorted[0].provider_id == "a"
    assert m.bindings_sorted[1].provider_id == "b"


def test_provider_config_roundtrip():
    p = ProviderConfig(
        id="openai",
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-secret",
        models=[ProviderModel(name="gpt-5", aliases=["gpt5"])],
    )
    d = p.to_dict()
    p2 = ProviderConfig.from_dict(d)
    assert p2.id == p.id
    assert p2.api_key == p.api_key
    assert p2.models[0].aliases == ["gpt5"]
