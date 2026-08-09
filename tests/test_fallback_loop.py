"""Tests for transparent error passthrough + health-based failover.

When a provider fails, the gateway returns the real upstream error to the
client (no silent in-request switching) and marks the provider down for a
cooldown. The NEXT request then routes to the next binding automatically
via the router (which skips down providers). Only when the upstream never
responds (timeout / unreachable) does the gateway synthesize a 504.
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app, get_state
from llmport.gateway.handler_base import UpstreamResult, OpenedStream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(tmp, provider_specs: list[tuple[str, str]]):
    """Create an app with providers built from (name, protocol) specs.

    A single logical model ``"m"`` is configured with bindings to every
    provider in list order, so failover visits them in sequence.
    """
    store = ConfigStore(tmp)
    store.init_first_run()
    pdata = store.load_providers_config()
    pdata["providers"] = [
        {
            "name": pid,
            "protocol": proto,
            "base_url": (
                "https://api.example.com"
                if proto == "openai"
                else "https://api.anthropic.com"
            ),
            "api_key": "sk-test",
        }
        for pid, proto in provider_specs
    ]
    store.save_providers_config(pdata)
    store.save_models_config({"models": {
        "m": [{pid: f"model-{pid}"} for pid, _ in provider_specs],
    }})
    return create_app(store)


def _opened(status, *, body=b"", chunks=None, content_type="application/json"):
    """Build a real OpenedStream backed by a mock resp/client."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.aread = AsyncMock(return_value=body)
    if chunks is not None:
        async def _gen():
            for c in chunks:
                yield c
        resp.aiter_bytes = _gen
    resp.aclose = AsyncMock()
    client = MagicMock()
    client.aclose = AsyncMock()
    return OpenedStream(resp, client)


# ---------------------------------------------------------------------------
# Non-streaming: transparent passthrough + mark down
# ---------------------------------------------------------------------------

class TestNonStreamingPassthrough:

    def test_5xx_returned_verbatim_and_marks_down(self):
        """A 503 is passed through to the client (real status + body); provider marked down."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                if provider.name == "p1":
                    return UpstreamResult(503, b'{"error":"overloaded"}', "application/json", None)
                return UpstreamResult(200, b'{"id":"ok"}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 503, f"Expected 503, got {resp.status_code}"
            assert resp.json() == {"error": "overloaded"}
            # p1 marked down (availability failure)
            assert get_state().providers[0].health.is_down()

    def test_4xx_returned_verbatim_not_marked_down(self):
        """A 404 is passed through but the provider is NOT marked down (not availability)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                return UpstreamResult(404, b'{"error":"model not found"}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 404
            assert resp.json() == {"error": "model not found"}
            assert not get_state().providers[0].health.is_down()

    def test_429_marks_down(self):
        """429 (rate limited) is an availability failure -> provider marked down."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                return UpstreamResult(429, b'{"error":"rate limited"}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 429
            assert get_state().providers[0].health.is_down()

    def test_no_response_returns_504_and_marks_down(self):
        """A timeout (no upstream response) yields 504 and marks the provider down."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                return UpstreamResult(None, b"", None, "timeout")

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 504
            assert "timeout" in resp.json()["error"]
            assert get_state().providers[0].health.is_down()

    def test_success_returned_verbatim(self):
        """A 2xx body is passed through byte-for-byte."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                return UpstreamResult(200, b'{"id":"ok","choices":[]}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            assert resp.json()["id"] == "ok"


# ---------------------------------------------------------------------------
# Next-request failover (no in-request switching)
# ---------------------------------------------------------------------------

class TestNextRequestFailover:

    def test_next_request_routes_around_down_provider(self):
        """p1 fails (503) on request 1 -> marked down; request 2 routes to p2."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            call_log = []

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                call_log.append(provider.name)
                if provider.name == "p1":
                    return UpstreamResult(503, b'{"error":"overloaded"}', "application/json", None)
                return UpstreamResult(200, b'{"id":"ok"}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                r1 = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })
                r2 = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            # Request 1 hit p1 (failed, returned real 503); request 2 skipped
            # the now-down p1 and hit p2.
            assert call_log == ["p1", "p2"]
            assert r1.status_code == 503
            assert r2.status_code == 200
            assert r2.json()["id"] == "ok"

    def test_first_request_does_not_silently_switch(self):
        """Within ONE request, a failure is returned to the client (no silent B)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            call_log = []

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                call_log.append(provider.name)
                return UpstreamResult(503, b'{"error":"overloaded"}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            # Only p1 was tried in this request; the client saw the real 503.
            assert call_log == ["p1"]
            assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Streaming: peek status before committing 200
# ---------------------------------------------------------------------------

class TestStreamingPassthrough:

    def test_upstream_error_returned_verbatim_not_200(self):
        """A 503 on the stream is returned as 503 + body, not 200 + fake [ERROR] text."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            opened = _opened(503, body=b'{"error":"overloaded"}')

            with patch("llmport.gateway.server.openai_handler.open_stream",
                       new=AsyncMock(return_value=opened)):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 503
            assert resp.json() == {"error": "overloaded"}
            assert b"[ERROR]" not in resp.content
            assert get_state().providers[0].health.is_down()

    def test_success_pipes_bytes(self):
        """A 2xx stream is piped through as SSE (200 committed only after success)."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            opened = _opened(200, chunks=[
                b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
                b"data: [DONE]\n\n",
            ], content_type="text/event-stream")

            with patch("llmport.gateway.server.openai_handler.open_stream",
                       new=AsyncMock(return_value=opened)):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200
            assert b"Hi" in resp.content
            assert b"[DONE]" in resp.content
            assert not get_state().providers[0].health.is_down()

    def test_no_response_returns_504(self):
        """A streaming connect failure (no response) yields 504, not 200."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai")])
            client = TestClient(app)

            with patch("llmport.gateway.server.openai_handler.open_stream",
                       new=AsyncMock(return_value="unreachable")):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 504
            assert "unreachable" in resp.json()["error"]
            assert get_state().providers[0].health.is_down()

    def test_stream_failure_then_next_request_routes_around(self):
        """Stream 503 marks p1 down; next (non-stream) request routes to p2."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            call_log = []

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                call_log.append(provider.name)
                return UpstreamResult(200, b'{"id":"ok"}', "application/json", None)

            opened = _opened(503, body=b'{"error":"overloaded"}')
            with patch("llmport.gateway.server.openai_handler.open_stream",
                       new=AsyncMock(return_value=opened)):
                r1 = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })
            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                r2 = client.post("/openai/v1/chat/completions", json={
                    "model": "m", "messages": [{"role": "user", "content": "hi"}],
                })

            assert r1.status_code == 503
            assert call_log == ["p2"]  # p1 down -> request 2 routed to p2
            assert r2.status_code == 200
