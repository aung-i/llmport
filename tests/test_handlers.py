"""Tests for protocol handlers. Uses httpx mock or simple unit checks."""

import pytest
from llmport.models.provider import ProviderConfig


def make_provider(id: str = "test") -> ProviderConfig:
    return ProviderConfig(
        id=id,
        name=id.title(),
        protocol="openai",
        base_url="https://api.example.com",
        api_key="sk-test",
    )


def test_openai_handler_exists():
    from llmport.gateway import openai_handler
    assert hasattr(openai_handler, "forward")
    assert hasattr(openai_handler, "stream")
    assert hasattr(openai_handler, "list_models")


def test_anthropic_handler_exists():
    from llmport.gateway import anthropic_handler
    assert hasattr(anthropic_handler, "forward")
    assert hasattr(anthropic_handler, "stream")
    assert hasattr(anthropic_handler, "test_connection")
