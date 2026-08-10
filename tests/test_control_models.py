"""Tests for the gateway's /health probe and the removed control surface.

The HTTP surface carries no control: lifecycle (stop / restart) is via
process signals from the CLI, and config CRUD/test/fetch endpoints were
removed (they formed a programmatic SSRF entry via arbitrary base_url).
The only non-forwarding route is the read-only ``/health`` liveness probe.
"""

import tempfile

from starlette.testclient import TestClient

from llmport.config.store import ConfigStore
from llmport.gateway.server import create_app, get_state
from tests._helpers import TEST_API_KEY, AuthedClient


def _make_app(tmp):
    """Create the gateway app with one provider and one model."""
    store = ConfigStore(tmp)
    store.init_first_run()
    store.set_api_key(TEST_API_KEY)
    store.save_providers_config({
        "version": 1,
        "gateway": {"host": "127.0.0.1", "port": 11434},
        "providers": [
            {"name": "p1", "protocol": "openai",
             "base_url": "https://api.p1.com", "api_key": "sk-p1"},
        ],
    })
    store.save_models_config({"models": {"gpt-5": "p1"}})
    return create_app(store)


class TestHealthEndpoint:
    """GET /health is a read-only liveness probe."""

    def test_health_returns_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AuthedClient(_make_app(tmp))
            resp = client.get("/health")
            assert resp.status_code == 200

    def test_health_returns_status_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AuthedClient(_make_app(tmp))
            data = client.get("/health").json()
            assert data == {"status": "ok"}


class TestBadBaseUrlMarkedDown:
    """A hand-edited SSRF base_url provider is loaded but marked 'down' so the
    router skips it -- never forwarded to. Checked via state, not /health,
    which only reports liveness."""

    def test_bad_base_url_provider_marked_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(tmp)
            store.init_first_run()
            # Write providers.yaml directly to disk, bypassing
            # save_providers_config validation (simulates a hand-edited file).
            store.providers_path.write_text(
                "version: 1\n"
                "gateway:\n  host: 127.0.0.1\n  port: 11434\n"
                "providers:\n"
                "  - name: bad\n    protocol: openai\n"
                "    base_url: http://169.254.169.254\n"
                "    api_key: sk\n",
                encoding="utf-8",
            )
            create_app(store)
            providers = get_state().providers
            assert providers[0].name == "bad"
            assert providers[0].health.status == "down"


class TestControlEndpointsRemoved:
    """No control surface rides on the forwarding port."""

    def test_removed_endpoints_return_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = AuthedClient(_make_app(tmp))
            for path, method in [
                # Lifecycle control (now via signals from the CLI).
                ("/api/daemon/stop", "POST"),
                ("/api/daemon/restart", "POST"),
                # Old read-only status (replaced by /health).
                ("/api/status", "GET"),
                # Config write/test/fetch (SSRF surface).
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
