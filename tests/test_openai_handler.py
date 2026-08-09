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
        name="OpenAI",
        protocol="openai",
        base_url="https://api.openai.com",
        api_key="sk-test-123",
    )


# ===========================================================================
# list_models() tests
# ===========================================================================
# openai_handler imports httpx at module level, so we patch the global
# ``httpx.AsyncClient`` (the handler references the same module object).

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
# test_connection() tests
# ===========================================================================

@pytest.mark.asyncio
async def test_test_connection_success(provider):
    """test_connection() returns (True, latency, None, reply) on 2xx."""
    from llmport.gateway.openai_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "有效"}}]
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error, reply = await test_connection(provider, "gpt-5")

    assert ok is True
    assert error is None
    assert reply == "有效"
    assert latency >= 0
    args, kwargs = mock_client.post.call_args
    assert args[0] == "https://api.openai.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test-123"
    assert kwargs["json"]["model"] == "gpt-5"
    assert kwargs["json"]["max_tokens"] == 128  # room for reasoning models to reply
    # the prompt asks for a one-word reply, so output stays tiny
    assert kwargs["json"]["messages"][0]["content"] == "只回复：有效"


@pytest.mark.asyncio
async def test_test_connection_reasoning_model(provider):
    """Reasoning model with empty ``content`` falls back to ``reasoning_content``."""
    from llmport.gateway.openai_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{
            "message": {"content": "", "reasoning_content": "思考中…"},
            "finish_reason": "length",
        }]
    }

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, _latency, error, reply = await test_connection(provider, "deepseek-v4-pro")

    assert ok is True
    assert error is None
    assert reply == "思考中…"  # fell back to reasoning_content


@pytest.mark.asyncio
async def test_test_connection_success_no_reply(provider):
    """2xx with a non-standard body is still ok; reply is None (best-effort)."""
    from llmport.gateway.openai_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"unexpected": "shape"}

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error, reply = await test_connection(provider, "gpt-5")

    assert ok is True
    assert error is None
    assert reply is None


@pytest.mark.asyncio
async def test_test_connection_key_invalid(provider):
    """test_connection() reports an invalid key on 401/403."""
    from llmport.gateway.openai_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 401

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error, reply = await test_connection(provider, "gpt-5")

    assert ok is False
    assert reply is None
    assert "key 无效" in error
    assert "401" in error


@pytest.mark.asyncio
async def test_test_connection_model_not_found(provider):
    """404 means the model name doesn't exist -- a model issue, not the key."""
    from llmport.gateway.openai_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error, reply = await test_connection(provider, "no-such-model")

    assert ok is False
    assert reply is None
    assert "404" in error
    assert "no-such-model" in error


@pytest.mark.asyncio
async def test_test_connection_other_error_status(provider):
    """test_connection() reports the upstream status for other 4xx/5xx."""
    from llmport.gateway.openai_handler import test_connection

    mock_resp = MagicMock()
    mock_resp.status_code = 429

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        ok, latency, error, reply = await test_connection(provider, "gpt-5")

    assert ok is False
    assert reply is None
    assert "429" in error


@pytest.mark.asyncio
async def test_test_connection_exception(provider):
    """test_connection() returns (False, 0.0, error, None) on network exception."""
    from llmport.gateway.openai_handler import test_connection

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("boom")

        ok, latency, error, reply = await test_connection(provider, "gpt-5")

    assert ok is False
    assert reply is None
    assert latency == 0.0
    assert error == "boom"


# ===========================================================================
# forward() delegation
# ===========================================================================

@pytest.mark.asyncio
async def test_forward_delegates(provider):
    """openai_handler.forward() delegates to handler_base.forward().

    The imported ``_forward`` function is called with
    ``(body, provider, model_name, path, headers)`` - all positional.
    """
    from llmport.gateway.openai_handler import forward
    from llmport.gateway.handler_base import UpstreamResult

    with patch("llmport.gateway.openai_handler._forward",
               new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = UpstreamResult(
            200, b'{"choices":[{"text":"Hello"}]}', "application/json", None
        )

        result = await forward(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
        )

    assert isinstance(result, UpstreamResult)
    assert result.status == 200
    assert result.body == b'{"choices":[{"text":"Hello"}]}'
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
    from llmport.gateway.handler_base import UpstreamResult

    with patch("llmport.gateway.openai_handler._forward",
               new_callable=AsyncMock) as mock_fwd:
        mock_fwd.return_value = UpstreamResult(502, b"err", None, None)

        result = await forward(
            {}, provider, "gpt-5", path="/v1/embeddings"
        )

    assert result.status == 502
    args, _kwargs = mock_fwd.call_args
    assert args[3] == "/v1/embeddings"


# ===========================================================================
# open_stream() delegation
# ===========================================================================

@pytest.mark.asyncio
async def test_open_stream_delegates(provider):
    """openai_handler.open_stream() delegates to handler_base.open_stream()."""
    from llmport.gateway.openai_handler import open_stream
    from llmport.gateway.handler_base import OpenedStream

    opened = MagicMock(spec=OpenedStream)
    with patch("llmport.gateway.openai_handler._open_stream",
               new_callable=AsyncMock) as mock_os:
        mock_os.return_value = opened

        result = await open_stream(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
        )

    assert result is opened
    mock_os.assert_awaited_once()

    args, _kwargs = mock_os.call_args
    assert args[2] == "gpt-5"          # model_name
    assert args[3] == "/v1/chat/completions"   # path
    assert args[4] == {"Authorization": "Bearer sk-test-123"}  # headers
