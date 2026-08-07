"""Route requests to the right provider based on the requested model name."""

from llmport.models.provider import ProviderConfig
from llmport.models.model import LogicalModel


class RouterError(Exception):
    """Raised when routing fails (unknown model, no provider, no binding)."""


class Router:
    """Routes a request to a provider based on the model name the client sent.

    Bindings are tried in list order: :meth:`resolve` returns the first
    healthy provider; :meth:`try_fallback` returns the next healthy binding
    after the one that just failed -- which may be another upstream on the
    same provider, or the next provider entirely.
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
        (non-``"down"``) provider.

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
            if provider and provider.health.status != "down":
                return provider, binding.upstream

        raise RouterError(
            f"No healthy provider found for model {model_name!r}"
        )

    def try_fallback(
        self, model_name: str | None, last_binding: tuple[str, str] | None
    ) -> tuple[ProviderConfig, str] | None:
        """Try the next binding in the fallback chain for ``model_name``.

        ``last_binding`` is the ``(provider_name, upstream)`` tuple that just
        failed; the next healthy binding after it is returned. Using the full
        tuple (not just the provider name) means multiple upstreams on the
        same provider are each tried in turn before crossing to the next
        provider.

        Returns ``(provider, upstream_model_name)`` or ``None`` if exhausted.
        """
        if not model_name or last_binding is None:
            return None
        model = self._models.get(model_name)
        if model is None:
            return None

        found_last = False
        for binding in model.bindings:
            if found_last:
                provider = self._providers.get(binding.provider)
                if provider and provider.health.status != "down":
                    return provider, binding.upstream
            if (binding.provider, binding.upstream) == last_binding:
                found_last = True

        return None
