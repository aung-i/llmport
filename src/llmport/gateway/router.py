"""Route requests to the right provider based on the requested model name."""

from llmport.models.provider import ProviderConfig
from llmport.models.model import LogicalModel


class RouterError(Exception):
    """Raised when routing fails (unknown model, no provider, no binding)."""


class Router:
    """Routes a request to a provider based on the model name the client sent.

    The routing strategy (``routing_strategy`` on :class:`LogicalModel`)
    determines fallback behaviour.  Currently only ``"priority_fallback"``
    is implemented:

    - :meth:`resolve` returns the first healthy provider from the sorted
      binding list for the requested model.
    - :meth:`try_fallback` returns the **next** healthy provider after the
      one that just failed, skipping ``"down"`` providers.
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        models: list[LogicalModel],
    ):
        self._providers = {p.id: p for p in providers}
        self._models = {m.name: m for m in models}

    def resolve(self, model_name: str | None) -> tuple[ProviderConfig, str]:
        """Resolve a requested model name to a provider and upstream model name.

        Implements ``"priority_fallback"``: iterates bindings sorted by
        priority and returns the first healthy (non-``"down"``) provider.

        Returns ``(provider, upstream_model_name)``.
        Raises :class:`RouterError` if the model is unknown or has no
        healthy provider.
        """
        if not model_name:
            raise RouterError("Request is missing the 'model' field")
        model = self._models.get(model_name)
        if model is None:
            raise RouterError(f"Unknown model: {model_name!r}")

        for binding in model.bindings_sorted:
            provider = self._providers.get(binding.provider)
            if provider and provider.health.status != "down":
                return provider, binding.upstream

        raise RouterError(
            f"No healthy provider found for model {model_name!r}"
        )

    def try_fallback(
        self, model_name: str | None, last_provider_id: str
    ) -> tuple[ProviderConfig, str] | None:
        """Try the next provider in the fallback chain for ``model_name``.

        Returns ``(provider, upstream_model_name)`` or ``None`` if exhausted.
        """
        if not model_name:
            return None
        model = self._models.get(model_name)
        if model is None:
            return None

        found_last = False
        for binding in model.bindings_sorted:
            if found_last:
                provider = self._providers.get(binding.provider)
                if provider and provider.health.status != "down":
                    return provider, binding.upstream
            if binding.provider == last_provider_id:
                found_last = True

        return None
