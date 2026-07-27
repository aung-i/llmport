"""Tests for gateway router."""

import pytest
from llmport.models.provider import ProviderConfig, ProviderHealth
from llmport.models.model import LogicalModel, ModelBinding
from llmport.gateway.router import Router, RouterError


def make_provider(id: str, health: str = "up") -> ProviderConfig:
    return ProviderConfig(
        id=id,
        name=id.title(),
        protocol="openai",
        base_url=f"https://api.{id}.com",
        api_key="sk-test",
        health=ProviderHealth(status=health),
    )


def test_resolve_returns_first_healthy_provider():
    p1 = make_provider("a")
    p2 = make_provider("b")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model", priority=1),
            ModelBinding(provider="b", upstream="b-model", priority=2),
        ],
    )
    router = Router([p1, p2], [model])
    provider, model_name = router.resolve("shared")
    assert provider.id == "a"
    assert model_name == "a-model"


def test_resolve_skips_down_provider():
    p1 = make_provider("a", health="down")
    p2 = make_provider("b")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model", priority=1),
            ModelBinding(provider="b", upstream="b-model", priority=2),
        ],
    )
    router = Router([p1, p2], [model])
    provider, model_name = router.resolve("shared")
    assert provider.id == "b"
    assert model_name == "b-model"


def test_resolve_no_healthy_provider_raises():
    p1 = make_provider("a", health="down")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model", priority=1),
        ],
    )
    router = Router([p1], [model])
    with pytest.raises(RouterError, match="No healthy provider"):
        router.resolve("shared")


def test_resolve_unknown_model_raises():
    router = Router([], [])
    with pytest.raises(RouterError, match="Unknown model"):
        router.resolve("nope")


def test_resolve_missing_model_raises():
    router = Router([], [])
    with pytest.raises(RouterError, match="missing the 'model' field"):
        router.resolve(None)
    with pytest.raises(RouterError, match="missing the 'model' field"):
        router.resolve("")


def test_try_fallback_finds_next():
    p1 = make_provider("a")
    p2 = make_provider("b")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model", priority=1),
            ModelBinding(provider="b", upstream="b-model", priority=2),
        ],
    )
    router = Router([p1, p2], [model])
    result = router.try_fallback("shared", "a")
    assert result is not None
    assert result[0].id == "b"
    assert result[1] == "b-model"


def test_try_fallback_skips_down_provider():
    p1 = make_provider("a")
    p2 = make_provider("b", health="down")
    p3 = make_provider("c")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model", priority=1),
            ModelBinding(provider="b", upstream="b-model", priority=2),
            ModelBinding(provider="c", upstream="c-model", priority=3),
        ],
    )
    router = Router([p1, p2, p3], [model])
    result = router.try_fallback("shared", "a")
    assert result is not None
    assert result[0].id == "c"
    assert result[1] == "c-model"


def test_try_fallback_exhausted_returns_none():
    p1 = make_provider("a")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model", priority=1),
        ],
    )
    router = Router([p1], [model])
    result = router.try_fallback("shared", "a")
    assert result is None


def test_try_fallback_unknown_model_returns_none():
    router = Router([], [])
    assert router.try_fallback("nope", "a") is None


def test_try_fallback_missing_model_returns_none():
    router = Router([], [])
    assert router.try_fallback(None, "a") is None
    assert router.try_fallback("", "a") is None
