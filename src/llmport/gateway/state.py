"""Gateway mutable state shared between the server and control API."""

import time

from llmport.config.store import ConfigStore
from llmport.models.provider import ProviderConfig
from llmport.models.model import merge_aliases_into_logical_models
from llmport.gateway.router import Router


def migrate_gateway_config(data: dict) -> dict:
    """Migrate old-format gateway config (openai_port/anthropic_port) to new format (host/port).

    Modifies *data* in place when migration is needed so callers can persist
    the result.  Returns the canonical ``{"host": str, "port": int}`` dict.
    """
    gw = data.get("gateway", {})
    if "host" not in gw:
        gw = {"host": "127.0.0.1", "port": gw.get("openai_port", 11434)}
        data["gateway"] = gw
    return {"host": gw["host"], "port": gw.get("port", 11434)}


class GatewayState:
    """Mutable state shared between the server and control API."""

    def __init__(self, store: ConfigStore):
        self.store = store
        self.providers: list[ProviderConfig] = []
        self.models = []
        self.active_model_id: str | None = None
        self.started_at = time.time()
        self.request_count = 0
        self.total_tokens = 0
        self.reload()

    def reload(self) -> None:
        """Reload config from disk."""
        data = self.store.load()
        had_host = "host" in data.get("gateway", {})
        self.gateway = migrate_gateway_config(data)
        if not had_host:
            self.store.save(data)
        self.providers = [
            ProviderConfig.from_dict(p) for p in data.get("providers", [])
        ]
        self.models = merge_aliases_into_logical_models(
            self.providers, data.get("models", []),
        )
        self.active_model_id = data.get("active_model")

    def save(self) -> None:
        """Persist current state to disk."""
        data = {
            "version": 1,
            "gateway": self.gateway,
            "providers": [p.to_dict() for p in self.providers],
            "models": [
                {
                    "id": m.id,
                    "bindings": [
                        {
                            "provider_id": b.provider_id,
                            "model_name": b.model_name,
                            "priority": b.priority,
                        }
                        for b in m.bindings
                    ],
                    "routing_strategy": m.routing_strategy,
                }
                for m in self.models
            ],
            "active_model": self.active_model_id,
        }
        self.store.save(data)

    def get_router(self) -> Router:
        return Router(self.providers, self.models, self.active_model_id)


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
