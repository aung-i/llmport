"""Tests for SSRF protection (Issue 1).

Requires:
- validate_public_url() that rejects private/reserved IPs
- allow_redirects=False on all httpx calls in handler_base
"""

import inspect


class TestValidatePublicUrl:

    def test_function_exists(self):
        from llmport.gateway.handler_base import validate_public_url
        assert callable(validate_public_url)

    def test_rejects_private_10_x(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("http://10.0.0.1:8080") is False

    def test_rejects_private_172_16(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("http://172.16.0.1/api") is False

    def test_rejects_private_172_31(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("http://172.31.255.255") is False

    def test_rejects_private_192_168(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("http://192.168.1.1") is False

    def test_rejects_loopback(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("http://127.0.0.1:11434") is False
        assert validate_public_url("http://localhost:11434") is False

    def test_rejects_zero_ip(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("http://0.0.0.0") is False

    def test_accepts_public_domain(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("https://api.openai.com/v1/chat") is True

    def test_accepts_public_ip(self):
        from llmport.gateway.handler_base import validate_public_url
        assert validate_public_url("https://8.8.8.8") is True


class TestHandlerBaseAllowRedirects:

    def test_forward_disallows_redirects(self):
        from llmport.gateway.handler_base import forward
        source = inspect.getsource(forward)
        assert "allow_redirects=False" in source, (
            "forward() must use allow_redirects=False for SSRF protection"
        )

    def test_stream_disallows_redirects(self):
        from llmport.gateway.handler_base import stream
        source = inspect.getsource(stream)
        assert "allow_redirects=False" in source, (
            "stream() must use allow_redirects=False for SSRF protection"
        )
