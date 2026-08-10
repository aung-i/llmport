"""Shared test helpers.

Auth on the gateway is **mandatory** (there is no unauthenticated mode), so
any test that drives the ASGI app must present llmport's API key. Two pieces
make that uniform across the routing/forwarding/translation test suites:

- ``TEST_API_KEY`` -- a fixed key the helpers write into the test store, so
  ``GatewayState.api_key`` matches what the client sends.
- ``AuthedClient`` -- a :class:`starlette.testclient.TestClient` subclass that
  injects ``x-api-key: TEST_API_KEY`` as a default header on every request.

Tests that deliberately exercise the auth boundary (no key, wrong key, etc.)
use plain ``TestClient`` and explicit headers instead -- see ``test_api_key``.
"""

from starlette.testclient import TestClient

# Fixed key shared between the test store and AuthedClient. Real keys are
# random (``generate_api_key``); a fixed value keeps tests deterministic.
TEST_API_KEY = "sk-llmport-test-fixed-key"


class AuthedClient(TestClient):
    """TestClient that sends llmport's API key on every request by default.

    ``x-api-key`` is added via ``setdefault`` so a test can still override it
    (or omit auth) by passing its own ``headers``.
    """

    def __init__(self, app, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("x-api-key", TEST_API_KEY)
        super().__init__(app, headers=headers, **kwargs)
