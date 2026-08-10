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
# Request translation -- tools & multimodal (Issue #4)
# ============================================================================


class TestOpenaiToAnthropicRequestTools:
    def test_tools_array_mapped(self):
        body = {"messages": [{"role": "user", "content": "weather?"}], "tools": [
            {"type": "function", "function": {
                "name": "get_weather", "description": "Get weather",
                "parameters": {"type": "object",
                               "properties": {"location": {"type": "string"}},
                               "required": ["location"]}}},
        ]}
        out = translator.openai_to_anthropic_request(body)
        assert out["tools"] == [{
            "name": "get_weather", "description": "Get weather",
            "input_schema": {"type": "object",
                             "properties": {"location": {"type": "string"}},
                             "required": ["location"]}}]

    def test_tool_choice_variants(self):
        cases = [
            ("auto", {"type": "auto"}),
            ("none", {"type": "none"}),
            ("required", {"type": "any"}),
            ({"type": "function", "function": {"name": "get_weather"}},
             {"type": "tool", "name": "get_weather"}),
        ]
        for oai, anth in cases:
            body = {"messages": [{"role": "user", "content": "x"}],
                    "tool_choice": oai}
            assert translator.openai_to_anthropic_request(body)["tool_choice"] == anth

    def test_assistant_tool_calls_become_tool_use_blocks(self):
        body = {"messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather",
                              "arguments": '{"location":"Paris"}'}}]},
        ]}
        out = translator.openai_to_anthropic_request(body)
        msg = out["messages"][1]
        assert msg["role"] == "assistant"
        assert msg["content"] == [{
            "type": "tool_use", "id": "call_1", "name": "get_weather",
            "input": {"location": "Paris"}}]

    def test_assistant_text_plus_tool_calls(self):
        body = {"messages": [
            {"role": "assistant", "content": "Sure, let me check.",
             "tool_calls": [{"id": "c1", "type": "function",
                 "function": {"name": "f", "arguments": "{}"}}]},
        ]}
        out = translator.openai_to_anthropic_request(body)
        blocks = out["messages"][0]["content"]
        assert blocks[0] == {"type": "text", "text": "Sure, let me check."}
        assert blocks[1]["type"] == "tool_use"

    def test_tool_result_messages_grouped_into_user_message(self):
        body = {"messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": "{}"}},
                {"id": "call_2", "type": "function",
                 "function": {"name": "get_time", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
            {"role": "tool", "tool_call_id": "call_2", "content": "noon"},
        ]}
        out = translator.openai_to_anthropic_request(body)
        # Two tool results collapse into one user message with two blocks.
        last = out["messages"][-1]
        assert last["role"] == "user"
        assert last["content"] == [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "sunny"},
            {"type": "tool_result", "tool_use_id": "call_2", "content": "noon"}]


class TestOpenaiToAnthropicRequestImages:
    def test_data_url_image_becomes_base64_source(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64,iVBORw0KGgo="}},
        ]}]}
        out = translator.openai_to_anthropic_request(body)
        assert out["messages"][0]["content"] == [
            {"type": "text", "text": "what is this?"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": "iVBORw0KGgo="}}]

    def test_http_url_image_becomes_url_source(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://x/a.png"}},
        ]}]}
        out = translator.openai_to_anthropic_request(body)
        assert out["messages"][0]["content"] == [
            {"type": "image", "source": {"type": "url", "url": "https://x/a.png"}}]

    def test_unsupported_media_type_dropped(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": "data:image/bmp;base64,AAAA"}},
        ]}]}
        out = translator.openai_to_anthropic_request(body)
        assert out["messages"][0]["content"] == ""

    def test_plain_string_content_unchanged(self):
        out = translator.openai_to_anthropic_request(
            {"messages": [{"role": "user", "content": "hi"}]})
        assert out["messages"][0] == {"role": "user", "content": "hi"}


class TestAnthropicToOpenaiRequestTools:
    def test_tools_array_mapped(self):
        body = {"messages": [{"role": "user", "content": "x"}], "tools": [{
            "name": "get_weather", "description": "Get weather",
            "input_schema": {"type": "object", "properties": {}}}]}
        out = translator.anthropic_to_openai_request(body)
        assert out["tools"] == [{
            "type": "function", "function": {
                "name": "get_weather", "description": "Get weather",
                "parameters": {"type": "object", "properties": {}}}}]

    def test_tool_choice_variants(self):
        cases = [
            ({"type": "auto"}, "auto"),
            ({"type": "none"}, "none"),
            ({"type": "any"}, "required"),
            ({"type": "tool", "name": "get_weather"},
             {"type": "function", "function": {"name": "get_weather"}}),
        ]
        for anth, oai in cases:
            body = {"messages": [{"role": "user", "content": "x"}],
                    "tool_choice": anth}
            assert translator.anthropic_to_openai_request(body)["tool_choice"] == oai

    def test_assistant_tool_use_becomes_tool_calls(self):
        body = {"messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "get_weather",
                 "input": {"location": "Paris"}}]},
        ]}
        out = translator.anthropic_to_openai_request(body)
        msg = out["messages"][1]
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert msg["tool_calls"] == [{
            "id": "toolu_1", "type": "function",
            "function": {"name": "get_weather",
                         "arguments": '{"location": "Paris"}'}}]

    def test_assistant_text_plus_tool_use(self):
        body = {"messages": [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "t1", "name": "f", "input": {}}]},
        ]}
        out = translator.anthropic_to_openai_request(body)
        msg = out["messages"][0]
        assert msg["content"] == "Checking."
        assert msg["tool_calls"][0]["id"] == "t1"

    def test_tool_result_blocks_become_tool_messages(self):
        body = {"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": "sunny"}]},
        ]}
        out = translator.anthropic_to_openai_request(body)
        assert out["messages"][0] == {
            "role": "tool", "tool_call_id": "toolu_1", "content": "sunny"}


class TestAnthropicToOpenaiRequestImages:
    def test_base64_image_becomes_data_url(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "see this"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": "AAA="}},
        ]}]}
        out = translator.anthropic_to_openai_request(body)
        assert out["messages"][0]["content"] == [
            {"type": "text", "text": "see this"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,AAA="}}]

    def test_url_image_becomes_image_url(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "url", "url": "https://x/a.png"}}],
        }]}
        out = translator.anthropic_to_openai_request(body)
        assert out["messages"][0]["content"] == [
            {"type": "image_url", "image_url": {"url": "https://x/a.png"}}]

    def test_text_only_blocks_collapse_to_string(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"}]}]}
        out = translator.anthropic_to_openai_request(body)
        assert out["messages"][0]["content"] == "Hello world"


# ============================================================================
# Response translation -- tools (Issue #4)
# ============================================================================


class TestAnthropicToOpenaiResponseTools:
    def test_tool_use_becomes_tool_calls(self):
        body = {
            "id": "msg_1",
            "content": [{"type": "tool_use", "id": "toolu_1",
                         "name": "get_weather", "input": {"location": "Paris"}}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        out = translator.anthropic_to_openai_response(body, "claude")
        msg = out["choices"][0]["message"]
        assert msg["content"] is None
        assert msg["tool_calls"] == [{
            "id": "toolu_1", "type": "function",
            "function": {"name": "get_weather",
                         "arguments": '{"location": "Paris"}'}}]
        assert out["choices"][0]["finish_reason"] == "tool_calls"

    def test_text_and_tool_use_combined(self):
        body = {"content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {}}],
            "stop_reason": "tool_use", "usage": {}}
        out = translator.anthropic_to_openai_response(body, "m")
        msg = out["choices"][0]["message"]
        assert msg["content"] == "Let me check."
        assert msg["tool_calls"][0]["id"] == "t1"


class TestOpenaiToAnthropicResponseTools:
    def test_tool_calls_become_tool_use_blocks(self):
        body = {"choices": [{"index": 0, "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_weather",
                             "arguments": '{"location":"Paris"}'}}]},
            "finish_reason": "tool_calls"}]}
        out = translator.openai_to_anthropic_response(body, "gpt5")
        assert out["content"] == [{
            "type": "tool_use", "id": "call_1", "name": "get_weather",
            "input": {"location": "Paris"}}]
        assert out["stop_reason"] == "tool_use"

    def test_text_and_tool_calls_combined(self):
        body = {"choices": [{"message": {
            "role": "assistant", "content": "Checking.", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "f", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}
        out = translator.openai_to_anthropic_response(body, "m")
        assert out["content"][0] == {"type": "text", "text": "Checking."}
        assert out["content"][1]["type"] == "tool_use"


# ============================================================================
# Streaming translation -- tools (Issue #4)
# ============================================================================


def _sse(event_name: str, obj: dict) -> bytes:
    """Build one Anthropic SSE event (event: + data:) from a dict."""
    return f"event: {event_name}\ndata: {json.dumps(obj)}\n\n".encode("utf-8")


def _oai_sse(obj: dict) -> bytes:
    """Build one OpenAI SSE data event from a dict."""
    return f"data: {json.dumps(obj)}\n\n".encode("utf-8")


# Anthropic stream emitting a single tool_use block whose input JSON
# (`{"location":"Paris"}`) arrives split across two input_json_delta fragments.
_ANTHROPIC_TOOL_SSE = b"".join([
    _sse("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "role": "assistant", "model": "claude", "content": [],
        "stop_reason": None, "usage": {"input_tokens": 5, "output_tokens": 1}}}),
    _sse("content_block_start", {"type": "content_block_start", "index": 0,
        "content_block": {"type": "tool_use", "id": "toolu_1",
                          "name": "get_weather", "input": {}}}),
    _sse("content_block_delta", {"type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '{"location":'}}),
    _sse("content_block_delta", {"type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": '"Paris"}'}}),
    _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
    _sse("message_delta", {"type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {"output_tokens": 3}}),
    _sse("message_stop", {"type": "message_stop"}),
])

# OpenAI stream emitting a single tool_call whose arguments JSON
# (`{"location":"Paris"}`) arrives split across two delta.tool_calls fragments.
_OPENAI_TOOL_SSE = b"".join([
    _oai_sse({"id": "chatcmpl-1", "object": "chat.completion.chunk",
        "created": 1, "model": "gpt", "choices": [{"index": 0,
        "delta": {"role": "assistant", "content": None, "tool_calls": [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": ""}}]},
        "finish_reason": None}]}),
    _oai_sse({"id": "chatcmpl-1", "object": "chat.completion.chunk",
        "created": 1, "model": "gpt", "choices": [{"index": 0,
        "delta": {"tool_calls": [{"index": 0,
            "function": {"arguments": '{"location":'}}]},
        "finish_reason": None}]}),
    _oai_sse({"id": "chatcmpl-1", "object": "chat.completion.chunk",
        "created": 1, "model": "gpt", "choices": [{"index": 0,
        "delta": {"tool_calls": [{"index": 0,
            "function": {"arguments": '"Paris"}'}}]},
        "finish_reason": None}]}),
    _oai_sse({"id": "chatcmpl-1", "object": "chat.completion.chunk",
        "created": 1, "model": "gpt", "choices": [{"index": 0,
        "delta": {}, "finish_reason": "tool_calls"}]}),
    b"data: [DONE]\n\n",
])


class TestAnthropicStreamToOpenaiTools:
    async def test_tool_use_emits_tool_calls_with_accumulated_args(self):
        out = b""
        async for chunk in translator.anthropic_stream_to_openai(
            _FakeOpened(200, [_ANTHROPIC_TOOL_SSE]), "claude"
        ):
            out += chunk
        events = _parse_output(out)
        # Collect tool_calls deltas across chunks.
        args_by_index = {}
        name = None
        tool_id = None
        finish = None
        for _ev, data in events:
            if data == "[DONE]":
                continue
            obj = json.loads(data)
            choice = obj["choices"][0]
            for tc in choice["delta"].get("tool_calls", []):
                idx = tc["index"]
                fn = tc.get("function", {})
                if "name" in fn:
                    name = fn["name"]
                    tool_id = tc.get("id")
                if "arguments" in fn and fn["arguments"]:
                    args_by_index.setdefault(idx, "")
                    args_by_index[idx] += fn["arguments"]
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
        assert name == "get_weather"
        assert tool_id == "toolu_1"
        assert args_by_index[0] == '{"location":"Paris"}'
        assert finish == "tool_calls"


class TestOpenaiStreamToAnthropicTools:
    async def test_tool_calls_emit_tool_use_with_accumulated_partial_json(self):
        out = b""
        async for chunk in translator.openai_stream_to_anthropic(
            _FakeOpened(200, [_OPENAI_TOOL_SSE]), "gpt"
        ):
            out += chunk
        events = _parse_output(out)
        names = [ev for ev, _ in events]
        assert names[0] == "message_start"
        # content_block_start carries the tool_use block with id + name.
        cbs = next(json.loads(d) for ev, d in events if ev == "content_block_start")
        assert cbs["content_block"] == {
            "type": "tool_use", "id": "call_1", "name": "get_weather", "input": {}}
        # input_json_delta fragments concatenate to the full input JSON.
        partial = "".join(
            json.loads(d)["delta"]["partial_json"]
            for ev, d in events if ev == "content_block_delta"
        )
        assert partial == '{"location":"Paris"}'
        assert "content_block_stop" in names
        md = next(json.loads(d) for ev, d in events if ev == "message_delta")
        assert md["delta"]["stop_reason"] == "tool_use"
        assert names[-1] == "message_stop"

    async def test_tool_only_stream_emits_no_text_block(self):
        out = b""
        async for chunk in translator.openai_stream_to_anthropic(
            _FakeOpened(200, [_OPENAI_TOOL_SSE]), "gpt"
        ):
            out += chunk
        events = _parse_output(out)
        # No text_delta should appear (the response is tool-only).
        assert not any(ev == "content_block_delta"
                       and json.loads(d)["delta"].get("type") == "text_delta"
                       for ev, d in events)


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

    def test_tools_round_trip_translated(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, _ANTHROPIC_PROVIDER,
                            {"models": {"claude": {"ant": "claude-sonnet-5"}}})
            client = AuthedClient(app)
            captured = {}

            anth_resp = {
                "id": "msg_2", "type": "message", "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "tool_use", "id": "toolu_1",
                             "name": "get_weather",
                             "input": {"location": "Paris"}}],
                "stop_reason": "tool_use", "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 3},
            }

            async def fake_forward(body, provider, model_name, path):
                captured["body"] = body
                return UpstreamResult(200, json.dumps(anth_resp).encode(),
                                      "application/json", None)

            with patch("llmport.gateway.server.anthropic_handler.forward",
                       new=fake_forward):
                resp = client.post("/openai/v1/chat/completions", json={
                    "model": "claude",
                    "messages": [
                        {"role": "user", "content": "weather in Paris?"},
                        {"role": "assistant", "content": None, "tool_calls": [{
                            "id": "call_1", "type": "function",
                            "function": {"name": "get_weather",
                                         "arguments": '{"location":"Paris"}'}}]},
                        {"role": "tool", "tool_call_id": "call_1",
                         "content": "sunny"},
                    ],
                    "tools": [{"type": "function", "function": {
                        "name": "get_weather", "description": "Get weather",
                        "parameters": {"type": "object",
                                       "properties": {"location": {"type": "string"}}}}}],
                })
            assert resp.status_code == 200
            # Upstream received Anthropic-format tools + tool_result.
            up = captured["body"]
            assert up["tools"][0]["name"] == "get_weather"
            assert up["tools"][0]["input_schema"]["properties"] == {
                "location": {"type": "string"}}
            assert up["messages"][1]["content"][0] == {
                "type": "tool_use", "id": "call_1", "name": "get_weather",
                "input": {"location": "Paris"}}
            assert up["messages"][2] == {
                "role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "call_1",
                     "content": "sunny"}]}
            # Client received OpenAI-format tool_calls.
            msg = resp.json()["choices"][0]["message"]
            assert msg["tool_calls"] == [{
                "id": "toolu_1", "type": "function",
                "function": {"name": "get_weather",
                             "arguments": '{"location": "Paris"}'}}]
            assert resp.json()["choices"][0]["finish_reason"] == "tool_calls"

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
