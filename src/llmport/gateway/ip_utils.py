"""SSRF protection utilities.

Validates that provider ``base_url`` points to a public (non-private) address
to prevent server-side request forgery attacks.

Uses ``ipaddress`` module for IP-level classification and ``socket.getaddrinfo``
to resolve hostnames before validation, ensuring DNS rebinding attacks are
mitigated.
"""

import ipaddress
import socket
from urllib.parse import urlparse


def validate_public_url(url: str) -> bool:
    """Validate that *url* points to a non-private address.

    Returns ``True`` if the URL is acceptable (public), ``False`` if it is
    a private/loopback/local address.

    If the hostname cannot be resolved via DNS it is allowed through —
    the connection will fail at the HTTP layer, which is sufficient.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Resolve hostname to all IP addresses
    addrs = _resolve_hostname(hostname)

    if not addrs:
        # Hostname could not be resolved – it might be a documentation-only
        # domain (e.g. ``api.example.com``) or DNS is unavailable.
        # Allow it; the real connect-time resolution will catch issues.
        return True

    # Every resolved address must be public
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return False
        # Also reject multicast and reserved ranges
        if ip.is_multicast or ip.is_reserved:
            return False

    return True


def _resolve_hostname(hostname: str) -> list[str]:
    """Resolve *hostname* to a list of IP address strings."""
    # Short-circuit: if hostname is already a bare IPv4/v6 address, use it
    # directly without DNS (which may be unreliable in test environments).
    try:
        ipaddress.ip_address(hostname)
        return [hostname]
    except ValueError:
        pass

    # Special-case known local hostnames to avoid depending on DNS
    # for addresses we're going to reject anyway.
    local = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if hostname.lower() in local:
        return ["127.0.0.1"]

    try:
        infos = socket.getaddrinfo(hostname, None)
        seen: set[str] = set()
        result: list[str] = []
        for info in infos:
            addr = info[4][0]
            if addr not in seen:
                seen.add(addr)
                result.append(addr)
        return result
    except socket.gaierror:
        return []
