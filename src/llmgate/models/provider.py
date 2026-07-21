"""Provider data model."""

from dataclasses import dataclass, field


@dataclass
class ProviderModel:
    """A model offered by a provider, with its aliases."""
    name: str                      # actual model name on the provider's API
    aliases: list[str] = field(default_factory=list)


@dataclass
class ProviderHealth:
    """Health check result for a provider."""
    status: str = "unknown"        # "up" | "degraded" | "down"
    latency_ms: float = 0.0
    last_check: str | None = None  # ISO timestamp


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""
    id: str                        # unique slug, e.g. "anthropic"
    name: str                      # display name, e.g. "Anthropic"
    protocol: str                  # "openai" | "anthropic"
    base_url: str                  # e.g. "https://api.anthropic.com"
    api_key: str                   # plaintext key in memory, encrypted on disk
    models: list[ProviderModel] = field(default_factory=list)
    health: ProviderHealth = field(default_factory=ProviderHealth)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "models": [{"name": m.name, "aliases": m.aliases} for m in self.models],
            "health": {
                "status": self.health.status,
                "latency_ms": self.health.latency_ms,
                "last_check": self.health.last_check,
            },
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        return cls(
            id=d["id"],
            name=d["name"],
            protocol=d["protocol"],
            base_url=d["base_url"],
            api_key=d["api_key"],
            models=[ProviderModel(name=m["name"], aliases=m.get("aliases", []))
                    for m in d.get("models", [])],
            health=ProviderHealth(**d.get("health", {})),
        )
