"""Tests for gateway router."""

import time

import pytest
from llmport.models.provider import ProviderConfig, ProviderHealth
from llmport.models.model import LogicalModel, ModelBinding
from llmport.gateway.router import Router, RouterError


def make_provider(name: str, health: str = "up") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol="openai",
        base_url=f"https://api.{name}.com",
        api_key="sk-test",
        health=ProviderHealth(status=health),
    )


def test_resolve_returns_first_healthy_provider():
    p1 = make_provider("a")
    p2 = make_provider("b")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model"),
            ModelBinding(provider="b", upstream="b-model"),
        ],
    )
    router = Router([p1, p2], [model])
    provider, model_name = router.resolve("shared")
    assert provider.name == "a"
    assert model_name == "a-model"


def test_resolve_skips_down_provider():
    p1 = make_provider("a", health="down")
    p2 = make_provider("b")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model"),
            ModelBinding(provider="b", upstream="b-model"),
        ],
    )
    router = Router([p1, p2], [model])
    provider, model_name = router.resolve("shared")
    assert provider.name == "b"
    assert model_name == "b-model"


def test_resolve_no_healthy_provider_raises():
    p1 = make_provider("a", health="down")
    model = LogicalModel(
        name="shared",
        bindings=[ModelBinding(provider="a", upstream="a-model")],
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


# ---------------------------------------------------------------------------
# ProviderHealth.is_down / mark_down (runtime cooldown vs permanent SSRF)
# ---------------------------------------------------------------------------


def test_mark_down_makes_provider_skipped():
    """A runtime mark_down() makes resolve skip the provider (cooldown active)."""
    p1 = make_provider("a")
    p1.health.mark_down(30)
    p2 = make_provider("b")
    model = LogicalModel(
        name="shared",
        bindings=[
            ModelBinding(provider="a", upstream="a-model"),
            ModelBinding(provider="b", upstream="b-model"),
        ],
    )
    router = Router([p1, p2], [model])
    provider, _ = router.resolve("shared")
    assert provider.name == "b"  # a is down -> skip to b


def test_runtime_down_recovers_after_cooldown():
    """Once down_until is in the past, the provider is healthy again."""
    p1 = make_provider("a")
    p1.health.mark_down(30)
    # Simulate cooldown expiry.
    p1.health.down_until = time.time() - 1
    assert p1.health.is_down() is False

    model = LogicalModel(
        name="shared",
        bindings=[ModelBinding(provider="a", upstream="a-model")],
    )
    router = Router([p1], [model])
    provider, _ = router.resolve("shared")
    assert provider.name == "a"  # recovered


def test_permanent_down_never_recovers():
    """SSRF-style permanent down (down_until None) never recovers."""
    p1 = make_provider("a")
    p1.health.status = "down"
    p1.health.down_until = None  # permanent
    # Even far in the past it stays down.
    assert p1.health.is_down() is True


def test_is_down_false_for_up():
    p = make_provider("a")
    assert p.health.is_down() is False
