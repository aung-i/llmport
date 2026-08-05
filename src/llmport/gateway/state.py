"""Gateway mutable state shared between the server and control API."""

import time

from llmport.config.store import ConfigStore
from llmport.models.provider import ProviderConfig
from llmport.models.model import parse_models_config
from llmport.gateway.router import Router


class GatewayState:
    """Mutable state shared between the server and control API."""

    def __init__(self, store: ConfigStore):
        self.store = store
        self.providers: list[ProviderConfig] = []
        self.models = []
        self.gateway: dict = {"host": "127.0.0.1", "port": 11434}
        self.started_at = time.time()
        self.request_count = 0
        self.total_tokens = 0
        self.reload()

    def reload(self) -> None:
        """Reload config and secrets from disk.

        Providers are built from ``config.yaml`` with their API keys injected
        from the plaintext ``secrets.yaml`` vault. Models are parsed from the
        ``models`` section.
        """
        data = self.store.load_config()
        self.gateway = data.get("gateway") or {"host": "127.0.0.1", "port": 11434}
        secrets = self.store.load_secrets()

        self.providers = [
            ProviderConfig.from_dict(p) for p in data.get("providers", [])
        ]
        for p in self.providers:
            p.api_key = secrets.get(p.id, "")

        # Defense-in-depth for hand-edited configs: a provider whose base_url
        # is an SSRF risk (metadata / self-loop) is marked "down" so the router
        # skips it. The CLI write path rejects these outright (see
        # config.validation); this catches configs edited on disk directly.
        from llmport.config.validation import validate_provider_base_url
        for p in self.providers:
            try:
                validate_provider_base_url(
                    p.base_url, self.gateway.get("host", "127.0.0.1"),
                    int(self.gateway.get("port", 11434)),
                )
            except ValueError:
                p.health.status = "down"

        self.models = parse_models_config(data.get("models", []))

    def get_router(self) -> Router:
        return Router(self.providers, self.models)


STATE: GatewayState | None = None


def init_state(store: ConfigStore) -> GatewayState:
    """Initialize the global GatewayState singleton (called by ``create_app``)."""
    global STATE
    STATE = GatewayState(store)
    return STATE


def get_state() -> GatewayState:
    """Return the global GatewayState singleton.

    Raises ``AssertionError`` if ``init_state()`` has not been called.
    """
    assert STATE is not None
    return STATE


def migrate_gateway_config(data: dict) -> dict:
    """Return the canonical ``{"host", "port"}`` gateway dict from a config dict.

    Kept as a thin shim for callers that still normalize a loaded config dict.
    """
    gw = data.get("gateway") or {}
    if "host" not in gw:
        gw = {
            "host": "127.0.0.1",
            "port": gw.get("openai_port", gw.get("anthropic_port", 11434)),
        }
    return {"host": gw.get("host", "127.0.0.1"), "port": int(gw.get("port", 11434))}
