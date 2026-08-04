"""Provider data model."""

from dataclasses import dataclass, field


@dataclass
class ProviderHealth:
    """Health check result for a provider (runtime state, not persisted in config)."""
    status: str = "unknown"        # "up" | "degraded" | "down"
    latency_ms: float = 0.0
    last_check: str | None = None  # ISO timestamp


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider - connection info only.

    Model routing lives in the ``models`` config section, not here.
    ``api_key`` is populated at runtime from the encrypted secrets vault
    and is never written to the readable ``config.yaml``.
    """

    id: str                        # unique slug, e.g. "anthropic"
    name: str                      # display name, e.g. "Anthropic"
    protocol: str                  # "openai" | "anthropic"
    base_url: str                  # e.g. "https://api.anthropic.com"
    api_key: str = ""              # plaintext in memory; encrypted in secrets.enc on disk
    health: ProviderHealth = field(default_factory=ProviderHealth)

    def to_dict(self, include_key: bool = True) -> dict:
        result: dict = {
            "id": self.id,
            "name": self.name,
            "protocol": self.protocol,
            "base_url": self.base_url,
        }
        if include_key:
            result["api_key"] = self.api_key
        else:
            result["api_key"] = "***" if self.api_key else ""
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderConfig":
        # Tolerate legacy entries that carried `models`/`health`; they are
        # ignored here - models live in the `models` section, health is runtime.
        health = ProviderHealth()
        raw_health = d.get("health") or {}
        if isinstance(raw_health, dict):
            for k in ("status", "latency_ms", "last_check"):
                if k in raw_health:
                    setattr(health, k, raw_health[k])
        # Use .get() so a hand-edited config missing a field degrades to an
        # empty/default value instead of raising KeyError and crashing startup.
        pid = d.get("id") or ""
        return cls(
            id=pid,
            name=d.get("name") or pid,
            protocol=d.get("protocol") or "openai",
            base_url=d.get("base_url") or "",
            api_key=d.get("api_key", ""),
            health=health,
        )
