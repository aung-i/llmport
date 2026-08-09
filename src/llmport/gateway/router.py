"""Route requests to the right provider based on the requested model name."""

from llmport.models.provider import ProviderConfig
from llmport.models.model import LogicalModel


class RouterError(Exception):
    """Raised when routing fails (unknown model, no provider, no binding)."""


class Router:
    """Routes a request to a provider based on the model name the client sent.

    Bindings are tried in list order: :meth:`resolve` returns the first
    healthy provider. There is **no in-request fallback** -- if the chosen
    provider fails, the real upstream error is returned to the client and the
    provider is marked down (see :class:`ProviderHealth`); the *next* request
    then routes to the next binding via :meth:`resolve`.
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        models: list[LogicalModel],
    ):
        self._providers = {p.name: p for p in providers}
        self._models = {m.name: m for m in models}

    def resolve(self, model_name: str | None) -> tuple[ProviderConfig, str]:
        """Resolve a requested model name to a provider and upstream model name.

        Iterates bindings in order and returns the first healthy
        (not :meth:`~ProviderHealth.is_down`) provider.

        Returns ``(provider, upstream_model_name)``.
        Raises :class:`RouterError` if the model is unknown or has no
        healthy provider.
        """
        if not model_name:
            raise RouterError("Request is missing the 'model' field")
        model = self._models.get(model_name)
        if model is None:
            raise RouterError(f"Unknown model: {model_name!r}")

        for binding in model.bindings:
            provider = self._providers.get(binding.provider)
            if provider and not provider.health.is_down():
                return provider, binding.upstream

        raise RouterError(
            f"No healthy provider found for model {model_name!r}"
        )
