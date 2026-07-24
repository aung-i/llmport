"""Unit tests for pure token-tracking helpers in ``server.py``.

Covers ``_safe_token_val``, ``_extract_tokens``, and
``_parse_usage_from_sse`` — all pure functions with no side effects.
"""

from llmport.gateway.server import _safe_token_val, _extract_tokens, _parse_usage_from_sse


# ============================================================================
# _safe_token_val
# ============================================================================

class TestSafeTokenVal:
    """Cover every branch of _safe_token_val()."""

    def test_positive_int_passthrough(self):
        """A non-negative int is returned as-is."""
        assert _safe_token_val(42) == 42
        assert _safe_token_val(0) == 0
        assert _safe_token_val(1) == 1

    def test_negative_int_clamped_to_zero(self):
        """A negative int falls through and returns 0."""
        assert _safe_token_val(-5) == 0
        assert _safe_token_val(-1) == 0

    def test_positive_float_truncated(self):
        """A positive float is truncated, then clamped."""
        assert _safe_token_val(3.14) == 3
        assert _safe_token_val(0.99) == 0

    def test_negative_float_clamped(self):
        """A negative float returns 0."""
        assert _safe_token_val(-3.14) == 0
        assert _safe_token_val(-0.5) == 0

    def test_numeric_string_parsed(self):
        """A string containing a number is parsed."""
        assert _safe_token_val("42") == 42
        assert _safe_token_val("0") == 0

    def test_float_string_parsed_and_truncated(self):
        """A string containing a float is parsed, truncated, then clamped."""
        assert _safe_token_val("3.14") == 3
        assert _safe_token_val("0.99") == 0

    def test_negative_float_string_clamped(self):
        """A negative float string returns 0."""
        assert _safe_token_val("-3.14") == 0
        assert _safe_token_val("-0.5") == 0

    def test_invalid_string_returns_zero(self):
        """A non-numeric string raises ValueError which is caught, returning 0."""
        assert _safe_token_val("abc") == 0
        assert _safe_token_val("") == 0

    def test_none_returns_zero(self):
        """None is not int and not float/str, so returns 0."""
        assert _safe_token_val(None) == 0

    def test_boolean_treated_as_int(self):
        """True/False are isinstance int in Python and handled accordingly."""
        assert _safe_token_val(True) == 1
        assert _safe_token_val(False) == 0

    def test_dict_list_returns_zero(self):
        """Non-primitive types return 0."""
        assert _safe_token_val({}) == 0
        assert _safe_token_val([]) == 0


# ============================================================================
# _extract_tokens
# ============================================================================

class TestExtractTokens:
    """Cover OpenAI, Anthropic, missing, and malformed usage scenarios."""

    def test_openai_total_tokens(self):
        """OpenAI-style usage.total_tokens is returned."""
        result = {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        assert _extract_tokens(result) == 30

    def test_anthropic_input_output_sum(self):
        """Anthropic-style usage sums input_tokens + output_tokens."""
        result = {"usage": {"input_tokens": 10, "output_tokens": 20}}
        assert _extract_tokens(result) == 30

    def test_anthropic_only_input_tokens(self):
        """Anthropic with only input_tokens sums correctly."""
        result = {"usage": {"input_tokens": 15}}
        assert _extract_tokens(result) == 15

    def test_anthropic_only_output_tokens(self):
        """Anthropic with only output_tokens sums correctly."""
        result = {"usage": {"output_tokens": 25}}
        assert _extract_tokens(result) == 25

    def test_openai_takes_precedence(self):
        """When both formats are present, total_tokens wins."""
        result = {"usage": {"total_tokens": 7, "input_tokens": 10, "output_tokens": 20}}
        assert _extract_tokens(result) == 7

    def test_no_usage_key(self):
        """Missing usage key returns 0."""
        assert _extract_tokens({}) == 0

    def test_none_usage(self):
        """Null usage is treated as empty dict via None or {}."""
        assert _extract_tokens({"usage": None}) == 0

    def test_empty_usage(self):
        """Empty usage dict returns 0."""
        assert _extract_tokens({"usage": {}}) == 0

    def test_negative_total_tokens_sanitised(self):
        """Negative total_tokens is clamped to 0 via _safe_token_val."""
        result = {"usage": {"total_tokens": -5}}
        assert _extract_tokens(result) == 0

    def test_negative_anthropic_tokens_sanitised(self):
        """Negative Anthropic token values are clamped to 0 before summing."""
        result = {"usage": {"input_tokens": -5, "output_tokens": 10}}
        assert _extract_tokens(result) == 10  # max(0,-5)=0 + 10 = 10

    def test_all_negative_anthropic_returns_zero(self):
        """All negative Anthropic values sum to 0."""
        result = {"usage": {"input_tokens": -5, "output_tokens": -3}}
        assert _extract_tokens(result) == 0

    def test_non_int_total_tokens(self):
        """Non-integer total_tokens (e.g. string) is sanitised to 0."""
        result = {"usage": {"total_tokens": "abc"}}
        assert _extract_tokens(result) == 0

    def test_non_int_anthropic_tokens(self):
        """Non-integer Anthropic token values are sanitised to 0."""
        result = {"usage": {"input_tokens": "xyz", "output_tokens": 5}}
        assert _extract_tokens(result) == 5

    def test_string_float_total_tokens(self):
        """String-encoded float total_tokens is parsed correctly."""
        result = {"usage": {"total_tokens": "3.14"}}
        assert _extract_tokens(result) == 3


# ============================================================================
# _parse_usage_from_sse
# ============================================================================

class TestParseUsageFromSSE:
    """Cover OpenAI SSE chunks, Anthropic message_start, DONE, edge cases."""

    def test_openai_sse_with_usage(self):
        """OpenAI SSE chunk with usage.total_tokens returns the value."""
        chunk = b'data: {"id":"x","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n'
        assert _parse_usage_from_sse(chunk) == 7

    def test_openai_sse_usage_zero_returns_none(self):
        """OpenAI SSE with total_tokens=0 returns None (0 is falsy)."""
        chunk = b'data: {"id":"x","choices":[],"usage":{"total_tokens":0}}\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_anthropic_message_start(self):
        """Anthropic message_start chunk with nested usage."""
        chunk = b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"output_tokens":20}}}\n\n'
        assert _parse_usage_from_sse(chunk) == 30

    def test_anthropic_message_start_no_usage(self):
        """Anthropic message_start chunk without usage returns None."""
        chunk = b'data: {"type":"message_start","message":{"id":"msg_1"}}\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_done_marker(self):
        """[DONE] marker is skipped and returns None."""
        chunk = b"data: [DONE]\n\n"
        assert _parse_usage_from_sse(chunk) is None

    def test_empty_chunk(self):
        """Empty bytes return None."""
        assert _parse_usage_from_sse(b"") is None
        assert _parse_usage_from_sse(b"\n\n") is None  # no data: prefix

    def test_no_data_prefix(self):
        """Lines without data: prefix are skipped."""
        chunk = b': ping\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_line_without_data_prefix(self):
        """Non-data lines are ignored."""
        chunk = b'data: {"id":"x","usage":{"total_tokens":5}}\nignore: me\n\n'
        assert _parse_usage_from_sse(chunk) == 5

    def test_null_usage_in_chunk(self):
        """Chunk with explicit null usage returns None."""
        chunk = b'data: {"id":"x","choices":[],"usage":null}\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_empty_usage_dict(self):
        """Chunk with empty usage dict returns None."""
        chunk = b'data: {"id":"x","usage":{}}\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_malformed_json(self):
        """Malformed JSON is caught by the exception handler and returns None."""
        chunk = b'data: {invalid json}\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_negative_usage_returns_none(self):
        """Negative total_tokens clamped to 0, and 0 is falsy so returns None."""
        chunk = b'data: {"id":"x","usage":{"total_tokens":-5}}\n\n'
        assert _parse_usage_from_sse(chunk) is None

    def test_negative_anthropic_usage(self):
        """Negative Anthropic tokens are clamped and may return 0 or None."""
        chunk = b'data: {"type":"message_start","message":{"usage":{"input_tokens":-5,"output_tokens":-3}}}\n\n'
        # Both clamp to 0, inp or out is 0 -> falsy -> None
        assert _parse_usage_from_sse(chunk) is None

    def test_openai_with_both_formats_prefers_total(self):
        """When both total_tokens and input/output_tokens exist, total_tokens wins."""
        chunk = b'data: {"id":"x","usage":{"total_tokens":7,"input_tokens":3,"output_tokens":4}}\n\n'
        assert _parse_usage_from_sse(chunk) == 7

    def test_multiple_data_lines(self):
        """Multiple event types in one chunk; only the one with usage matters."""
        chunk = (
            b'data: {"type":"ping"}\n'
            b'data: {"id":"x","usage":{"total_tokens":5}}\n\n'
        )
        assert _parse_usage_from_sse(chunk) == 5

    def test_non_dict_message_skipped(self):
        """Anthropic 'message' field that is not a dict is safely skipped."""
        chunk = b'data: {"type":"message_start","message":"just a string"}\n\n'
        assert _parse_usage_from_sse(chunk) is None
