"""Tests for handler_base (shared HTTP forwarding logic).

Uses ``unittest.mock`` to patch ``httpx.AsyncClient`` so no real network
calls are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from llmport.gateway.handler_base import forward, open_stream, UpstreamResult, OpenedStream
from llmport.models.provider import ProviderConfig


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def provider() -> ProviderConfig:
    return ProviderConfig(
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
    """forward() returns UpstreamResult with the real status + body on 2xx."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"choices":[{"text":"Hello"}]}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await forward(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
            "/v1/chat/completions",
            {"Authorization": "Bearer test"},
        )

    assert isinstance(result, UpstreamResult)
    assert result.status == 200
    assert result.body == b'{"choices":[{"text":"Hello"}]}'
    assert result.content_type == "application/json"
    assert result.reason is None

    args, kwargs = mock_client.post.call_args
    assert args[0] == "https://api.example.com/v1/chat/completions"
    # model injected into JSON body
    assert kwargs["json"]["model"] == "gpt-5"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["headers"] == {"Authorization": "Bearer test"}
    assert kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_forward_error_status_keeps_real_status_and_body(provider):
    """forward() returns the real upstream status + body on 4xx/5xx (no synthesized string)."""
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.content = b'{"error":"rate limited"}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_resp

        result = await forward(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result.status == 429
    assert result.body == b'{"error":"rate limited"}'
    assert result.reason is None


@pytest.mark.asyncio
async def test_forward_timeout(provider):
    """forward() returns status=None, reason='timeout' on httpx.TimeoutException."""
    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("timed out")

        result = await forward(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result.status is None
    assert result.reason == "timeout"
    assert result.body == b""


@pytest.mark.asyncio
async def test_forward_connect_error(provider):
    """forward() returns status=None, reason='unreachable' on httpx.ConnectError."""
    with patch("llmport.gateway.handler_base.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        result = await forward(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result.status is None
    assert result.reason == "unreachable"


# ===========================================================================
# open_stream() tests
# ===========================================================================

def _mock_opened(status, *, body=b"", chunks=None, content_type="text/event-stream"):
    """Build a real OpenedStream backed by mock resp/client."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.aread = AsyncMock(return_value=body)
    if chunks is not None:
        resp.aiter_bytes = lambda: _async_gen(*chunks)
    resp.aclose = AsyncMock()
    client = MagicMock()
    client.aclose = AsyncMock()
    return OpenedStream(resp, client)


@pytest.mark.asyncio
async def test_open_stream_success(provider):
    """open_stream() returns an OpenedStream whose aiter_bytes yields SSE chunks."""
    chunks = [b'data: {"key":"value"}\n\n', b"data: [DONE]\n\n"]
    opened = _mock_opened(200, chunks=chunks)

    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(return_value=opened._resp)
    mock_client.aclose = AsyncMock()

    with patch("llmport.gateway.handler_base.httpx.AsyncClient", return_value=mock_client):
        result = await open_stream(
            {"messages": [{"role": "user", "content": "hi"}]},
            provider,
            "gpt-5",
            "/v1/chat/completions",
            {"Authorization": "Bearer test"},
        )

    assert isinstance(result, OpenedStream)
    assert result.status == 200
    collected = []
    async for chunk in result.aiter_bytes():
        collected.append(chunk)
    assert collected == chunks

    args, kwargs = mock_client.build_request.call_args
    assert args[0] == "POST"
    assert args[1] == "https://api.example.com/v1/chat/completions"
    assert kwargs["json"]["stream"] is True
    assert kwargs["json"]["model"] == "gpt-5"
    assert kwargs["headers"]["Accept"] == "text/event-stream"
    assert kwargs["headers"]["Authorization"] == "Bearer test"


@pytest.mark.asyncio
async def test_open_stream_error_status(provider):
    """open_stream() returns an OpenedStream even on 4xx/5xx (caller peeks status)."""
    opened = _mock_opened(502, body=b"Bad Gateway", content_type="text/plain")

    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(return_value=opened._resp)
    mock_client.aclose = AsyncMock()

    with patch("llmport.gateway.handler_base.httpx.AsyncClient", return_value=mock_client):
        result = await open_stream(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert isinstance(result, OpenedStream)
    assert result.status == 502
    assert await result.aread() == b"Bad Gateway"


@pytest.mark.asyncio
async def test_open_stream_timeout(provider):
    """open_stream() returns 'timeout' on httpx.TimeoutException."""
    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.aclose = AsyncMock()

    with patch("llmport.gateway.handler_base.httpx.AsyncClient", return_value=mock_client):
        result = await open_stream(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result == "timeout"
    mock_client.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_open_stream_connect_error(provider):
    """open_stream() returns 'unreachable' on httpx.ConnectError."""
    mock_client = MagicMock()
    mock_client.build_request.return_value = MagicMock()
    mock_client.send = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.aclose = AsyncMock()

    with patch("llmport.gateway.handler_base.httpx.AsyncClient", return_value=mock_client):
        result = await open_stream(
            {"messages": []}, provider, "gpt-5", "/v1/chat/completions", {}
        )

    assert result == "unreachable"
