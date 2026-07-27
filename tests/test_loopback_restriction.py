"""Tests for loopback hard-limit on gateway host (Issue 9).

Per the spec, non-loopback hosts (0.0.0.0, 192.168.x.x, etc.) must be rejected
with HTTP 400 when updating gateway config.  Only loopback addresses
(127.0.0.1, localhost, ::1) are allowed.
"""

import tempfile

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_control_app(tmp):
    store = ConfigStore(tmp)
    store.init_first_run()
    return create_app(store)


class TestLoopbackRestriction:

    LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"]
    NON_LOOPBACK_HOSTS = ["0.0.0.0", "192.168.1.1", "10.0.0.5", "172.16.0.1"]

    def test_loopback_hosts_accepted(self):
        """Loopback hosts (127.0.0.1, localhost, ::1) must return 200."""
        with tempfile.TemporaryDirectory() as tmp:
            control_app = _make_control_app(tmp)
            client = TestClient(control_app)

            for host in self.LOOPBACK_HOSTS:
                resp = client.post("/api/gateway/config", json={
                    "host": host,
                    "port": 11434,
                })
                assert resp.status_code == 200, (
                    f"Loopback host '{host}' should be accepted (200), "
                    f"got {resp.status_code}"
                )

    def test_non_loopback_hosts_rejected(self):
        """Non-loopback hosts must return 400."""
        with tempfile.TemporaryDirectory() as tmp:
            control_app = _make_control_app(tmp)
            client = TestClient(control_app)

            for host in self.NON_LOOPBACK_HOSTS:
                resp = client.post("/api/gateway/config", json={
                    "host": host,
                    "port": 11434,
                })
                assert resp.status_code == 400, (
                    f"Non-loopback host '{host}' should be rejected (400), "
                    f"got {resp.status_code}"
                )
                data = resp.json()
                assert "error" in data, (
                    f"Response for '{host}' must contain 'error' key"
                )

    def test_error_message_mentions_loopback(self):
        """The 400 error message should mention loopback or localhost."""
        with tempfile.TemporaryDirectory() as tmp:
            control_app = _make_control_app(tmp)
            client = TestClient(control_app)

            resp = client.post("/api/gateway/config", json={
                "host": "0.0.0.0",
                "port": 11434,
            })
            assert resp.status_code == 400
            error_msg = resp.json().get("error", "")
            # Should mention "loopback", "localhost", or similar
            assert any(kw in error_msg.lower() for kw in ("loopback", "localhost", "127.0.0.1", "回环")), (
                f"Error message should mention loopback restriction, got: {error_msg}"
            )

    def test_port_validation_still_works(self):
        """Port validation (1024-65535) must still function alongside host check."""
        with tempfile.TemporaryDirectory() as tmp:
            control_app = _make_control_app(tmp)
            client = TestClient(control_app)

            # Valid host, invalid port
            resp = client.post("/api/gateway/config", json={
                "host": "127.0.0.1",
                "port": 80,
            })
            assert resp.status_code == 400, (
                f"Port 80 should be rejected, got {resp.status_code}"
            )
