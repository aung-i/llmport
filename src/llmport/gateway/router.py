"""Route requests to the right provider based on active model and priority."""

from llmport.models.provider import ProviderConfig
from llmport.models.model import LogicalModel


class RouterError(Exception):
    """Raised when routing fails (no model, no provider, no binding)."""


class Router:
    """Routes requests to the right provider based on active model and priority.

    The routing strategy (``routing_strategy`` on :class:`LogicalModel`)
    determines fallback behaviour.  Currently only ``"priority_fallback"``
    is implemented:

    - ``resolve()`` returns the first healthy provider from the sorted
      binding list.
    - ``try_fallback()`` returns the **next** healthy provider after the
      one that just failed, skipping ``"down"`` providers.
    """

    def __init__(
        self,
        providers: list[ProviderConfig],
        models: list[LogicalModel],
        active_model_id: str | None,
    ):
        self._providers = {p.id: p for p in providers}
        self._models = {m.id: m for m in models}
        self.active_model_id = active_model_id

    @property
    def active_model(self) -> LogicalModel | None:
        if self.active_model_id is None:
            return None
        return self._models.get(self.active_model_id)

    def resolve(self) -> tuple[ProviderConfig, str]:
        """Resolve the active model to a specific provider and model name.

        Implements ``"priority_fallback"``: iterates bindings sorted by
        priority and returns the first healthy (non-``"down"``) provider.

        Returns (provider, actual_model_name).
        Raises RouterError if no active model or no bindings.
        """
        model = self.active_model
        if model is None:
            raise RouterError("No active model selected")

        for binding in model.bindings_sorted:
            provider = self._providers.get(binding.provider_id)
            if provider and provider.health.status != "down":
                return provider, binding.model_name

        raise RouterError(
            f"No healthy provider found for model '{model.id}'"
        )

    def try_fallback(
        self, last_provider_id: str
    ) -> tuple[ProviderConfig, str] | None:
        """Try the next provider in the fallback chain.

        Returns (provider, model_name) or None if exhausted.
        """
        model = self.active_model
        if model is None:
            return None

        found_last = False
        for binding in model.bindings_sorted:
            if found_last:
                provider = self._providers.get(binding.provider_id)
                if provider and provider.health.status != "down":
                    return provider, binding.model_name
            if binding.provider_id == last_provider_id:
                found_last = True

        return None

    def set_active_model(self, model_id: str | None) -> None:
        self.active_model_id = model_id
