"""Logical model and binding data models."""

from dataclasses import dataclass, field

from llmgate.models.provider import ProviderConfig


@dataclass
class ModelBinding:
    """Binds a logical model to a specific provider's model name with priority."""
    provider_id: str
    model_name: str               # actual name on that provider
    priority: int = 1


@dataclass
class LogicalModel:
    """A model as seen and selected by the user.

    Auto-created from provider model aliases that share the same alias string.
    """
    id: str                       # the alias that created/identifies this model
    bindings: list[ModelBinding] = field(default_factory=list)
    routing_strategy: str = "priority_fallback"

    @property
    def provider_count(self) -> int:
        return len({b.provider_id for b in self.bindings})

    @property
    def bindings_sorted(self) -> list[ModelBinding]:
        return sorted(self.bindings, key=lambda b: b.priority)


def merge_aliases_into_logical_models(
    providers: list[ProviderConfig],
    existing_models: list[dict] | None = None,
) -> list[LogicalModel]:
    """Build logical models from provider model aliases.

    When two providers both have a model with alias "claude-opus",
    they merge into one LogicalModel with two bindings.

    If existing_models is provided, manual bindings from those models
    are merged in as well.
    """
    alias_map: dict[str, list[ModelBinding]] = {}

    for p in providers:
        for m in p.models:
            aliases = m.aliases if m.aliases else [m.name]
            for alias in aliases:
                if alias not in alias_map:
                    alias_map[alias] = []
                binding = ModelBinding(
                    provider_id=p.id,
                    model_name=m.name,
                    priority=len(alias_map[alias]) + 1,
                )
                # Avoid duplicates
                if not any(
                    b.provider_id == binding.provider_id
                    and b.model_name == binding.model_name
                    for b in alias_map[alias]
                ):
                    alias_map[alias].append(binding)

    # Merge with existing manual models
    if existing_models:
        for em in existing_models:
            alias = em["id"]
            if alias not in alias_map:
                alias_map[alias] = []
            for b in em.get("bindings", []):
                binding = ModelBinding(
                    provider_id=b["provider_id"],
                    model_name=b["model_name"],
                    priority=b.get("priority", 1),
                )
                if not any(
                    x.provider_id == binding.provider_id
                    and x.model_name == binding.model_name
                    for x in alias_map[alias]
                ):
                    alias_map[alias].append(binding)

    return [
        LogicalModel(id=alias, bindings=bindings)
        for alias, bindings in alias_map.items()
    ]
