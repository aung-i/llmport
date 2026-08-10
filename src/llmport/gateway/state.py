"""Gateway mutable state shared between the server and health endpoint."""

import time

from llmport.config.store import ConfigStore
from llmport.models.provider import ProviderConfig
from llmport.models.model import parse_models_config
from llmport.gateway.router import Router


class GatewayState:
    """Mutable state shared between the server and health endpoint."""

    def __init__(self, store: ConfigStore):
        self.store = store
        self.providers: list[ProviderConfig] = []
        self.models = []
        self.gateway: dict = {"host": "127.0.0.1", "port": 11434}
        self.api_key: str = ""  # llmport's own key (client->gateway auth)
        self.started_at = time.time()
        self.reload()

    def reload(self) -> None:
        """Reload gateway/models/providers from disk.

        Gateway + models come from the non-secret ``config.yaml``; providers
        (with their ``api_key``) come from ``providers.yaml``. A missing
        ``config.yaml`` degrades to the default gateway + no models (models
        are optional); a missing ``providers.yaml`` raises. A provider whose
        ``base_url`` is an SSRF risk (hand-edited) is marked "down" so the
        router skips it.
        """
        try:
            cfg = self.store.load_config()
        except FileNotFoundError:
            cfg = {}
        self.gateway = cfg.get("gateway") or {"host": "127.0.0.1", "port": 11434}

        pdata = self.store.load_providers_config()
        self.providers = [
            ProviderConfig.from_dict(p) for p in pdata.get("providers", [])
        ]
        # api_key lives inside each provider dict (from_dict reads it); no
        # separate secrets vault to inject from.
        # llmport's OWN api key (client->gateway auth) lives in its own
        # dedicated api_key.yaml (0600), separate from providers.yaml.
        # "" when unset -> no auth enforced (loopback-only default).
        self.api_key = self.store.load_api_key()

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

        self.models = parse_models_config(cfg.get("models", []))

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
