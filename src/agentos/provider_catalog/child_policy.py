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
        return tuple(self._catalog.list(ProviderCatalogContext(user_id, purpose), source))

    def validate(self, *, parent, child, command) -> None:
        source, target = _provider(parent), _provider(child)
        if source is None or target is None or source == target:
            return
        if self._grants is not None and self._grants.allows(
            parent=parent, child=child, authorization_ref=command.authorization_ref,
            user_id=command.user_id, workspace_id=command.workspace_id, purpose=command.purpose,
        ):
            return
        raise PermissionError("cross-provider child model requires an explicit grant")


def _provider(resolved) -> str | None:
    parts = str(getattr(resolved, "model_profile_ref", "")).split(":", 3)
    return parts[1] if len(parts) >= 3 and parts[0] == "model-profile" and parts[1] else None
