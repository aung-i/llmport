"""Tests for SSRF protection (Issue 1).

Requires:
- validate_public_url() that rejects private/reserved IPs
- allow_redirects=False on all httpx calls in handler_base
"""

import inspect
from unittest.mock import patch


class TestValidatePublicUrl:

    def test_function_exists(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert callable(validate_public_url)

    def test_rejects_private_10_x(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://10.0.0.1:8080") is False

    def test_rejects_private_172_16(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://172.16.0.1/api") is False

    def test_rejects_private_172_31(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://172.31.255.255") is False

    def test_rejects_private_192_168(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://192.168.1.1") is False

    def test_rejects_loopback(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://127.0.0.1:11434") is False
        assert validate_public_url("http://localhost:11434") is False

    def test_rejects_zero_ip(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://0.0.0.0") is False

    def test_rejects_multicast(self):
        """validate_public_url must reject multicast addresses (224.0.0.0/4)."""
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://224.0.0.1") is False
        assert validate_public_url("http://224.0.0.255") is False

    def test_rejects_reserved(self):
        """validate_public_url must reject reserved addresses (240.0.0.0/4)."""
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("http://240.0.0.1") is False
        assert validate_public_url("http://255.255.255.255") is False

    def test_rejects_no_hostname(self):
        """A URL with no hostname (e.g. http:///path) must be rejected."""
        from llmport.gateway.ip_utils import validate_public_url
        # http:///path has netloc='' -> hostname=None -> returns False
        assert validate_public_url("http:///path") is False

    def test_accepts_public_domain(self):
        from llmport.gateway.ip_utils import validate_public_url
        # Mock DNS so the test is deterministic (live DNS can return sandbox
        # addresses that flake the result).
        with patch(
            "llmport.gateway.ip_utils._resolve_hostname",
            return_value=["199.59.148.201"],
        ):
            assert validate_public_url("https://api.openai.com/v1/chat") is True

    def test_accepts_public_ip(self):
        from llmport.gateway.ip_utils import validate_public_url
        assert validate_public_url("https://8.8.8.8") is True

    def test_rejects_non_ip_in_resolved(self):
        """When _resolve_hostname returns something that is not a valid IP
        address, ip_address() raises ValueError and validate_public_url
        must return False."""
        from llmport.gateway.ip_utils import validate_public_url
        with patch(
            "llmport.gateway.ip_utils._resolve_hostname",
            return_value=["not-an-ip"],
        ):
            assert validate_public_url("http://example.com") is False

    def test_urlparse_exception_returns_false(self):
        """If urlparse raises an exception, validate_public_url must
        catch it and return False."""
        from llmport.gateway.ip_utils import validate_public_url
        with patch(
            "llmport.gateway.ip_utils.urlparse",
            side_effect=ValueError("malformed URL"),
        ):
            assert validate_public_url("http://example.com") is False


class TestResolveHostname:

    def test_resolve_hostname_multicast_ip(self):
        """_resolve_hostname should return a multicast IP as-is without
        attempting DNS resolution."""
        from llmport.gateway.ip_utils import _resolve_hostname
        assert _resolve_hostname("224.0.0.1") == ["224.0.0.1"]

    def test_resolve_hostname_reserved_ip(self):
        """_resolve_hostname should return a reserved IP as-is without
        attempting DNS resolution."""
        from llmport.gateway.ip_utils import _resolve_hostname
        assert _resolve_hostname("240.0.0.1") == ["240.0.0.1"]


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


class TestResolveHostname:
    """_resolve_hostname() - deterministic, no live DNS."""

    def test_resolves_via_getaddrinfo(self):
        from llmport.gateway import ip_utils
        fake = [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("1.1.1.1", 0)),
            (None, None, None, None, ("8.8.8.8", 0)),  # dup, deduped
        ]
        with patch.object(ip_utils.socket, "getaddrinfo", return_value=fake):
            assert ip_utils._resolve_hostname("example.com") == ["8.8.8.8", "1.1.1.1"]

    def test_returns_empty_on_gaierror(self):
        import socket as _socket
        from llmport.gateway import ip_utils
        with patch.object(ip_utils.socket, "getaddrinfo",
                          side_effect=_socket.gaierror):
            assert ip_utils._resolve_hostname("nonexistent.invalid") == []

    def test_bare_ip_short_circuits(self):
        from llmport.gateway.ip_utils import _resolve_hostname
        assert _resolve_hostname("8.8.8.8") == ["8.8.8.8"]
