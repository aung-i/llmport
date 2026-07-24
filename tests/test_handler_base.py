"""Tests for handler_base (shared HTTP forwarding logic).

Uses ``unittest.mock`` to patch ``httpx.AsyncClient`` so no real network
calls are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from llmport.gateway.handler_base import forward, stream
from llmport.models.provider import ProviderConfig


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> ProviderConfig:
    return ProviderConfig(
        id="test",
        name="Test",
        protocol="openai",
        base_url="https://api.example.com",
        api_key="sk-test",
    )


def _async_gen(*items):
    """Return an async generator that yields *items."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


# ===========================================================================
# forward() tests
# ===========================================================================

@pytest.mark.asyncio
async def test_forward_success(provider):
    """forward() returns (response_body, None) on 2xx."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"text": "Hello"}]}

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result, error = await forward(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
            "/v1/chat/completions",
            {"Authorization": "Bearer test"},
        )

    assert result == {"choices": [{"text": "Hello"}]}
    assert error is None

    # URL is the first positional arg
    args, kwargs = mock_client.post.call_args
    assert args[0] == "https://api.example.com/v1/chat/completions"
    # model injected into JSON body
    assert kwargs["json"]["model"] == "gpt-5"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["headers"] == {"Authorization": "Bearer test"}
    assert kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_forward_error_status(provider):
    """forward() returns (None, error_string) on 4xx/5xx."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Rate limited"

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result, error = await forward(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result is None
    assert "Provider returned 429" in error
    assert "Rate limited" in error


@pytest.mark.asyncio
async def test_forward_timeout(provider):
    """forward() returns (None, "Provider timeout") on httpx.TimeoutException."""
    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("timed out")

        result, error = await forward(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result is None
    assert error == "Provider timeout"


@pytest.mark.asyncio
async def test_forward_connect_error(provider):
    """forward() returns (None, "Provider unreachable") on httpx.ConnectError."""
    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        result, error = await forward(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result is None
    assert error == "Provider unreachable"


# ===========================================================================
# stream() tests
# ===========================================================================

@pytest.mark.asyncio
async def test_stream_success(provider):
    """stream() yields raw SSE chunks on 2xx."""
    chunks = [b'data: {"key":"value"}\n\n', b"data: [DONE]\n\n"]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_bytes = lambda: _async_gen(*chunks)

    stream_cm = MagicMock()
    stream_cm.__aenter__.return_value = mock_resp

    stream_mock = MagicMock(return_value=stream_cm)

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.stream = stream_mock

        collected = []
        async for chunk in stream(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
            "/v1/chat/completions",
            {"Authorization": "Bearer test"},
        ):
            collected.append(chunk)

    assert collected == chunks

    args, kwargs = mock_client.stream.call_args
    assert args[0] == "POST"
    assert args[1] == "https://api.example.com/v1/chat/completions"
    assert kwargs["json"]["stream"] is True
    assert kwargs["json"]["model"] == "gpt-5"
    assert kwargs["headers"]["Accept"] == "text/event-stream"
    assert kwargs["headers"]["Authorization"] == "Bearer test"


@pytest.mark.asyncio
async def test_stream_error_status(provider):
    """stream() yields a single [ERROR] event on 4xx/5xx."""
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.aread = AsyncMock(return_value=b"Bad Gateway")

    stream_cm = MagicMock()
    stream_cm.__aenter__.return_value = mock_resp
    stream_mock = MagicMock(return_value=stream_cm)

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.stream = stream_mock

        collected = []
        async for chunk in stream(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        ):
            collected.append(chunk)

    assert len(collected) == 1
    assert b"[ERROR] Provider 502" in collected[0]
    assert b"Bad Gateway" in collected[0]


@pytest.mark.asyncio
async def test_stream_timeout(provider):
    """stream() yields timeout error on httpx.TimeoutException."""
    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(side_effect=httpx.TimeoutException("timed out"))

        collected = []
        async for chunk in stream(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        ):
            collected.append(chunk)

    assert len(collected) == 1
    assert collected[0] == b"data: [ERROR] Provider timeout\n\n"


@pytest.mark.asyncio
async def test_stream_connect_error(provider):
    """stream() yields connect error on httpx.ConnectError."""
    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.stream = MagicMock(side_effect=httpx.ConnectError("connection refused"))

        collected = []
        async for chunk in stream(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        ):
            collected.append(chunk)

    assert len(collected) == 1
    assert collected[0] == b"data: [ERROR] Provider unreachable\n\n"


@pytest.mark.asyncio
async def test_stream_bytes_input(provider):
    """stream() accepts bytes input, parses as JSON, and yields chunks."""
    chunks = [b'data: hello\n\n']

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_bytes = lambda: _async_gen(*chunks)

    stream_cm = MagicMock()
    stream_cm.__aenter__.return_value = mock_resp
    stream_mock = MagicMock(return_value=stream_cm)

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.stream = stream_mock

        body_bytes = b'{"messages": [{"role": "user", "content": "hi"}]}'
        collected = []
        async for chunk in stream(
            body_bytes, provider, "gpt-5", "/v1/chat/completions", {}
        ):
            collected.append(chunk)

    assert collected == chunks

    # Verify bytes were decoded and model/stream were injected
    args, kwargs = mock_client.stream.call_args
    assert kwargs["json"]["model"] == "gpt-5"
    assert kwargs["json"]["stream"] is True
    assert "messages" in kwargs["json"]
