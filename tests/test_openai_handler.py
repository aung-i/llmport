"""Tests for openai_handler (thin wrapper over handler_base)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from llmport.models.provider import ProviderConfig


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> ProviderConfig:
    return ProviderConfig(
        id="openai",
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-test-123",
    )


# ===========================================================================
# list_models() tests
# ===========================================================================
# NOTE: openai_handler.list_models() does ``import httpx`` inside the
# function body (no module-level httpx import), so we patch the global
# ``httpx.AsyncClient`` rather than a dotted path on the handler module.

@pytest.mark.asyncio
async def test_list_models_success(provider):
    """list_models() returns model list on 2xx."""
    from llmport.gateway.openai_handler import list_models

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [{"id": "gpt-5", "object": "model"},
                 {"id": "gpt-4", "object": "model"}],
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp

        models, error = await list_models(provider)

    assert models == [{"id": "gpt-5", "object": "model"},
                      {"id": "gpt-4", "object": "model"}]
    assert error is None

    # url is the first positional arg to client.get()
    args, kwargs = mock_client.get.call_args
    assert args[0] == "https://api.openai.com/v1/models"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test-123"


@pytest.mark.asyncio
async def test_list_models_error_status(provider):
    """list_models() returns (None, error) on 4xx/5xx."""
    from llmport.gateway.openai_handler import list_models

    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp

        models, error = await list_models(provider)

    assert models is None
    assert "Failed to fetch models" in error
    assert "401" in error


@pytest.mark.asyncio
async def test_list_models_exception(provider):
    """list_models() returns (None, str(exception)) on network error."""
    from llmport.gateway.openai_handler import list_models

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("connection failed")

        models, error = await list_models(provider)

    assert models is None
    assert error == "connection failed"


# ===========================================================================
# forward() delegation
# ===========================================================================

@pytest.mark.asyncio
async def test_forward_delegates(provider):
    """openai_handler.forward() delegates to handler_base.forward().

    The imported ``_forward`` function is called with
    ``(body, provider, model_name, path, headers)`` — all positional.
    """
    from llmport.gateway.openai_handler import forward

    with patch("llmport.gateway.openai_handler._forward",
               new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = ({"choices": [{"text": "Hello"}]}, None)

        result, error = await forward(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
        )

    assert result == {"choices": [{"text": "Hello"}]}
    assert error is None
    mock_fwd.assert_awaited_once()

    # All arguments passed positionally
    args, _kwargs = mock_fwd.call_args
    assert args[2] == "gpt-5"         # model_name
    assert args[3] == "/v1/chat/completions"  # path
    assert args[4] == {"Authorization": "Bearer sk-test-123"}  # headers


@pytest.mark.asyncio
async def test_forward_delegates_custom_path(provider):
    """openai_handler.forward() passes custom path through."""
    from llmport.gateway.openai_handler import forward

    with patch("llmport.gateway.openai_handler._forward",
               new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = (None, "error")

        result, error = await forward(
            {}, provider, "gpt-5", path="/v1/embeddings"
        )

    assert result is None
    assert error == "error"
    args, _kwargs = mock_fwd.call_args
    assert args[3] == "/v1/embeddings"


# ===========================================================================
# stream() delegation
# ===========================================================================

@pytest.mark.asyncio
async def test_stream_delegates(provider):
    """openai_handler.stream() delegates to handler_base.stream()."""
    from llmport.gateway.openai_handler import stream

    async def _mock_stream(*a, **kw):
        yield b"data: chunk1\n\n"
        yield b"data: chunk2\n\n"

    with patch("llmport.gateway.openai_handler._stream") as mock_str:
        mock_str.return_value = _mock_stream()

        collected = []
        async for chunk in stream(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
        ):
            collected.append(chunk)

    assert collected == [b"data: chunk1\n\n", b"data: chunk2\n\n"]
    mock_str.assert_called_once()

    args, _kwargs = mock_str.call_args
    assert args[2] == "gpt-5"          # model_name
    assert args[3] == "/v1/chat/completions"   # path
    assert args[4] == {"Authorization": "Bearer sk-test-123"}  # headers
