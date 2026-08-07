"""Tests for streaming / non-streaming fallback loop (Issue 4).

Verifies that when the primary provider fails, the server loops through
subsequent fallback bindings, skips protocol-mismatched providers, and
returns an appropriate error when all are exhausted.
"""

import tempfile
from unittest.mock import patch

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(tmp, provider_specs: list[tuple[str, str]]):
    """Create an app with providers built from (id, protocol) specs.

    A single logical model ``"m"`` is configured with bindings to every
    provider in priority order, so the fallback chain visits them in
    sequence (p1 priority 1, p2 priority 2, ...).
    """
    store = ConfigStore(tmp)
    store.init_first_run()
    pdata = store.load_providers_config()
    pdata["providers"] = [
        {
            "id": pid,
            "name": f"Provider {pid}",
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
    store.save_models_config({"models": [
        {
            "name": "m",
            "bindings": [
                {"provider": pid, "upstream": f"model-{pid}", "priority": i + 1}
                for i, (pid, _) in enumerate(provider_specs)
            ],
        }
    ]})
    return create_app(store)

# ---------------------------------------------------------------------------
# Streaming fallback loop
# ---------------------------------------------------------------------------

class TestStreamingFallbackLoop:

    def test_fallback_to_next_openai_provider(self):
        """Primary fails with [ERROR]; fallback to next openai provider succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            async def mock_stream(body, provider, model_name, path="/v1/chat/completions"):
                if provider.id == "p1":
                    yield b"data: [ERROR] p1 failed\n\n"
                else:
                    yield b'data: {"id":"ok","choices":[{"delta":{"content":"Hello"}}]}\n\n'
                    yield b"data: [DONE]\n\n"

            with patch("llmport.gateway.server.openai_handler.stream", new=mock_stream):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            assert b"[ERROR]" not in resp.content, (
                "Fallback should have succeeded, but response still contains [ERROR]"
            )
            assert b"Hello" in resp.content

    def test_skips_anthropic_fallback(self):
        """Primary fails; fallback with wrong protocol (anthropic) is skipped;
        the next openai fallback is tried."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "anthropic"), ("p3", "openai")])
            client = TestClient(app)

            call_log = []

            async def mock_stream(body, provider, model_name, path="/v1/chat/completions"):
                call_log.append(provider.id)
                if provider.id in ("p1",):
                    yield b"data: [ERROR] fail\n\n"
                elif provider.id == "p3":
                    yield b'data: {"id":"ok","choices":[{"delta":{"content":"OK"}}]}\n\n'
                    yield b"data: [DONE]\n\n"

            with patch("llmport.gateway.server.openai_handler.stream", new=mock_stream):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200
            # The anthropic provider (p2) must never be called on the openai handler
            # (protocol mismatch → skip to next matching provider)
            assert "p2" not in call_log, (
                f"Anthropic provider p2 should not be called via openai handler; "
                f"call_log = {call_log}"
            )
            # Final success should come from p3
            assert call_log[-1] == "p3", (
                f"Expected final call to p3, got call_log = {call_log}"
            )
            assert b"OK" in resp.content

    def test_all_streaming_fallbacks_exhausted(self):
        """Every provider fails; the final streaming response contains [ERROR]."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            async def mock_stream(body, provider, model_name, path="/v1/chat/completions"):
                yield b"data: [ERROR] all dead\n\n"

            with patch("llmport.gateway.server.openai_handler.stream", new=mock_stream):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })

            assert resp.status_code == 200
            assert b"[ERROR]" in resp.content, (
                "Expected [ERROR] in response when all fallbacks exhausted"
            )


# ---------------------------------------------------------------------------
# Non-streaming fallback loop
# ---------------------------------------------------------------------------

class TestNonStreamingFallbackLoop:

    def test_fallback_to_next_openai_provider(self):
        """Primary returns error; fallback to next openai provider succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            call_log = []

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                call_log.append(provider.id)
                if provider.id == "p1":
                    return None, "p1 error"
                return {"id": "ok", "choices": [], "usage": {"total_tokens": 5}}, None

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            assert resp.json()["id"] == "ok"
            assert call_log == ["p1", "p2"]

    def test_skips_anthropic_fallback_non_streaming(self):
        """Primary fails; anthropic fallback is skipped; next openai succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "anthropic"), ("p3", "openai")])
            client = TestClient(app)

            call_log = []

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                call_log.append(provider.id)
                if provider.id == "p1":
                    return None, "p1 error"
                elif provider.id == "p3":
                    return {"id": "p3-result", "choices": [],
                            "usage": {"total_tokens": 7}}, None

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 200
            # p2 (anthropic) should not appear in forward calls
            assert call_log == ["p1", "p3"], (
                f"Expected calls [p1, p3], got {call_log}"
            )
            assert resp.json()["id"] == "p3-result"

    def test_all_non_streaming_fallbacks_exhausted(self):
        """Every provider returns an error; the response is 502."""
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, [("p1", "openai"), ("p2", "openai")])
            client = TestClient(app)

            async def mock_forward(body, provider, model_name, path="/v1/chat/completions"):
                return None, "provider error"

            with patch("llmport.gateway.server.openai_handler.forward", new=mock_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                })

            assert resp.status_code == 502, (
                f"Expected 502 when all fallbacks exhausted, got {resp.status_code}"
            )
            assert "error" in resp.json()
