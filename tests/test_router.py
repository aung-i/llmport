"""Tests for gateway router."""

import pytest
from llmport.models.provider import ProviderConfig, ProviderModel, ProviderHealth
from llmport.models.model import LogicalModel, ModelBinding
from llmport.gateway.router import Router, RouterError


def make_provider(id: str, health: str = "up") -> ProviderConfig:
    return ProviderConfig(
        id=id,
        name=id.title(),
        protocol="openai",
        base_url=f"https://api.{id}.com",
        api_key="sk-test",
        models=[ProviderModel(name=f"{id}-model", aliases=["shared"])],
        health=ProviderHealth(status=health),
    )


def test_resolve_returns_first_healthy_provider():
    p1 = make_provider("a")
    p2 = make_provider("b")
    model = LogicalModel(
        id="shared",
        bindings=[
            ModelBinding(provider_id="a", model_name="a-model", priority=1),
            ModelBinding(provider_id="b", model_name="b-model", priority=2),
        ],
    )
    router = Router([p1, p2], [model], "shared")
    provider, model_name = router.resolve()
    assert provider.id == "a"
    assert model_name == "a-model"


def test_resolve_skips_down_provider():
    p1 = make_provider("a", health="down")
    p2 = make_provider("b")
    model = LogicalModel(
        id="shared",
        bindings=[
            ModelBinding(provider_id="a", model_name="a-model", priority=1),
            ModelBinding(provider_id="b", model_name="b-model", priority=2),
        ],
    )
    router = Router([p1, p2], [model], "shared")
    provider, _ = router.resolve()
    assert provider.id == "b"


def test_resolve_no_active_model_raises():
    router = Router([], [], None)
    with pytest.raises(RouterError):
        router.resolve()


def test_try_fallback_finds_next():
    p1 = make_provider("a")
    p2 = make_provider("b")
    model = LogicalModel(
        id="shared",
        bindings=[
            ModelBinding(provider_id="a", model_name="a-model", priority=1),
            ModelBinding(provider_id="b", model_name="b-model", priority=2),
        ],
    )
    router = Router([p1, p2], [model], "shared")
    result = router.try_fallback("a")
    assert result is not None
    assert result[0].id == "b"


def test_try_fallback_exhausted_returns_none():
    p1 = make_provider("a")
    model = LogicalModel(
        id="shared",
        bindings=[
            ModelBinding(provider_id="a", model_name="a-model", priority=1),
        ],
    )
    router = Router([p1], [model], "shared")
    result = router.try_fallback("a")
    assert result is None
