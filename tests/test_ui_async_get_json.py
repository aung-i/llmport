"""Tests for async_get_json — HTTP JSON fetch helper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAsyncGetJson:
    """async_get_json should return parsed JSON on success, None on errors."""

    @pytest.mark.asyncio
    async def test_success_returns_parsed_json(self):
        """200 response with JSON body returns the parsed data."""
        from llmport.ui import async_get_json

        # httpx.Response.json() is synchronous, so use MagicMock for response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"key": "value"}

        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await async_get_json("http://example.com/api")

        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        """Non-200 status code returns None."""
        from llmport.ui import async_get_json

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await async_get_json("http://example.com/api")

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        """Timeout or connection error returns None (no crash)."""
        from llmport.ui import async_get_json

        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(side_effect=Exception("Connection timeout"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await async_get_json("http://example.com/api")

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        """Invalid JSON in response body returns None."""
        from llmport.ui import async_get_json

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")

        mock_client = MagicMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await async_get_json("http://example.com/api")

        assert result is None
