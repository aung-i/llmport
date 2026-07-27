"""Logical model and binding data models."""

from dataclasses import dataclass, field


@dataclass
class ModelBinding:
    """Binds a public model name to a specific provider's upstream model.

    Attributes:
        provider: the provider id this binding routes to.
        upstream: the real model name on that provider's API.
        priority: fallback order (1 = primary, 2+ = fallback).
    """

    provider: str
    upstream: str
    priority: int = 1


@dataclass
class LogicalModel:
    """A model as named by the client in the request body.

    Attributes:
        name: the public model name clients send in ``{"model": name}``.
        bindings: provider bindings, tried in priority order for fallback.
        routing_strategy: how the next provider is picked on failure.
            Currently only ``"priority_fallback"`` is supported: providers
            are tried in priority order and the first healthy one is used.
    """

    name: str
    bindings: list[ModelBinding] = field(default_factory=list)
    routing_strategy: str = "priority_fallback"

    @property
    def provider_count(self) -> int:
        return len({b.provider for b in self.bindings})

    @property
    def bindings_sorted(self) -> list[ModelBinding]:
        return sorted(self.bindings, key=lambda b: b.priority)


def parse_models_config(models_data: list[dict] | None) -> list[LogicalModel]:
    """Parse the ``models`` config section into :class:`LogicalModel` instances.

    Two entry shapes are supported:

    Shorthand (single binding, no fallback)::

        - name: claude-sonnet
          provider: anthropic
          upstream: claude-sonnet-4

    Full form (multiple bindings for fallback)::

        - name: gpt-4o
          bindings:
            - {provider: openai, upstream: gpt-4o, priority: 1}
            - {provider: azure-openai, upstream: gpt4o-deploy, priority: 2}
    """
    models: list[LogicalModel] = []
    for entry in models_data or []:
        name = entry.get("name") or entry.get("id")
        if not name:
            continue
        strategy = entry.get("routing_strategy", "priority_fallback")
        if "bindings" in entry:
            bindings = [
                ModelBinding(
                    provider=b["provider"],
                    upstream=b["upstream"],
                    priority=b.get("priority", 1),
                )
                for b in entry["bindings"]
            ]
        else:
            bindings = [
                ModelBinding(
                    provider=entry["provider"],
                    upstream=entry["upstream"],
                    priority=entry.get("priority", 1),
                )
            ]
        models.append(
            LogicalModel(name=name, bindings=bindings, routing_strategy=strategy)
        )
    return models
