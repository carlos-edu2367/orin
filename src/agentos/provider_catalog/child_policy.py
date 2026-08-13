"""Provider-aware child model policy kept outside the multi-agent domain."""

from __future__ import annotations

from .models import ProviderCatalogContext


class ParentProviderChildModelPolicy:
    def __init__(self, catalog, grants=None) -> None:
        self._catalog = catalog
        self._grants = grants

    def list_available_models(self, *, user_id: str, parent, purpose: str):
        source = _provider(parent)
        if source is None:
            return ()
        return tuple(self._catalog.list(ProviderCatalogContext(user_id, purpose), source, favorites_only=True))

    def validate(self, *, parent, child, command) -> None:
        source, target = _provider(parent), _provider(child)
        if source is None or target is None or source != target:
            raise PermissionError("child model must use the parent provider")
        allowed = self.list_available_models(user_id=command.user_id, parent=parent, purpose=command.purpose)
        profile_ref = str(getattr(child, "model_profile_ref", ""))
        allowed_prefixes = tuple(f"model-profile:{source}:{item.model_id}:" for item in allowed)
        if not profile_ref.startswith(allowed_prefixes):
            raise PermissionError("child model must be a favorite of the parent provider")


def _provider(resolved) -> str | None:
    parts = str(getattr(resolved, "model_profile_ref", "")).split(":", 3)
    return parts[1] if len(parts) >= 3 and parts[0] == "model-profile" and parts[1] else None
