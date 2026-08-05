"""Tests for the loopback hard-limit on the gateway bind address.

The gateway is loopback-only by design. Enforcement moved from the removed
``POST /api/gateway/config`` endpoint to the daemon bind path
(:func:`llmport.daemon._loopback_host`): no matter what ``config.yaml`` says,
the daemon never binds a non-loopback interface. This catches hand-edited
configs too, not just programmatic changes.
"""

from llmport.daemon import _loopback_host


class TestLoopbackRestriction:

    LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"]
    NON_LOOPBACK_HOSTS = ["0.0.0.0", "192.168.1.1", "10.0.0.5", "172.16.0.1"]

    def test_loopback_hosts_pass_through(self):
        """Loopback hosts are returned unchanged (the daemon binds them)."""
        for host in self.LOOPBACK_HOSTS:
            assert _loopback_host(host) == host, (
                f"Loopback host '{host}' should pass through unchanged, "
                f"got {_loopback_host(host)!r}"
            )

    def test_non_loopback_hosts_clamped_to_loopback(self):
        """Non-loopback hosts are clamped to 127.0.0.1 (never bind externally)."""
        for host in self.NON_LOOPBACK_HOSTS:
            assert _loopback_host(host) == "127.0.0.1", (
                f"Non-loopback host '{host}' must be clamped to 127.0.0.1, "
                f"got {_loopback_host(host)!r}"
            )

    def test_empty_and_garbage_clamped(self):
        """Empty / garbage hosts fall back to 127.0.0.1, never propagate."""
        for host in ["", "not-a-host", "example.com", "::ffff:8.8.8.8"]:
            assert _loopback_host(host) == "127.0.0.1"
