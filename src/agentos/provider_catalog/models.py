from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")


@dataclass(frozen=True, slots=True)
class ProviderCatalogContext:
    user_id: str
    purpose: str

    def __post_init__(self) -> None:
        _required(self.user_id, "user_id")
        _required(self.purpose, "purpose")


@dataclass(frozen=True, slots=True)
class PricingSummary:
    input_per_million: str | None = None
    output_per_million: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderModelRecord:
    provider: str
    model_id: str
    display_name: str
    context_window: int | None
    capabilities: tuple[str, ...]
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    pricing: PricingSummary | None
    refreshed_at: datetime
    is_favorite: bool = False
    # ``/v1/models`` is intentionally OpenAI-compatible.  OmniRoute documents
    # its dynamic ``auto/*`` routes, but does not promise a combo discriminator
    # for arbitrary user-defined combo IDs, so unknown IDs remain ``model``.
    route_kind: str = "model"

    def __post_init__(self) -> None:
        for name, value in (("provider", self.provider), ("model_id", self.model_id), ("display_name", self.display_name)):
            _required(value, name)
        if self.context_window is not None and self.context_window < 1:
            raise ValueError("context_window must be positive when present")
        if self.refreshed_at.tzinfo is None or self.refreshed_at.utcoffset() is None:
            raise ValueError("refreshed_at must be timezone-aware")
        if self.route_kind not in {"model", "auto"}:
            raise ValueError("route_kind must be model or auto")


@dataclass(frozen=True, slots=True)
class RefreshReceipt:
    refreshed_at: datetime
    count: int

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("count cannot be negative")
