"""Tests for bidirectional OpenAI <-> Anthropic translation (Issue #2).

Two layers:
  * translator.py pure functions -- request/response/stream conversion.
  * server route integration -- cross-format forwarding (OpenAI client <->
    Anthropic provider), non-streaming and streaming.
"""

import json
import tempfile
from unittest.mock import patch

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway import server as gateway_server
from llmport.gateway import translator
from llmport.gateway.handler_base import UpstreamResult
from tests._helpers import TEST_API_KEY, AuthedClient


# ============================================================================
# Fake upstream stream (for streaming tests)
# ============================================================================


class _FakeOpened:
    """Minimal stand-in for OpenedStream over a fixed list of byte chunks."""

    def __init__(self, status: int, chunks: list[bytes]):
        self.status = status
        self._chunks = chunks
        self.content_type = "text/event-stream"

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aread(self) -> bytes:
        return b"".join(self._chunks)

    async def aclose(self) -> None:
        pass


def _parse_output(buf: bytes) -> list[tuple[str | None, str | None]]:
    """Parse emitted SSE bytes back into (event, data) pairs."""
    events = []
    for block in buf.decode("utf-8").split("\n\n"):
        if not block.strip():
            continue
        ev = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].lstrip()
        events.append((ev, data))
    return events


# ============================================================================
# Request translation
# ============================================================================


class TestOpenaiToAnthropicRequest:
    def test_system_role_lifted_to_top_level(self):
        body = {
            "model": "gpt5",
            "messages": [
                {"role": "system", "content": "Be nice."},
                {"role": "user", "content": "hi"},
            ],
        }
        out = translator.openai_to_anthropic_request(body)
        assert out["system"] == "Be nice."
        assert out["messages"] == [{"role": "user", "content": "hi"}]

    def test_multiple_system_messages_joined(self):
        body = {"messages": [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "x"},
        ]}
        out = translator.openai_to_anthropic_request(body)
        assert out["system"] == "A\n\nB"

    def test_max_tokens_required_defaults_when_absent(self):
        out = translator.openai_to_anthropic_request({"messages": [
            {"role": "user", "content": "hi"}]})
        assert out["max_tokens"] == 1024

    def test_max_tokens_passed_through(self):
        out = translator.openai_to_anthropic_request({
            "max_tokens": 50,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert out["max_tokens"] == 50

    def test_sampling_params_and_stop_mapped(self):
        out = translator.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.5, "top_p": 0.9, "stop": ["END"],
        })
        assert out["temperature"] == 0.5
        assert out["top_p"] == 0.9
        assert out["stop_sequences"] == ["END"]

    def test_stop_string_wrapped_to_list(self):
        out = translator.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}], "stop": "END",
        })
        assert out["stop_sequences"] == ["END"]

    def test_stream_flag_passed(self):
        out = translator.openai_to_anthropic_request({
            "messages": [{"role": "user", "content": "hi"}], "stream": True,
        })
        assert out["stream"] is True

    def test_assistant_role_preserved(self):
        out = translator.openai_to_anthropic_request({"messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]})
        assert out["messages"][1] == {"role": "assistant", "content": "hello"}


class TestAnthropicToOpenaiRequest:
    def test_system_becomes_system_message(self):
        body = {
            "system": "Be nice.",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        }
        out = translator.anthropic_to_openai_request(body)
        assert out["messages"][0] == {"role": "system", "content": "Be nice."}
        assert out["messages"][1] == {"role": "user", "content": "hi"}
        assert out["max_tokens"] == 100

    def test_content_blocks_flattened_to_text(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
        ]}]}
        out = translator.anthropic_to_openai_request(body)
        assert out["messages"][0]["content"] == "Hello world"

    def test_stop_sequences_to_stop(self):
        body = {"messages": [{"role": "user", "content": "hi"}],
                "stop_sequences": ["END"]}
        out = translator.anthropic_to_openai_request(body)
        assert out["stop"] == "END"

    def test_stop_sequences_list_preserved(self):
        body = {"messages": [{"role": "user", "content": "hi"}],
                "stop_sequences": ["A", "B"]}
        out = translator.anthropic_to_openai_request(body)
        assert out["stop"] == ["A", "B"]


# ============================================================================
# Response translation (non-streaming)
# ============================================================================


class TestAnthropicToOpenaiResponse:
    def test_basic_mapping(self):
        body = {
            "id": "msg_1",
            "content": [{"type": "text", "text": "Hi there"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        out = translator.anthropic_to_openai_response(body, "claude")
        assert out["object"] == "chat.completion"
        assert out["model"] == "claude"
        assert out["choices"][0]["message"]["content"] == "Hi there"
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["usage"] == {"prompt_tokens": 5, "completion_tokens": 3,
                                "total_tokens": 8}

    def test_stop_reason_length_maps_to_length(self):
        body = {"content": [{"type": "text", "text": "..."}],
                "stop_reason": "max_tokens", "usage": {}}
        out = translator.anthropic_to_openai_response(body, "m")
        assert out["choices"][0]["finish_reason"] == "length"


class TestOpenaiToAnthropicResponse:
    def test_basic_mapping(self):
        body = {
            "id": "chatcmpl_1",
            "choices": [{"index": 0, "message": {"role": "assistant",
                         "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3,
                      "total_tokens": 8},
        }
        out = translator.openai_to_anthropic_response(body, "gpt5")
        assert out["type"] == "message"
        assert out["role"] == "assistant"
        assert out["model"] == "gpt5"
        assert out["content"] == [{"type": "text", "text": "Hi"}]
        assert out["stop_reason"] == "end_turn"
        assert out["usage"] == {"input_tokens": 5, "output_tokens": 3}

    def test_empty_content_yields_empty_blocks(self):
        body = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        out = translator.openai_to_anthropic_response(body, "m")
        assert out["content"] == []

    def test_finish_reason_length_maps_to_max_tokens(self):
        body = {"choices": [{"message": {"content": "..."},
                             "finish_reason": "length"}]}
        out = translator.openai_to_anthropic_response(body, "m")
        assert out["stop_reason"] == "max_tokens"


# ============================================================================
# Streaming translation
# ============================================================================


_ANTHROPIC_SSE = (
    b'event: message_start\n'
    b'data: {"type":"message_start","message":{"id":"msg_1","role":"assistant","model":"claude","content":[],"stop_reason":null,"usage":{"input_tokens":5,"output_tokens":1}}}\n\n'
    b'event: content_block_delta\n'
    b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
    b'event: content_block_delta\n'
    b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
    b'event: message_delta\n'
    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":3}}\n\n'
    b'event: message_stop\n'
    b'data: {"type":"message_stop"}\n\n'
)

_OPENAI_SSE = (
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"gpt","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"gpt","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,"model":"gpt","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    b'data: [DONE]\n\n'
)


class TestAnthropicStreamToOpenai:
    async def test_text_deltas_and_finish_and_done(self):
        out = b""
        async for chunk in translator.anthropic_stream_to_openai(
            _FakeOpened(200, [_ANTHROPIC_SSE]), "claude"
        ):
            out += chunk
        events = _parse_output(out)

        contents = []
        saw_role_chunk = False
        saw_finish = False
        saw_done = False
        for _ev, data in events:
            if data == "[DONE]":
                saw_done = True
                continue
            obj = json.loads(data)
            delta = obj["choices"][0]["delta"]
            if delta.get("role") == "assistant":
                saw_role_chunk = True
            if delta.get("content"):
                contents.append(delta["content"])
            if obj["choices"][0].get("finish_reason"):
                saw_finish = True
        assert saw_role_chunk
        assert "".join(contents) == "Hello world"
        assert saw_finish
        assert saw_done

    async def test_split_chunks_do_not_corrupt(self):
        """A multi-byte / mid-event chunk boundary must not break parsing."""
        # Split the SSE into tiny pieces.
        pieces = [_ANTHROPIC_SSE[i:i + 7] for i in range(0, len(_ANTHROPIC_SSE), 7)]
        out = b""
        async for chunk in translator.anthropic_stream_to_openai(
            _FakeOpened(200, pieces), "claude"
        ):
            out += chunk
        assert b"Hello world".decode() in "".join(
            json.loads(d)["choices"][0]["delta"].get("content", "")
            for _e, d in _parse_output(out) if d and d != "[DONE]"
        )

    async def test_stream_without_message_stop_still_closes(self):
        sse = (
            b'event: message_start\n'
            b'data: {"type":"message_start","message":{"id":"m","role":"assistant","model":"c","content":[],"stop_reason":null,"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
            b'event: content_block_delta\n'
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"x"}}\n\n'
        )
        out = b""
        async for chunk in translator.anthropic_stream_to_openai(
            _FakeOpened(200, [sse]), "c"
        ):
            out += chunk
        assert out.endswith(b"data: [DONE]\n\n")


class TestOpenaiStreamToAnthropic:
    async def test_emits_full_anthropic_event_sequence(self):
        out = b""
        async for chunk in translator.openai_stream_to_anthropic(
            _FakeOpened(200, [_OPENAI_SSE]), "gpt"
        ):
            out += chunk
        events = _parse_output(out)
        names = [ev for ev, _ in events]

        assert names[0] == "message_start"
        assert "content_block_start" in names
        # text deltas concatenate to "Hi"
        text = "".join(
            json.loads(d)["delta"]["text"]
            for ev, d in events
            if ev == "content_block_delta"
        )
        assert text == "Hi"
        assert "content_block_stop" in names
        # message_delta carries end_turn
        md = next(json.loads(d) for ev, d in events if ev == "message_delta")
        assert md["delta"]["stop_reason"] == "end_turn"
        assert names[-1] == "message_stop"

    async def test_stream_without_done_still_closes(self):
        sse = (
            b'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"gpt","choices":[{"index":0,"delta":{"content":"yo"},"finish_reason":null}]}\n\n'
        )
        out = b""
        async for chunk in translator.openai_stream_to_anthropic(
            _FakeOpened(200, [sse]), "gpt"
        ):
            out += chunk
        events = _parse_output(out)
        assert events[-1][0] == "message_stop"


# ============================================================================
# Route integration (cross-format forwarding)
# ============================================================================


_ANTHROPIC_PROVIDER = {
    "version": 1,
    "gateway": {"host": "127.0.0.1", "port": 11434},
    "providers": [
        {"name": "ant", "protocol": "anthropic",
         "base_url": "https://api.anthropic.com", "api_key": "sk-ant"},
    ],
}
_OPENAI_PROVIDER = {
    "version": 1,
    "gateway": {"host": "127.0.0.1", "port": 11434},
    "providers": [
        {"name": "oai", "protocol": "openai",
         "base_url": "https://api.openai.com", "api_key": "sk-oai"},
    ],
}


def _make_app(tmp: str, providers: dict, models: dict):
    store = ConfigStore(tmp)
    store.init_first_run()
    store.set_api_key(TEST_API_KEY)
    store.save_providers_config(providers)
    store.save_models_config(models)
    return gateway_server.create_app(store)


class TestOpenaiClientAnthropicProvider:
    """OpenAI SDK -> gateway -> Anthropic provider (translated)."""

    def test_non_stream_translates_request_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, _ANTHROPIC_PROVIDER,
                            {"models": {"claude": {"ant": "claude-sonnet-5"}}})
            client = AuthedClient(app)
            captured = {}

            anth_resp = {
                "id": "msg_1", "type": "message", "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "Hi there"}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }

            async def fake_forward(body, provider, model_name, path):
                captured["body"] = body
                captured["path"] = path
                return UpstreamResult(200, json.dumps(anth_resp).encode(),
                                      "application/json", None)

            with patch("llmport.gateway.server.anthropic_handler.forward",
                       new=fake_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "claude",
                    "messages": [
                        {"role": "system", "content": "Be nice."},
                        {"role": "user", "content": "hi"},
                    ],
                })
            assert resp.status_code == 200
            body = resp.json()
            assert body["object"] == "chat.completion"
            assert body["model"] == "claude"  # echoes client's public name
            assert body["choices"][0]["message"]["content"] == "Hi there"
            assert body["choices"][0]["finish_reason"] == "stop"
            # Upstream received Anthropic format.
            assert captured["path"] == "/v1/messages"
            assert captured["body"]["system"] == "Be nice."
            assert captured["body"]["max_tokens"] == 1024
            assert captured["body"]["messages"] == [
                {"role": "user", "content": "hi"}]

    def test_stream_translates_sse(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, _ANTHROPIC_PROVIDER,
                            {"models": {"claude": {"ant": "claude-sonnet-5"}}})
            client = AuthedClient(app)
            fake = _FakeOpened(200, [_ANTHROPIC_SSE])

            async def fake_open_stream(body, provider, model_name, path):
                return fake

            with patch("llmport.gateway.server.anthropic_handler.open_stream",
                       new=fake_open_stream):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "claude",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })
            assert resp.status_code == 200
            contents = []
            saw_done = False
            for _ev, data in _parse_output(resp.content):
                if data == "[DONE]":
                    saw_done = True
                    continue
                delta = json.loads(data)["choices"][0]["delta"]
                if delta.get("content"):
                    contents.append(delta["content"])
            assert "".join(contents) == "Hello world"
            assert saw_done


class TestAnthropicClientOpenaiProvider:
    """Anthropic SDK -> gateway -> OpenAI provider (translated)."""

    def test_non_stream_translates_request_and_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, _OPENAI_PROVIDER,
                            {"models": {"gpt5": {"oai": "gpt-5"}}})
            client = AuthedClient(app)
            captured = {}

            oai_resp = {
                "id": "chatcmpl_1", "object": "chat.completion",
                "model": "gpt-5",
                "choices": [{"index": 0, "message": {"role": "assistant",
                             "content": "Hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2,
                          "total_tokens": 6},
            }

            async def fake_forward(body, provider, model_name, path):
                captured["body"] = body
                captured["path"] = path
                return UpstreamResult(200, json.dumps(oai_resp).encode(),
                                      "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward",
                       new=fake_forward):
                resp = client.post("/anthropic/v1/messages", json={
                    "model": "gpt5",
                    "system": "Be nice.",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 50,
                })
            assert resp.status_code == 200
            body = resp.json()
            assert body["type"] == "message"
            assert body["model"] == "gpt5"
            assert body["content"] == [{"type": "text", "text": "Hi"}]
            assert body["stop_reason"] == "end_turn"
            # Upstream received OpenAI format.
            assert captured["path"] == "/v1/chat/completions"
            assert captured["body"]["messages"][0] == {
                "role": "system", "content": "Be nice."}
            assert captured["body"]["max_tokens"] == 50

    def test_stream_translates_sse(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, _OPENAI_PROVIDER,
                            {"models": {"gpt5": {"oai": "gpt-5"}}})
            client = AuthedClient(app)
            fake = _FakeOpened(200, [_OPENAI_SSE])

            async def fake_open_stream(body, provider, model_name, path):
                return fake

            with patch("llmport.gateway.server.openai_handler.open_stream",
                       new=fake_open_stream):
                resp = client.post("/anthropic/v1/messages", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                })
            assert resp.status_code == 200
            events = _parse_output(resp.content)
            names = [ev for ev, _ in events]
            assert names[0] == "message_start"
            text = "".join(
                json.loads(d)["delta"]["text"]
                for ev, d in events if ev == "content_block_delta"
            )
            assert text == "Hi"
            assert names[-1] == "message_stop"


class TestSameProtocolUnchanged:
    """Same-protocol requests must skip translation (no regression)."""

    def test_openai_client_openai_provider_no_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, _OPENAI_PROVIDER,
                            {"models": {"gpt5": {"oai": "gpt-5"}}})
            client = AuthedClient(app)
            captured = {}

            async def fake_forward(body, provider, model_name, path):
                captured["body"] = body
                return UpstreamResult(200, b'{"choices":[]}', "application/json", None)

            with patch("llmport.gateway.server.openai_handler.forward",
                       new=fake_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "gpt5",
                    "messages": [{"role": "user", "content": "hi"}],
                })
            assert resp.status_code == 200
            # Body forwarded as-is (no system lift, no max_tokens injection).
            assert captured["body"]["messages"] == [
                {"role": "user", "content": "hi"}]
            assert "max_tokens" not in captured["body"]
