"""Logical model and binding data models."""

from dataclasses import dataclass, field


@dataclass
class ModelBinding:
    """Binds a public model name to a specific provider's upstream model.

    Attributes:
        provider: the provider name this binding routes to.
        upstream: the real model name on that provider's API.
    """

    provider: str
    upstream: str


@dataclass
class LogicalModel:
    """A model as named by the client in the request body.

    Attributes:
        name: the public model name clients send in ``{"model": name}``.
        bindings: provider bindings, tried in list order for fallback.
            The first healthy provider wins; on failure the next binding is
            tried, which may be another upstream on the same provider.
    """

    name: str
    bindings: list[ModelBinding] = field(default_factory=list)

    @property
    def provider_count(self) -> int:
        return len({b.provider for b in self.bindings})


def parse_models_config(models_data) -> list[LogicalModel]:
    """Parse the ``models`` config section into :class:`LogicalModel` instances.

    ``models`` is a dict keyed by the public model name. Each value is
    normalized into an ordered list of ``(provider, upstream)`` bindings;
    ``upstream`` defaults to the public name when omitted. Bindings are
    tried in order for fallback.

    Supported value forms (all normalize to bindings)::

        claude-sonnet: anthropic                 # str -> single provider, upstream=name
        gpt-4o:                                   # list of providers (upstream=name)
          - openai
          - azure
        sonnet:                                   # provider: single upstream
          - anthropic: claude-sonnet-4
        gpt4:                                     # provider: list of upstreams (fallback)
          - openai: gpt-4
          - azure: [gpt4o-deploy, gpt4o-turbo]

    A list element may be a plain provider name (str) or a single-key
    ``{provider: upstream}`` dict whose upstream is a str or a list. A bare
    dict value (no enclosing list) is also accepted for a single-provider
    alias. Malformed entries are skipped rather than crashing the daemon.
    """
    models: list[LogicalModel] = []
    if not isinstance(models_data, dict):
        return models
    for name, value in models_data.items():
        bindings = _normalize_bindings(value, name)
        if bindings:
            models.append(LogicalModel(name=name, bindings=bindings))
    return models


def _normalize_bindings(value, name: str) -> list[ModelBinding]:
    """Normalize a models.yaml value into ordered bindings.

    ``name`` is the public model name, used as the default ``upstream``.
    """
    out: list[ModelBinding] = []
    if isinstance(value, str):
        if value:
            out.append(ModelBinding(provider=value, upstream=name))
    elif isinstance(value, dict):
        # Bare {provider: upstream} for a single-provider alias.
        for prov, up in value.items():
            out.extend(_bindings_from_upstream(prov, up, name))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item:
                    out.append(ModelBinding(provider=item, upstream=name))
            elif isinstance(item, dict):
                for prov, up in item.items():
                    out.extend(_bindings_from_upstream(prov, up, name))
    return out


def _bindings_from_upstream(provider: str, upstream, name: str) -> list[ModelBinding]:
    """Expand one provider's upstream (str or list) into bindings."""
    if not provider:
        return []
    if isinstance(upstream, list):
        return [
            ModelBinding(provider=provider, upstream=u or name)
            for u in upstream
            if isinstance(u, str)
        ]
    return [ModelBinding(provider=provider, upstream=upstream or name)]
