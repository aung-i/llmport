"""Provider data model."""

import time
from dataclasses import dataclass, field


@dataclass
class ProviderHealth:
    """Health check result for a provider (runtime state, not persisted in config).

    ``status == "down"`` has two flavors, distinguished by ``down_until``:

    * ``down_until is None`` -> **permanent** down. Used for SSRF-risky
      base_urls caught at config load (never recovered).
    * ``down_until`` is an epoch -> **temporary** down (runtime failure).
      :meth:`is_down` returns False once ``time.time()`` passes it, so the
      provider is retried on the next request without a background task.
    """
    status: str = "unknown"        # "up" | "degraded" | "down"
    latency_ms: float = 0.0
    last_check: str | None = None  # ISO timestamp
    down_until: float | None = None  # epoch seconds; None + "down" = permanent

    def is_down(self) -> bool:
        """True if the provider should be skipped by the router right now.

        A temporary (runtime) down recovers automatically once its cooldown
        expires; a permanent (SSRF) down never does.
        """
        if self.status != "down":
            return False
        if self.down_until is None:
            return True  # permanent
        return self.down_until > time.time()

    def mark_down(self, seconds: float) -> None:
        """Mark the provider temporarily down for ``seconds`` (runtime failure)."""
        self.status = "down"
        self.down_until = time.time() + seconds


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider - connection info only.

    Model routing lives in ``config.yaml`` (the ``models:`` section), not here.
    ``api_key`` is stored alongside ``base_url`` in ``providers.yaml``
    (self-contained) and read directly by ``from_dict`` -- there is no
    separate secrets vault. ``name``
    is the sole identity: it is both the slug referenced by model bindings
    and the display name.
    """

    name: str                      # unique slug + display name, e.g. "anthropic"
    protocol: str                  # "openai" | "anthropic"
    base_url: str                  # e.g. "https://api.anthropic.com"
    api_key: str = ""              # plaintext in memory; plaintext in providers.yaml on disk
    health: ProviderHealth = field(default_factory=ProviderHealth)

    def to_dict(self, include_key: bool = True) -> dict:
        result: dict = {
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
        # name is the sole identity; tolerate a hand-edited entry that still
        # uses the legacy `id` key by falling back to it.
        name = d.get("name") or d.get("id") or ""
        return cls(
            name=name,
            protocol=d.get("protocol") or "openai",
            base_url=d.get("base_url") or "",
            api_key=d.get("api_key", ""),
            health=health,
        )
