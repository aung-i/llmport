"""Tests for the remaining control API endpoints.

The control API was narrowed to read-only status + lifecycle. Configuration
CRUD / test / fetch endpoints were removed (they formed a programmatic SSRF
entry via arbitrary ``base_url``); providers and models are now managed
through the CLI, which writes ``config.yaml`` + ``secrets.enc`` and restarts
the daemon.
"""

import tempfile
from unittest.mock import MagicMock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app


def _make_app(tmp):
    """Create the gateway app with one provider and one model."""
    store = ConfigStore(tmp)
    store.init_first_run()
    store.save_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {"id": "p1", "name": "P1", "protocol": "openai",
             "base_url": "https://api.p1.com"},
        ],
        "models": [
            {"name": "gpt-5", "provider": "p1", "upstream": "gpt-5"},
        ],
    })
    store.save_secrets({"p1": "sk-p1"})
    return create_app(store)


class TestControlStatusEndpoint:
    """GET /api/status returns runtime stats and provider/model summaries."""

    def test_status_returns_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(_make_app(tmp))
            resp = client.get("/api/status")
            assert resp.status_code == 200

    def test_status_returns_stats_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(_make_app(tmp))
            data = client.get("/api/status").json()
            assert data["provider_count"] == 1
            assert data["model_count"] == 1
            assert data["models"] == ["gpt-5"]
            assert data["providers"][0]["id"] == "p1"
            assert "uptime" in data and "request_count" in data
            assert data["gateway"]["host"] == "127.0.0.1"

    def test_bad_base_url_provider_marked_down(self):
        """A provider with an SSRF base_url (hand-edited config) is loaded but
        marked 'down' so the router skips it -- never forwarded to."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            store.save_secrets({"bad": "sk"})
            # Write config directly to disk, bypassing save_config validation
            # (simulates a hand-edited config.yaml).
            store.config_path.write_text(
                "version: 1\n"
                "gateway:\n  host: 127.0.0.1\n  port: 11434\n"
                "providers:\n"
                "  - id: bad\n    name: Bad\n    protocol: openai\n"
                "    base_url: http://169.254.169.254\n"
                "models: []\n",
                encoding="utf-8",
            )
            client = TestClient(create_app(store))
            data = client.get("/api/status").json()
            assert data["providers"][0]["id"] == "bad"
            assert data["providers"][0]["status"] == "down"


class TestControlDaemonLifecycle:
    """POST /api/daemon/stop and /restart."""

    def test_daemon_stop_signals_shutdown(self):
        """POST /api/daemon/stop sets should_exit on the registered server."""
        from llmport.gateway.control_api import (
            control_daemon_stop,
            set_shutdown_server,
        )
        app = Starlette(routes=[
            Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        ])
        client = TestClient(app)
        mock_server = MagicMock()
        mock_server.should_exit = False
        set_shutdown_server(mock_server)
        try:
            resp = client.post("/api/daemon/stop")
        finally:
            set_shutdown_server(None)
        assert resp.status_code == 200
        assert resp.json().get("ok") is True
        assert mock_server.should_exit is True

    def test_daemon_stop_without_registered_server(self):
        """POST /api/daemon/stop still returns ok when no server is registered."""
        from llmport.gateway.control_api import (
            control_daemon_stop,
            set_shutdown_server,
        )
        app = Starlette(routes=[
            Route("/api/daemon/stop", control_daemon_stop, methods=["POST"]),
        ])
        client = TestClient(app)
        set_shutdown_server(None)
        resp = client.post("/api/daemon/stop")
        assert resp.status_code == 200
        assert resp.json().get("ok") is True

    def test_daemon_restart_endpoint(self):
        """POST /api/daemon/restart returns the restart action."""
        from llmport.gateway.control_api import control_daemon_restart
        app = Starlette(routes=[
            Route("/api/daemon/restart", control_daemon_restart, methods=["POST"]),
        ])
        client = TestClient(app)
        resp = client.post("/api/daemon/restart")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["action"] == "restart"


class TestControlWriteEndpointsRemoved:
    """The config write/test/fetch endpoints are gone (SSRF surface)."""

    def test_removed_endpoints_return_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(_make_app(tmp))
            for path, method in [
                ("/api/providers", "GET"),
                ("/api/providers", "POST"),
                ("/api/providers", "DELETE"),
                ("/api/providers/test", "POST"),
                ("/api/providers/models", "POST"),
                ("/api/models", "GET"),
                ("/api/models", "DELETE"),
                ("/api/gateway/config", "GET"),
                ("/api/gateway/config", "POST"),
            ]:
                resp = client.request(method, path, json={})
                assert resp.status_code == 404, (
                    f"{method} {path} should be removed (404), "
                    f"got {resp.status_code}"
                )
