"""Bidirectional OpenAI <-> Anthropic format translation.

Used when a client speaks one protocol but the resolved provider speaks the
other (e.g. an OpenAI SDK pointed at an Anthropic provider, or vice versa).
Translates the request body, and -- for both non-streaming and streaming
(SSE) responses -- the reply back into the client's protocol.

Translation lives here (pure functions), not in the handlers, so
``handler_base``'s transparent-forward contract stays intact and the server
route layer just decides *when* to translate and orchestrates the call.

Scope (this pass): text chat -- messages, system prompt, and the common
sampling params (max_tokens, temperature, top_p, stop). Tool calling /
function calling and multimodal (image) content are NOT translated; a
cross-format request carrying them has those fields dropped (see issue #2).
Error responses (upstream >=400) are passed through verbatim in the upstream
format by the server, not translated here.
"""

import codecs
import json
import time
import uuid

# ── finish_reason (OpenAI) <-> stop_reason (Anthropic) ──────────────────────

_OPENAI_TO_ANTHROPIC_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}
_ANTHROPIC_TO_OPENAI_STOP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}


# ============================================================================
# Request translation
# ============================================================================


def openai_to_anthropic_request(body: dict) -> dict:
    """Translate an OpenAI chat-completions request to Anthropic messages.

    OpenAI ``system`` role messages become Anthropic's top-level ``system``
    field (joined with blank lines). ``max_tokens`` is required by Anthropic,
    so it defaults to 1024 when the client omitted it. Tools / image parts are
    dropped (out of scope this pass).
    """
    out: dict = {}
    system_parts: list[str] = []
    anth_messages: list[dict] = []
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            text = _openai_content_to_text(content)
            if text:
                system_parts.append(text)
            continue
        anth_role = "assistant" if role == "assistant" else "user"
        anth_messages.append(
            {"role": anth_role, "content": _openai_content_to_anthropic(content)}
        )
    if system_parts:
        out["system"] = "\n\n".join(system_parts)
    out["messages"] = anth_messages
    out["max_tokens"] = body.get("max_tokens") or 1024
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    stop = body.get("stop")
    if stop:
        out["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if body.get("stream"):
        out["stream"] = True
    return out


def anthropic_to_openai_request(body: dict) -> dict:
    """Translate an Anthropic messages request to OpenAI chat-completions.

    Anthropic's top-level ``system`` becomes an OpenAI ``system`` message.
    Content blocks are flattened to a text string (text blocks concatenated;
    non-text blocks dropped). Tools are dropped (out of scope this pass).
    """
    out: dict = {}
    messages: list[dict] = []
    system = body.get("system")
    if system:
        sys_text = _anthropic_content_to_text(system)
        if sys_text:
            messages.append({"role": "system", "content": sys_text})
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "user")
        oai_role = "assistant" if role == "assistant" else "user"
        messages.append(
            {"role": oai_role, "content": _anthropic_content_to_text(m.get("content"))}
        )
    out["messages"] = messages
    if body.get("max_tokens") is not None:
        out["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    stop = body.get("stop_sequences")
    if stop:
        out["stop"] = stop[0] if len(stop) == 1 else stop
    if body.get("stream"):
        out["stream"] = True
    return out


def _openai_content_to_anthropic(content):
    """OpenAI message content -> Anthropic content (string or text-block list)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                blocks.append({"type": "text", "text": part.get("text", "")})
            # image_url / other part types dropped (multimodal out of scope)
        return blocks or ""
    return str(content)


def _openai_content_to_text(content) -> str:
    """Flatten OpenAI content (string or text-part list) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content)


def _anthropic_content_to_text(content) -> str:
    """Flatten Anthropic content (string or block list) to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


# ============================================================================
# Response translation (non-streaming)
# ============================================================================


def anthropic_to_openai_response(body: dict, model: str) -> dict:
    """Translate an Anthropic messages response to an OpenAI chat completion."""
    text = _anthropic_content_to_text(body.get("content"))
    finish = _ANTHROPIC_TO_OPENAI_STOP.get(body.get("stop_reason"), "stop")
    usage = body.get("usage") or {}
    prompt = usage.get("input_tokens", 0) or 0
    comp = usage.get("output_tokens", 0) or 0
    return {
        "id": body.get("id") or "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": comp,
            "total_tokens": prompt + comp,
        },
    }


def openai_to_anthropic_response(body: dict, model: str) -> dict:
    """Translate an OpenAI chat completion to an Anthropic messages response."""
    choices = body.get("choices") or []
    choice = choices[0] if choices else {}
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    finish = _OPENAI_TO_ANTHROPIC_STOP.get(choice.get("finish_reason"), "end_turn")
    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}] if text else [],
        "stop_reason": finish,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0) or 0,
            "output_tokens": usage.get("completion_tokens", 0) or 0,
        },
    }


# ============================================================================
# Streaming translation (SSE)
# ============================================================================


def _parse_sse_block(raw: str) -> tuple[str | None, str | None]:
    """Parse one SSE block (between blank lines) into (event, data).

    ``data`` is None when the block carries no data line (e.g. a keep-alive
    comment), so callers can skip it. Multiple ``data:`` lines are joined with
    ``\n`` per the SSE spec.
    """
    event = None
    data_lines: list[str] = []
    for line in raw.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    data = "\n".join(data_lines) if data_lines else None
    return event, data


async def _iter_sse_events(opened):
    """Yield ``(event, data)`` parsed from the upstream SSE byte stream.

    Uses an incremental UTF-8 decoder so a multi-byte character split across
    chunk boundaries is not corrupted. Events are delimited by a blank line
    (``\\n\\n``); a trailing partial block is flushed when the stream ends.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""
    async for chunk in opened.aiter_bytes():
        buffer += decoder.decode(chunk)
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            event, data = _parse_sse_block(raw)
            if data is not None:
                yield event, data
    buffer += decoder.decode(b"", final=True)
    if buffer.strip():
        event, data = _parse_sse_block(buffer)
        if data is not None:
            yield event, data


def _oai_chunk(chat_id: str, created: int, model: str, delta: dict,
               finish_reason) -> bytes:
    """Encode one OpenAI chat.completion.chunk SSE event."""
    payload = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _anth_event(event_name: str, obj: dict) -> bytes:
    """Encode one Anthropic SSE event (``event:`` + ``data:``)."""
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


async def anthropic_stream_to_openai(opened, model: str):
    """Consume an Anthropic messages SSE stream; yield OpenAI chunk SSE bytes.

    message_start -> role chunk; content_block_delta(text_delta) -> content
    chunk; message_delta(stop_reason) -> finish_reason chunk; message_stop ->
    ``[DONE]``. A stream that ends without message_stop still closes cleanly.
    """
    chat_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    finished = False
    async for event, data in _iter_sse_events(opened):
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            continue
        etype = obj.get("type") or event
        if etype == "message_start":
            yield _oai_chunk(chat_id, created, model,
                             {"role": "assistant", "content": ""}, None)
        elif etype == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield _oai_chunk(chat_id, created, model,
                                 {"content": delta["text"]}, None)
        elif etype == "message_delta":
            delta = obj.get("delta") or {}
            stop = delta.get("stop_reason")
            if stop:
                finish = _ANTHROPIC_TO_OPENAI_STOP.get(stop, "stop")
                yield _oai_chunk(chat_id, created, model, {}, finish)
                finished = True
        elif etype == "message_stop":
            break
    if not finished:
        yield _oai_chunk(chat_id, created, model, {}, "stop")
    yield b"data: [DONE]\n\n"


async def openai_stream_to_anthropic(opened, model: str):
    """Consume an OpenAI chat-completion SSE stream; yield Anthropic SSE bytes.

    Emits message_start + content_block_start up front, a text_delta per
    content chunk, then content_block_stop + message_delta + message_stop at
    finish_reason (or a clean end_turn close if the stream ends without one).
    """
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    yield _anth_event("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })
    yield _anth_event("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    finished = False
    async for event, data in _iter_sse_events(opened):
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            continue
        choices = obj.get("choices") or []
        choice = choices[0] if choices else {}
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            yield _anth_event("content_block_delta", {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": content},
            })
        fr = choice.get("finish_reason")
        if fr and not finished:
            stop = _OPENAI_TO_ANTHROPIC_STOP.get(fr, "end_turn")
            yield _anth_event("content_block_stop",
                              {"type": "content_block_stop", "index": 0})
            yield _anth_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 0},
            })
            yield _anth_event("message_stop", {"type": "message_stop"})
            finished = True
    if not finished:
        yield _anth_event("content_block_stop",
                          {"type": "content_block_stop", "index": 0})
        yield _anth_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 0},
        })
        yield _anth_event("message_stop", {"type": "message_stop"})
