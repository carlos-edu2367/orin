"""Catalog-side adapters: change scope (K18), reviewer picking (K21) and quota assessment (K22). WS-C7."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agentos.agentic.model_tiers import family_of, tier_of

from ..ports import QuotaAssessment

logger = logging.getLogger("agentos.pipelines.adapters.catalog")


# ---------------------------------------------------------------------------- K18
class SessionChangeScope:
    """``ChangeScopePort``: the files changed since the turn began. ``AgentToolset.changed_paths()`` already includes
    what merged worktrees applied (``session.py`` hands a child's applied files up to the parent's toolset)."""

    def __init__(self, changed_paths: Callable[[], Any] | None) -> None:
        self._changed_paths = changed_paths

    def changed_files(self) -> tuple[str, ...]:
        if not callable(self._changed_paths):
            return ()
        try:
            return tuple(sorted({str(path).replace("\\", "/") for path in self._changed_paths() if str(path).strip()}))
        except Exception:  # noqa: BLE001 - an unknown scope makes the verifier look at the whole project, never fail
            logger.warning("could not read the changed files of the turn", exc_info=True)
            return ()


# ---------------------------------------------------------------------------- K22
class SessionQuota:
    """``QuotaPort`` over E's ``QuotaPolicy``: ``warn`` (cost notice) and ``blocked`` (background ceiling) providers."""

    def __init__(self, policy: Any, *, user_id: str, settings: Any) -> None:
        self._policy = policy
        self._user_id = user_id
        self._settings = settings

    @classmethod
    def for_session(cls, session: Any) -> SessionQuota | None:
        service = session.child_quota_service()
        if service is None or getattr(session, "effective_settings", None) is None:
            return None
        from agentos.quota.policy import QuotaPolicy

        return cls(QuotaPolicy(service), user_id=str(session.turn.get("user_id") or ""), settings=session.effective_settings)

    def assess(self, providers: Sequence[str]) -> QuotaAssessment:
        warn: list[str] = []
        blocked: list[str] = []
        for provider in dict.fromkeys(str(item) for item in providers if str(item).strip()):
            try:
                if not self._policy.check_background(self._user_id, provider, settings=self._settings).allowed:
                    blocked.append(provider)
                elif self._policy.warn(self._user_id, provider, settings=self._settings).should_warn:
                    warn.append(provider)
            except Exception:  # noqa: BLE001 - quota is advice: no reading means no warning and no block
                logger.warning("could not assess the quota of %s", provider, exc_info=True)
        return QuotaAssessment(warn=tuple(warn), blocked=tuple(blocked))


# ---------------------------------------------------------------------------- K21
@dataclass(frozen=True, slots=True)
class ModelRef:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


class ModelCatalogPort(Protocol):
    def pick_reviewers(self, n: int, author: ModelRef, distinct_families: bool = True) -> list[ModelRef]: ...


class FavoritesModelCatalog:
    """Picks the models that review an author's work (the ``auto`` of a ``models`` input, plan K21).

    Candidates are the favorite models of the configured providers. Tier 0 (unknown) models and subscriptions that
    are over their quota are out; models of another family than the author's come first, then the stronger ones.
    ``distinct_families`` keeps at most one model per family.
    """

    def __init__(
        self,
        favorites: Mapping[str, Sequence[str]],
        *,
        tiers: Mapping[str, Any] | None = None,
        rejected: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._favorites = favorites
        self._tiers = tiers
        self._rejected = rejected

    def pick_reviewers(self, n: int, author: ModelRef, distinct_families: bool = True) -> list[ModelRef]:
        if n <= 0:
            return []
        author_family = family_of(author.provider, author.model, tiers=self._tiers)
        ranked: list[tuple[tuple[int, int, str, str], ModelRef]] = []
        for provider, models in self._favorites.items():
            for model in models:
                if tier_of(provider, model, tiers=self._tiers) <= 0:
                    continue
                if self._rejected is not None and self._rejected(provider, model):
                    continue
                family = family_of(provider, model, tiers=self._tiers)
                same_model = int(provider == author.provider and model == author.model)
                key = (same_model, int(family == author_family), -tier_of(provider, model, tiers=self._tiers), f"{provider}/{model}")
                ranked.append((key, ModelRef(provider, model)))
        ranked.sort(key=lambda item: item[0])
        picked: list[ModelRef] = []
        families: set[str] = set()
        for _, ref in ranked:
            family = family_of(ref.provider, ref.model, tiers=self._tiers)
            if distinct_families and family in families:
                continue
            picked.append(ref)
            families.add(family)
            if len(picked) == n:
                break
        return picked


__all__ = ["FavoritesModelCatalog", "ModelCatalogPort", "ModelRef", "SessionChangeScope", "SessionQuota"]
