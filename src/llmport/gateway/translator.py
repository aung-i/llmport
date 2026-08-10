"""Bidirectional OpenAI <-> Anthropic format translation.

Used when a client speaks one protocol but the resolved provider speaks the
other (e.g. an OpenAI SDK pointed at an Anthropic provider, or vice versa).
Translates the request body, and -- for both non-streaming and streaming
(SSE) responses -- the reply back into the client's protocol.

Translation lives here (pure functions), not in the handlers, so
``handler_base``'s transparent-forward contract stays intact and the server
route layer just decides *when* to translate and orchestrates the call.

Scope: text chat, tool calling / function calling, and multimodal (image)
content -- request, non-streaming response, and streaming response, in both
directions. Field mappings follow the public OpenAI Chat Completions and
Anthropic Messages API specs. Error responses (upstream >=400) are passed
through verbatim in the upstream format by the server, not translated here.
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

# Anthropic accepts these image media types.
_ANTHROPIC_IMAGE_MEDIA_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
}


# ============================================================================
# Request translation
# ============================================================================


def openai_to_anthropic_request(body: dict) -> dict:
    """Translate an OpenAI chat-completions request to Anthropic messages.

    OpenAI ``system`` role messages become Anthropic's top-level ``system``
    field (joined with blank lines). ``max_tokens`` is required by Anthropic,
    so it defaults to 1024 when the client omitted it. Tools, tool_choice,
    assistant ``tool_calls``, ``tool`` role results, and ``image_url`` parts
    are mapped to their Anthropic equivalents.
    """
    out: dict = {}
    system_parts: list[str] = []
    anth_messages: list[dict] = []
    pending_tool_results: list[dict] = []

    def flush_tools():
        if pending_tool_results:
            anth_messages.append(
                {"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            flush_tools()
            text = _openai_content_to_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            # OpenAI tool result -> Anthropic user tool_result block. Consecutive
            # tool messages are grouped into one user message (Anthropic wants
            # parallel tool results together, and roles must alternate).
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id"),
                "content": _openai_content_to_text(content),
            })
            continue
        flush_tools()
        if role == "assistant" and m.get("tool_calls"):
            blocks = []
            text = _openai_content_to_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or ("toolu_" + uuid.uuid4().hex[:24]),
                    "name": fn.get("name", ""),
                    "input": _parse_json_or_empty(fn.get("arguments")),
                })
            anth_messages.append({"role": "assistant", "content": blocks})
            continue
        anth_role = "assistant" if role == "assistant" else "user"
        anth_messages.append(
            {"role": anth_role, "content": _openai_content_to_anthropic(content)}
        )
    flush_tools()
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
    tools = _openai_tools_to_anthropic(body.get("tools"))
    if tools:
        out["tools"] = tools
    tc = _openai_tool_choice_to_anthropic(body.get("tool_choice"))
    if tc is not None:
        out["tool_choice"] = tc
    return out


def anthropic_to_openai_request(body: dict) -> dict:
    """Translate an Anthropic messages request to OpenAI chat-completions.

    Anthropic's top-level ``system`` becomes an OpenAI ``system`` message.
    Content blocks are mapped to OpenAI parts: text blocks to text, image
    blocks to ``image_url`` (collapsed to a plain string when purely textual).
    Tool definitions, tool_choice, assistant ``tool_use`` blocks, and user
    ``tool_result`` blocks are mapped to their OpenAI equivalents.
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
        content = m.get("content")
        if role == "assistant":
            messages.append(_anthropic_assistant_to_openai(content))
            continue
        # user role: tool_result blocks -> tool messages; rest -> user content
        if isinstance(content, list):
            tool_results = [b for b in content
                            if isinstance(b, dict) and b.get("type") == "tool_result"]
            others = [b for b in content
                      if isinstance(b, dict) and b.get("type") != "tool_result"]
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id"),
                    "content": _anthropic_content_to_text(tr.get("content")),
                })
            if others:
                messages.append(
                    {"role": "user", "content": _anthropic_content_to_openai(others)})
        else:
            messages.append(
                {"role": "user", "content": _anthropic_content_to_openai(content)})
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
    tools = _anthropic_tools_to_openai(body.get("tools"))
    if tools:
        out["tools"] = tools
    tc = _anthropic_tool_choice_to_openai(body.get("tool_choice"))
    if tc is not None:
        out["tool_choice"] = tc
    return out


# ── request helpers ──────────────────────────────────────────────────────────


def _openai_content_to_anthropic(content):
    """OpenAI message content -> Anthropic content (string or block list).

    Text parts become text blocks; ``image_url`` parts become Anthropic image
    blocks (data URLs to base64 sources, http(s) URLs to url sources -- no
    fetching, both APIs support a url source). Pure-string content is returned
    as a string (Anthropic accepts that for text-only messages).
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                blocks.append({"type": "text", "text": part.get("text", "")})
            elif ptype == "image_url":
                img = _openai_image_url_to_anthropic(part.get("image_url"))
                if img:
                    blocks.append(img)
        return blocks or ""
    return str(content)


def _openai_image_url_to_anthropic(image_url):
    """OpenAI ``image_url`` object -> Anthropic image block, or None."""
    if not isinstance(image_url, dict):
        return None
    url = image_url.get("url")
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:") and ";base64," in url:
        header, data = url.split(";base64,", 1)
        media_type = header[len("data:"):]
        if media_type not in _ANTHROPIC_IMAGE_MEDIA_TYPES:
            return None
        return {"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": data}}
    if url.startswith("http://") or url.startswith("https://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _anthropic_content_to_openai(content):
    """Anthropic content (string or block list) -> OpenAI content.

    Returns a plain string when the content is purely textual (OpenAI accepts a
    string for text), or a list of text/image_url parts when images are present.
    tool_use/tool_result blocks are handled by the caller, not here.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif btype == "image":
                img = _anthropic_image_to_openai(b.get("source"))
                if img:
                    parts.append(img)
        if not parts:
            return ""
        if all(p.get("type") == "text" for p in parts):
            return "".join(p.get("text", "") for p in parts)
        return parts
    return str(content)


def _anthropic_image_to_openai(source):
    """Anthropic image source -> OpenAI image_url part, or None."""
    if not isinstance(source, dict):
        return None
    stype = source.get("type")
    if stype == "base64":
        media_type = source.get("media_type", "")
        data = source.get("data", "")
        return {"type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"}}
    if stype == "url":
        url = source.get("url")
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
    return None


def _anthropic_assistant_to_openai(content) -> dict:
    """Anthropic assistant content -> OpenAI assistant message.

    text blocks -> message content; tool_use blocks -> ``tool_calls`` (input
    object serialized to the JSON ``arguments`` string per OpenAI's spec).
    """
    if content is None:
        return {"role": "assistant", "content": ""}
    if isinstance(content, str):
        return {"role": "assistant", "content": content}
    text_parts: list[str] = []
    tool_calls = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id") or ("call_" + uuid.uuid4().hex[:24]),
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": json.dumps(b.get("input") or {},
                                            ensure_ascii=False),
                },
            })
    text = "".join(text_parts)
    if tool_calls:
        return {"role": "assistant",
                "content": text if text else None, "tool_calls": tool_calls}
    return {"role": "assistant", "content": text}


def _openai_tools_to_anthropic(tools):
    """OpenAI tools -> Anthropic tools, or None when empty/absent."""
    if not isinstance(tools, list):
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            fn = t["function"]
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters")
                or {"type": "object", "properties": {}},
            })
    return out or None


def _anthropic_tools_to_openai(tools):
    """Anthropic tools -> OpenAI tools, or None when empty/absent."""
    if not isinstance(tools, list):
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema")
                or {"type": "object", "properties": {}},
            },
        })
    return out or None


def _openai_tool_choice_to_anthropic(tc):
    """OpenAI tool_choice -> Anthropic tool_choice, or None.

    "auto"->auto, "none"->none, "required"->any, {function:{name}}->{tool,name}.
    """
    if tc is None:
        return None
    if isinstance(tc, str):
        if tc == "auto":
            return {"type": "auto"}
        if tc == "none":
            return {"type": "none"}
        if tc == "required":
            return {"type": "any"}
        return None
    if isinstance(tc, dict) and tc.get("type") == "function":
        name = (tc.get("function") or {}).get("name")
        if name:
            return {"type": "tool", "name": name}
    return None


def _anthropic_tool_choice_to_openai(tc):
    """Anthropic tool_choice -> OpenAI tool_choice, or None.

    auto->"auto", none->"none", any->"required", {tool,name}->{function,{name}}.
    """
    if tc is None:
        return None
    if isinstance(tc, str):
        return tc if tc in ("auto", "none") else None
    if isinstance(tc, dict):
        t = tc.get("type")
        if t == "auto":
            return "auto"
        if t == "none":
            return "none"
        if t == "any":
            return "required"
        if t == "tool":
            name = tc.get("name")
            if name:
                return {"type": "function", "function": {"name": name}}
    return None


def _parse_json_or_empty(s):
    """Parse a JSON string to an object; tolerate None/empty/dict input."""
    if s is None or s == "":
        return {}
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {}


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
    """Translate an Anthropic messages response to an OpenAI chat completion.

    text blocks -> message content; tool_use blocks -> ``tool_calls`` (input
    object serialized to the JSON ``arguments`` string). stop_reason maps to
    finish_reason (tool_use -> tool_calls).
    """
    text_parts: list[str] = []
    tool_calls = []
    for b in body.get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id") or ("call_" + uuid.uuid4().hex[:24]),
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": json.dumps(b.get("input") or {},
                                            ensure_ascii=False),
                },
            })
    text = "".join(text_parts)
    finish = _ANTHROPIC_TO_OPENAI_STOP.get(body.get("stop_reason"), "stop")
    usage = body.get("usage") or {}
    prompt = usage.get("input_tokens", 0) or 0
    comp = usage.get("output_tokens", 0) or 0
    message = {"role": "assistant", "content": text if text else None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": body.get("id") or "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": comp,
            "total_tokens": prompt + comp,
        },
    }


def openai_to_anthropic_response(body: dict, model: str) -> dict:
    """Translate an OpenAI chat completion to an Anthropic messages response.

    message content -> text block; ``tool_calls`` -> tool_use blocks (JSON
    ``arguments`` string parsed to the input object). finish_reason maps to
    stop_reason (tool_calls -> tool_use).
    """
    choices = body.get("choices") or []
    choice = choices[0] if choices else {}
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    finish = _OPENAI_TO_ANTHROPIC_STOP.get(choice.get("finish_reason"), "end_turn")
    blocks = []
    if text:
        blocks.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or ("toolu_" + uuid.uuid4().hex[:24]),
            "name": fn.get("name", ""),
            "input": _parse_json_or_empty(fn.get("arguments")),
        })
    usage = body.get("usage") or {}
    return {
        "id": body.get("id") or "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
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
    chunk; content_block_start(tool_use) -> a tool_calls chunk carrying id +
    name (and a fresh OpenAI tool_call index); content_block_delta(
    input_json_delta) -> a tool_calls chunk carrying the arguments fragment
    (the partial_json string is the same JSON fragment OpenAI streams);
    message_delta(stop_reason) -> finish_reason chunk; message_stop ->
    ``[DONE]``. A stream that ends without message_stop still closes cleanly.
    """
    chat_id = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    finished = False
    tool_index_map: dict[int, int] = {}  # anthropic block index -> oai tool idx
    next_tc = 0
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
        elif etype == "content_block_start":
            cb = obj.get("content_block") or {}
            if cb.get("type") == "tool_use":
                idx = obj.get("index", 0)
                tc_index = next_tc
                tool_index_map[idx] = tc_index
                next_tc += 1
                yield _oai_chunk(chat_id, created, model, {"tool_calls": [{
                    "index": tc_index,
                    "id": cb.get("id") or ("call_" + uuid.uuid4().hex[:24]),
                    "type": "function",
                    "function": {"name": cb.get("name", ""), "arguments": ""},
                }]}, None)
        elif etype == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield _oai_chunk(chat_id, created, model,
                                 {"content": delta["text"]}, None)
            elif delta.get("type") == "input_json_delta" and delta.get("partial_json"):
                idx = obj.get("index", 0)
                tc_index = tool_index_map.get(idx, 0)
                yield _oai_chunk(chat_id, created, model, {"tool_calls": [{
                    "index": tc_index,
                    "function": {"arguments": delta["partial_json"]},
                }]}, None)
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

    Emits message_start up front, then content blocks lazily: a text block on
    the first content delta, and a tool_use block on the first sighting of each
    OpenAI tool_call index (carrying id + name). Subsequent tool_call argument
    fragments become input_json_delta events (the JSON fragment is identical to
    what Anthropic streams). At finish_reason every open block is closed, then
    message_delta + message_stop follow. A stream that ends without a
    finish_reason still closes cleanly with end_turn.
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
    text_block_index = None
    next_index = 0
    tool_call_to_block: dict[int, int] = {}  # oai tool_call index -> anth block
    open_blocks: list[int] = []
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
            if text_block_index is None:
                text_block_index = next_index
                open_blocks.append(text_block_index)
                next_index += 1
                yield _anth_event("content_block_start", {
                    "type": "content_block_start", "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                })
            yield _anth_event("content_block_delta", {
                "type": "content_block_delta", "index": text_block_index,
                "delta": {"type": "text_delta", "text": content},
            })
        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tc_idx = tc.get("index", 0)
            fn = tc.get("function") or {}
            if tc_idx not in tool_call_to_block:
                block_idx = next_index
                tool_call_to_block[tc_idx] = block_idx
                open_blocks.append(block_idx)
                next_index += 1
                yield _anth_event("content_block_start", {
                    "type": "content_block_start", "index": block_idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": tc.get("id") or ("toolu_" + uuid.uuid4().hex[:24]),
                        "name": fn.get("name", ""), "input": {},
                    },
                })
            block_idx = tool_call_to_block[tc_idx]
            args = fn.get("arguments")
            if args:
                yield _anth_event("content_block_delta", {
                    "type": "content_block_delta", "index": block_idx,
                    "delta": {"type": "input_json_delta", "partial_json": args},
                })
        fr = choice.get("finish_reason")
        if fr and not finished:
            stop = _OPENAI_TO_ANTHROPIC_STOP.get(fr, "end_turn")
            for bi in open_blocks:
                yield _anth_event("content_block_stop",
                                  {"type": "content_block_stop", "index": bi})
            yield _anth_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 0},
            })
            yield _anth_event("message_stop", {"type": "message_stop"})
            finished = True
    if not finished:
        for bi in open_blocks:
            yield _anth_event("content_block_stop",
                              {"type": "content_block_stop", "index": bi})
        yield _anth_event("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 0},
        })
        yield _anth_event("message_stop", {"type": "message_stop"})
