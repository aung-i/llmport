"""Tests for request_count and total_tokens tracking (Issue 1).

Covers non-streaming and streaming paths for both request counting and
usage token accumulation as specified in the design spec.

Routing is by client-sent ``model`` field, so every request must include
``"model": "gpt5"`` matching the configured logical model name.
"""

import tempfile
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway import server as gateway_server


_SUCCESS_RESPONSE = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
}

_SUCCESS_RESPONSE_NO_USAGE = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}}],
}


def _make_app(tmp):
    """Create a gateway app with one OpenAI provider and a 'gpt5' model."""
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_providers_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {
                "id": "test-p",
                "name": "Test",
                "protocol": "openai",
                "base_url": "https://api.example.com",
                "api_key": "sk-test",
            },
        ],
    })
    store.save_models_config({"models": [
        {"name": "gpt5", "provider": "test-p", "upstream": "gpt-5"},
    ]})
    return gateway_server.create_app(store)


# ──────────────────────────────────────────────
# Non-streaming
# ──────────────────────────────────────────────

class TestNonStreamingStats:

    def test_increments_request_count(self):
        """After a successful non-streaming call, request_count increments by 1."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            assert gateway_server.get_state().request_count == 0

            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(_SUCCESS_RESPONSE, None)),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().request_count == 1

    def test_accumulates_total_tokens(self):
        """total_tokens is extracted from the usage field of the response."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            assert gateway_server.get_state().total_tokens == 0

            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(_SUCCESS_RESPONSE, None)),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().total_tokens == 30

    def test_accumulates_multiple_calls(self):
        """Multiple requests sum request_count and total_tokens."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)

            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(_SUCCESS_RESPONSE, None)),
            ):
                for _ in range(3):
                    resp = client.post("/openai/v1/chat/completions", json={
                        "model": "gpt5",
                        "messages": [{"role": "user", "content": "hi"}],
                    })
                    assert resp.status_code == 200

            assert gateway_server.get_state().request_count == 3
            assert gateway_server.get_state().total_tokens == 90  # 3 * 30

    def test_no_usage_field_does_not_error(self):
        """When the response has no usage field, no error is raised and
        request_count still increments.  total_tokens stays unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)

            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(_SUCCESS_RESPONSE_NO_USAGE, None)),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().request_count == 1
            assert gateway_server.get_state().total_tokens == 0

    def test_negative_usage_sanitised(self):
        """Negative total_tokens is sanitised to 0 via max(0, value)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)

            bad = {**_SUCCESS_RESPONSE, "usage": {"total_tokens": -5}}
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(bad, None)),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            # max(0, -5) => 0, so total_tokens stays 0
            assert gateway_server.get_state().total_tokens == 0

    def test_non_int_usage_skipped(self):
        """Non-integer total_tokens (e.g. string) is skipped without error."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)

            bad = {**_SUCCESS_RESPONSE, "usage": {"total_tokens": "abc"}}
            with patch(
                "llmport.gateway.server.openai_handler.forward",
                new=AsyncMock(return_value=(bad, None)),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().request_count == 1
            # total_tokens should remain unchanged (0)
            assert gateway_server.get_state().total_tokens == 0


# ──────────────────────────────────────────────
# Streaming
# ──────────────────────────────────────────────

class TestStreamingStats:

    SSE_WITH_USAGE = [
        b'data: {"id":"x","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hi"}}],"usage":null}\n\n',
        b'data: {"id":"x","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
        b"data: [DONE]\n\n",
    ]

    SSE_NO_USAGE = [
        b'data: {"id":"x","object":"chat.completion.chunk","choices":[{"delta":{"content":"Hi"}}],"usage":null}\n\n',
        b'data: {"id":"x","object":"chat.completion.chunk","choices":[]}\n\n',
        b"data: [DONE]\n\n",
    ]

    @staticmethod
    def _stream(chunks):
        """Return an async-generator function that yields *chunks*."""
        async def gen(body, provider, model_name, path="/v1/chat/completions"):
            for c in chunks:
                yield c
        return gen

    def test_streaming_increments_request_count(self):
        """After a streaming call completes, request_count increments by 1."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            assert gateway_server.get_state().request_count == 0

            with patch(
                "llmport.gateway.server.openai_handler.stream",
                new=self._stream(self.SSE_WITH_USAGE),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().request_count == 1

    def test_streaming_parses_usage(self):
        """total_tokens is extracted from usage in the final SSE chunk."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)
            assert gateway_server.get_state().total_tokens == 0

            with patch(
                "llmport.gateway.server.openai_handler.stream",
                new=self._stream(self.SSE_WITH_USAGE),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().total_tokens == 7

    def test_streaming_no_usage_does_not_error(self):
        """When streaming chunks contain no usage, no error is raised."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            client = TestClient(app)

            with patch(
                "llmport.gateway.server.openai_handler.stream",
                new=self._stream(self.SSE_NO_USAGE),
            ):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200
            assert gateway_server.get_state().request_count == 1
            assert gateway_server.get_state().total_tokens == 0
