"""Tests for anthropic_handler (thin wrapper over handler_base)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from llmport.models.provider import ProviderConfig


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> ProviderConfig:
    p = ProviderConfig(
        name="Anthropic",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-test-xyz",
    )
    return p


# ===========================================================================
# list_models (should NOT exist on Anthropic handler)
# ===========================================================================

@pytest.mark.asyncio
async def test_list_models_not_exposed():
    """Anthropic handler does not expose list_models (Anthropic has no model list endpoint)."""
    import llmport.gateway.anthropic_handler as handler
    assert not hasattr(handler, "list_models"), (
        "anthropic_handler should not expose list_models"
    )


# ===========================================================================
# test_connection() tests
# ===========================================================================

@pytest.mark.asyncio
async def test_test_connection_success(provider):
    """test_connection() returns (True, latency, None) for <500 status."""
    from llmport.gateway.anthropic_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("llmport.gateway.anthropic_handler.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error = await test_connection(provider)

    assert ok is True
    assert latency > 0
    assert error is None

    # url is the first positional arg to client.post()
    args, kwargs = mock_client.post.call_args
    assert "api.anthropic.com/v1/messages" in args[0]
    assert kwargs["headers"]["x-api-key"] == "sk-ant-test-xyz"
    assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
    assert kwargs["json"]["model"] == "claude-sonnet-5"
    assert kwargs["json"]["max_tokens"] == 1


@pytest.mark.asyncio
async def test_test_connection_server_error(provider):
    """test_connection() returns (False, latency, error) for >=500 status."""
    from llmport.gateway.anthropic_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 500

    with patch("llmport.gateway.anthropic_handler.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error = await test_connection(provider)

    assert ok is False
    assert latency > 0
    assert error == "Status 500"


@pytest.mark.asyncio
async def test_test_connection_client_error_is_ok(provider):
    """test_connection() considers 4xx as ok (status < 500)."""
    from llmport.gateway.anthropic_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 400

    with patch("llmport.gateway.anthropic_handler.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error = await test_connection(provider)

    assert ok is True
    assert latency > 0
    assert error is None


@pytest.mark.asyncio
async def test_test_connection_exception(provider):
    """test_connection() returns (False, 0.0, error) on network exception."""
    from llmport.gateway.anthropic_handler import test_connection

    with patch("llmport.gateway.anthropic_handler.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        ok, latency, error = await test_connection(provider)

    assert ok is False
    assert latency == 0.0
    assert error == "connection refused"


@pytest.mark.asyncio
async def test_test_connection_fallback_model(provider):
    """test_connection() uses fallback model when provider has no models."""
    from llmport.gateway.anthropic_handler import test_connection

    empty_provider = ProviderConfig(
        name="Anthropic",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-test-xyz",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("llmport.gateway.anthropic_handler.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error = await test_connection(empty_provider)

    assert ok is True
    # When no models are configured, the fallback "claude-sonnet-5" is used
    _args, kwargs = mock_client.post.call_args
    assert kwargs["json"]["model"] == "claude-sonnet-5"


# ===========================================================================
# forward() delegation
# ===========================================================================

@pytest.mark.asyncio
async def test_forward_delegates(provider):
    """anthropic_handler.forward() delegates to handler_base.forward().

    The imported ``_forward`` function is called with
    ``(body, provider, model_name, path, headers)`` — all positional.
    """
    from llmport.gateway.anthropic_handler import forward

    with patch("llmport.gateway.anthropic_handler._forward",
               new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = ({"content": [{"text": "Hello"}]}, None)

        result, error = await forward(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "claude-sonnet-5",
        )

    assert result == {"content": [{"text": "Hello"}]}
    assert error is None
    mock_fwd.assert_awaited_once()

    args, _kwargs = mock_fwd.call_args
    assert args[2] == "claude-sonnet-5"            # model_name
    assert args[3] == "/v1/messages"               # path
    assert args[4] == {"x-api-key": "sk-ant-test-xyz",
                       "anthropic-version": "2023-06-01"}  # headers


@pytest.mark.asyncio
async def test_forward_delegates_custom_path(provider):
    """anthropic_handler.forward() passes custom path through."""
    from llmport.gateway.anthropic_handler import forward

    with patch("llmport.gateway.anthropic_handler._forward",
               new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = (None, "Anthropic error")

        result, error = await forward(
            {}, provider, "claude-sonnet-5", path="/v1/complete"
        )

    assert result is None
    assert error == "Anthropic error"
    args, _kwargs = mock_fwd.call_args
    assert args[3] == "/v1/complete"


# ===========================================================================
# stream() delegation
# ===========================================================================

@pytest.mark.asyncio
async def test_stream_delegates(provider):
    """anthropic_handler.stream() delegates to handler_base.stream()."""
    from llmport.gateway.anthropic_handler import stream

    async def _mock_stream(*a, **kw):
        yield b"event: ping\n"
        yield b"data: response\n\n"

    with patch("llmport.gateway.anthropic_handler._stream") as mock_str:
        mock_str.return_value = _mock_stream()

        collected = []
        async for chunk in stream(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "claude-sonnet-5",
        ):
            collected.append(chunk)

    assert collected == [b"event: ping\n", b"data: response\n\n"]
    mock_str.assert_called_once()

    args, _kwargs = mock_str.call_args
    assert args[2] == "claude-sonnet-5"            # model_name
    assert args[3] == "/v1/messages"               # path
    assert args[4] == {"x-api-key": "sk-ant-test-xyz",
                       "anthropic-version": "2023-06-01"}  # headers
