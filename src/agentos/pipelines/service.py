"""``PipelineService``: the one entry point to list, validate, start, pause, resume and cancel pipelines (WS-C10).

The HTTP routes, the ``RunPipeline`` tool, the ``orin pipeline`` command and the ``/goal`` commands all go through
here. The service never executes a step: it checks everything that can be checked without running anything
(``pipelines.enabled``, trust, approval by hash, structure and context, inputs), writes the journal run and asks the
worker for a ``turn_kind="pipeline"`` turn through ``ChatApplication.send_pipeline``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from agentos.permissions.trust import WorkspaceTrustStore
from agentos.persistence.postgres.schema import pipeline_runs
from agentos.settings.writer import SettingsWriteError, _file_lock

from . import events as pipeline_events
from . import paths as _paths
from .adapters.catalog import FavoritesModelCatalog, ModelRef
from .approvals import ApprovalStore
from .discovery import DiscoveryDisabled, DiscoveryEntry, discover, read_checked
from .driver import PAUSE_REQUESTED, active_run_ids, cancel_run, pause_run
from .errors import PipelineError, ValidationReport
from .goal import GOAL_PIPELINE_ID, GoalController, GoalError
from .inputs import DegradedNote, InputError, program_inputs, resolve_inputs, whole_command_inputs
from .model import Budget, PipelineDefinition, compute_definition_hash
from .resume import unwrap_envelope
from .settings import PipelineSettings, cap_budget_to_ceiling, ceiling_from_settings
from .store import PipelineStore, PipelineStoreError, RunRecord
from .telemetry import run_summary
from .validator import ValidationContext, validate_context, validate_structure

logger = logging.getLogger("agentos.pipelines.service")

RESUMABLE_STATES = frozenset({"interrupted", "failed", "cancelled", "budget_exceeded"})
OUTPUT_LIMIT_BYTES = 256 * 1024
MAX_PENDING_PER_TURN = 3
COMPLETED_TURN_STATES = frozenset({"completed", "completed_with_caveats"})
# Serialises the If-Match comparison and the replace of a pipeline file inside this process; the lock file next to
# the target (``<id>.yaml.lock``, not a pipeline suffix) serialises processes (WS-C10 security review, S1).
_WRITE_LOCK = threading.Lock()


class ServiceError(Exception):
    """A refusal with a stable ``code``, a pt-BR ``message`` and the HTTP ``status`` the API answers with."""

    def __init__(self, code: str, message: str, status: int = 422, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = dict(details or {})


def issue(error: PipelineError) -> dict[str, Any]:
    return {"code": error.code, "message": error.message, "line": error.line, "column": error.column, "path": error.path}


def report_dict(report: ValidationReport) -> dict[str, Any]:
    return {"errors": [issue(item) for item in report.errors], "warnings": [issue(item) for item in report.warnings]}


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class PendingStart:
    """A start the agent asked for during its turn; it runs when the turn ends (G17)."""

    pending_id: str
    user_id: str
    conversation_id: str
    turn_id: str
    workspace_root: str
    pipeline_id: str
    inputs: Mapping[str, Any]  # what the agent typed, NOT the resolved values: re-resolved after the turn (review P2)
    definition_hash: str


_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, list[PendingStart]] = {}


def pending_for(turn_id: str) -> tuple[PendingStart, ...]:
    with _PENDING_LOCK:
        return tuple(_PENDING.get(turn_id, ()))


def _take_pending(turn_id: str) -> list[PendingStart]:
    with _PENDING_LOCK:
        return _PENDING.pop(turn_id, [])


class PipelineService:
    def __init__(
        self,
        engine: Any,
        *,
        chat: Any,
        store: PipelineStore | None = None,
        approvals: ApprovalStore | None = None,
        trust_store: WorkspaceTrustStore | None = None,
        goal: GoalController | None = None,
        favorites: Callable[[str], Mapping[str, Sequence[str]]] | None = None,
        agent_names: Callable[[str, Path | None], frozenset[str] | None] | None = None,
        quota_service: Any = None,
        settings_loader: Callable[[Path | None], Any] | None = None,
        user_dir: Path | None = None,
        builtin_dir: Path | None = None,
    ) -> None:
        self.engine = engine
        self.chat = chat
        self.store = store or PipelineStore(engine)
        self.approvals = approvals or ApprovalStore()
        self.trust = trust_store or WorkspaceTrustStore()
        self._goal = goal
        self._favorites = favorites
        self._agent_names = agent_names
        self.quota_service = quota_service
        self._settings_loader = settings_loader
        self.user_dir = user_dir
        self.builtin_dir = builtin_dir

    @classmethod
    def for_store(cls, chat_store: Any, **kwargs: Any) -> PipelineService:
        from agentos.conversations.chat import ChatApplication

        return cls(chat_store._engine, chat=ChatApplication(chat_store), **kwargs)

    # ------------------------------------------------------------------ settings and context
    def effective(self, root: Path | None) -> Any:
        if self._settings_loader is not None:
            return self._settings_loader(root)
        from agentos.settings.loader import load_effective_settings

        return load_effective_settings(root)

    def settings_for(self, root: Path | None) -> PipelineSettings:
        return PipelineSettings.from_effective(self.effective(root))

    def _require_enabled(self, root: Path | None) -> PipelineSettings:
        settings = self.settings_for(root)
        if not settings.enabled:
            raise ServiceError("E_DISABLED", "Os pipelines estão desligados (`pipelines.enabled`).", 409)
        return settings

    def favorites_for(self, user_id: str) -> Mapping[str, Sequence[str]]:
        if self._favorites is not None:
            return self._favorites(user_id)
        from agentos.persistence.postgres.schema import provider_model_catalog, provider_model_favorites
        from agentos.persistence.postgres.user_agents import enabled_providers

        catalog, fav = provider_model_catalog, provider_model_favorites
        try:
            with self.engine.connect() as connection:
                allowed = enabled_providers(connection, user_id)
                rows = connection.execute(
                    select(catalog.c.provider, catalog.c.model_id)
                    .join(fav, (fav.c.user_id == catalog.c.user_id) & (fav.c.provider == catalog.c.provider) & (fav.c.model_id == catalog.c.model_id))
                    .where(catalog.c.user_id == user_id)
                    .order_by(catalog.c.display_name, catalog.c.model_id)
                ).all()
        except Exception:  # noqa: BLE001 - no catalog means no favorites, never a failed listing
            logger.warning("could not read the favorite models", exc_info=True)
            return {}
        grouped: dict[str, list[str]] = {}
        for row in rows:
            provider, model = str(row.provider or ""), str(row.model_id or "")
            if provider in allowed and model and not model.lower().endswith(":batch") and model not in grouped.setdefault(provider, []):
                grouped[provider].append(model)
        return grouped

    def _header(self, user_id: str, conversation_id: str | None) -> Mapping[str, Any]:
        if not conversation_id:
            return {}
        try:
            return dict(self.chat.store.get_header(conversation_id, user_id))
        except LookupError:
            raise
        except Exception:  # noqa: BLE001 - the header only adds hints (provider, mode)
            return {}

    def _known_agents(self, user_id: str, root: Path | None, trusted: bool) -> frozenset[str] | None:
        if self._agent_names is not None:
            return self._agent_names(user_id, root)
        try:
            from agentos.agentic.agent_definitions import load_agent_definitions
            from agentos.persistence.postgres.user_agents import UserAgentStore

            from agentos.agentic.session import _BUILTIN_SUBAGENT_SPECS  # the builtin types are not files: explore, plan, general, fork

            names = set(load_agent_definitions(workspace_root=root, trusted=trusted).agents) | {"general-purpose", *_BUILTIN_SUBAGENT_SPECS}
            names |= {definition.name for definition in UserAgentStore(self.engine).definitions(user_id)}
            return frozenset(names)
        except Exception:  # noqa: BLE001 - an unreadable agent list skips the check; the turn checks again
            logger.warning("could not list the agents for validation", exc_info=True)
            return None

    def validation_context(self, user_id: str, root: Path | None, *, provider: str | None = None) -> ValidationContext:
        from agentos.settings.registry import resolve

        effective = self.effective(root)
        favorites = self.favorites_for(user_id)
        models = {str(name): frozenset(ids) for name, ids in favorites.items() if ids}
        if provider:
            models.setdefault(provider, frozenset())
        try:
            worktree = bool(resolve(effective, "orchestration.worktree.enabled").value)
        except Exception:  # noqa: BLE001
            worktree = False
        is_git = False
        if root is not None:
            try:
                from agentos.code_mode.agent_worktree import AgentWorktreeManager

                is_git = bool(AgentWorktreeManager(root).is_git_repo())
            except Exception:  # noqa: BLE001
                is_git = False

        def efforts(provider_name: str, model: str) -> tuple[str, ...] | None:
            from agentos.agentic.effort_levels import levels_for

            try:
                return tuple(levels_for(provider_name, model).levels) or None
            except Exception:  # noqa: BLE001
                return None

        trusted = root is not None and self.trust.is_trusted(user_id, root)
        return ValidationContext(
            pipelines_enabled=PipelineSettings.from_effective(effective).enabled,
            known_agents=self._known_agents(user_id, root, trusted),
            models=models or None,
            supported_efforts=efforts,
            worktree_enabled=worktree,
            is_git=is_git,
            verifier_available=True,
            default_provider=provider,
        )

    # ------------------------------------------------------------------------- discovery
    def _discover(self, user_id: str, root: Path | None, *, enabled: bool = True):  # noqa: ANN202
        return discover(
            user_id, root, trust_store=self.trust, enabled=enabled, builtin_dir=self.builtin_dir, user_dir=self.user_dir
        )

    def _entry(self, user_id: str, root: Path | None, pipeline_id: str) -> DiscoveryEntry:
        for entry in self._discover(user_id, root).entries:
            if entry.id == pipeline_id:
                return entry
        raise ServiceError("PIPELINE_NOT_FOUND", f"O pipeline `{pipeline_id}` não existe (ou é de uma pasta não confiável).", 404)

    def _usable(self, entry: DiscoveryEntry) -> PipelineDefinition:
        if entry.definition is None or entry.errors:
            raise ServiceError(
                "PIPELINE_INVALID", f"O pipeline `{entry.id}` tem erros e não pode rodar.", 422,
                {"errors": [issue(item) for item in entry.errors]},
            )
        return entry.definition

    def _entry_dict(self, user_id: str, root: Path | None, entry: DiscoveryEntry) -> dict[str, Any]:
        definition = entry.definition
        out: dict[str, Any] = {
            "id": entry.id, "source": entry.source, "definition_hash": entry.definition_hash, "valid": definition is not None and not entry.errors,
            "errors": [issue(item) for item in entry.errors], "warnings": [issue(item) for item in entry.warnings],
            "name": definition.name if definition else entry.id, "description": definition.description if definition else "",
            "inputs": [], "steps": [], "approval_required": entry.source == "project", "approved": entry.source != "project",
            "agent_approved": entry.source == "builtin", "gate_commands": 0,
        }
        if definition is None:
            return out
        whole = whole_command_inputs(definition)
        program = program_inputs(definition)  # the whole command or its first word: only the person types these
        out["inputs"] = [
            {"name": spec.name, "type": spec.type, "required": spec.required, "default": spec.default,
             "max_length": spec.max_length, "agent_settable": spec.name not in program, "whole_command": spec.name in whole,
             "selects_program": spec.name in program}
            for spec in definition.inputs
        ]
        out["steps"] = [{"id": step.id, "name": step.name, "kind": step.kind} for step in definition.steps]
        out["gate_commands"] = len(ApprovalStore.commands_for_review(definition).commands)
        out["approved"] = self.approvals.is_approved(user_id, root, definition)
        out["agent_approved"] = self.approvals.is_approved(user_id, root, definition, for_agent=True)
        return out

    def list_pipelines(self, user_id: str, root: Path | None) -> dict[str, Any]:
        if not self.settings_for(root).enabled:
            return {"enabled": False, "pipelines": [], "shadowed": [], "project_skipped": 0, "errors": [issue(DiscoveryDisabled().error)]}
        result = self._discover(user_id, root)
        return {
            "enabled": True,
            "pipelines": [self._entry_dict(user_id, root, entry) for entry in result.entries],
            "shadowed": [{"id": e.id, "source": e.source, "definition_hash": e.definition_hash} for e in result.shadowed],
            "project_skipped": result.project_skipped,
            "errors": [],
        }

    def get_pipeline(self, user_id: str, root: Path | None, pipeline_id: str, *, provider: str | None = None) -> dict[str, Any]:
        self._require_enabled(root)
        entry = self._entry(user_id, root, pipeline_id)
        out = self._entry_dict(user_id, root, entry)
        # the text discovery validated; empty for a link out of the folder, a file too large or not UTF-8 (review P3)
        out["text"] = entry.text
        out["context"] = {"errors": [], "warnings": []}
        if entry.definition is not None:
            out["review"] = review_dict(entry.definition)
            context = validate_context(entry.definition, self.validation_context(user_id, root, provider=provider))
            out["context"] = report_dict(context)
        return out

    def validate_text(self, user_id: str, root: Path | None, text: str, *, source: str = "user", provider: str | None = None) -> dict[str, Any]:
        if len(text.encode("utf-8")) > _paths.MAX_FILE_BYTES:
            return {"valid": False, **report_dict(ValidationReport((PipelineError("E_TOO_LARGE", "o arquivo passa de 256 KB"),)))}
        definition, report = validate_structure(text, source)
        if definition is not None:
            report = report.merged(validate_context(definition, self.validation_context(user_id, root, provider=provider)))
        return {"valid": report.ok, "id": definition.id if definition else None, **report_dict(report)}

    # ------------------------------------------------------------------------ file writes
    def _target_dir(self, user_id: str, root: Path | None, scope: str) -> Path:
        if scope == "user":
            return self.user_dir if self.user_dir is not None else _paths.user_dir()
        if scope != "project":
            raise ServiceError("PIPELINE_SCOPE_INVALID", "O escopo deve ser `user` ou `project`.", 422)
        if root is None or not self.trust.is_trusted(user_id, root):
            raise ServiceError("PIPELINE_UNTRUSTED", "A pasta do projeto não é confiável: confie nela para gravar pipelines do projeto.", 409)
        folder = _paths.project_dir(root)
        if not folder.parent.resolve().is_relative_to(Path(root).resolve()) or (folder.exists() and not folder.resolve().is_relative_to(Path(root).resolve())):
            raise ServiceError("PIPELINE_PATH_INVALID", "A pasta de pipelines do projeto aponta para fora do projeto.", 422)
        return folder

    @staticmethod
    def _existing_file(folder: Path, pipeline_id: str) -> Path | None:
        for suffix in _paths.SUFFIXES:
            candidate = folder / f"{pipeline_id}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _existing_hash(folder: Path, existing: Path) -> str | None:
        """The hash of the file on disk read through the discovery reader, ``None`` when it is too large or not UTF-8.

        A link that leaves the folder is refused outright: neither its content nor its hash may come back (review P3).
        """
        text, problem = read_checked(existing, folder.resolve())
        if problem is None:
            return compute_definition_hash(text)
        if problem.code == "E_YAML_FORBIDDEN":
            raise ServiceError("PIPELINE_PATH_INVALID", "O arquivo atual é um atalho para fora da pasta de pipelines; corrija-o fora do Orin.", 422)
        return None

    @staticmethod
    @contextmanager
    def _write_lock(folder: Path, pipeline_id: str) -> Iterator[None]:
        try:
            with _WRITE_LOCK, _file_lock(folder / f"{pipeline_id}.yaml"):
                yield
        except SettingsWriteError as error:
            raise ServiceError("PIPELINE_BUSY", "Outro processo está gravando este pipeline. Tente de novo.", 409) from error

    def save_pipeline(
        self, user_id: str, root: Path | None, pipeline_id: str, text: str, *, scope: str, if_match: str | None = None
    ) -> dict[str, Any]:
        self._require_enabled(root)
        if not _valid_id(pipeline_id):
            raise ServiceError("E_ID", "O id deve ter letras minúsculas, números e `-`, até 64 caracteres.", 422)
        report = self.validate_text(user_id, root, text, source=scope)
        if report["errors"] or report["id"] != pipeline_id:
            errors = report["errors"] or [issue(PipelineError("E_ID", f"o `id` do arquivo deve ser `{pipeline_id}`", None, None, "id"))]
            raise ServiceError("PIPELINE_INVALID", "O pipeline tem erros e não foi gravado.", 422, {"errors": errors})
        folder = self._target_dir(user_id, root, scope)
        folder.mkdir(parents=True, exist_ok=True)
        with self._write_lock(folder, pipeline_id):  # the If-Match comparison and the replace happen as one step (S1)
            existing = self._existing_file(folder, pipeline_id)
            if existing is not None:
                current = self._existing_hash(folder, existing)
                if current is None:
                    raise ServiceError(
                        "PIPELINE_FILE_UNREADABLE", "O arquivo atual passa de 256 KB ou não está em UTF-8; apague-o ou corrija-o fora do Orin.", 422
                    )
                if if_match != current:
                    raise ServiceError("PIPELINE_CONFLICT", "O arquivo mudou (ou já existe). Recarregue antes de gravar.", 409, {"current_hash": current})
            elif if_match:
                raise ServiceError("PIPELINE_CONFLICT", "O arquivo não existe mais.", 409)
            target = existing or folder / f"{pipeline_id}.yaml"
            temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                temporary.write_text(text, encoding="utf-8", newline="\n")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return self.get_pipeline(user_id, root, pipeline_id)

    def delete_pipeline(self, user_id: str, root: Path | None, pipeline_id: str, *, scope: str, if_match: str | None = None) -> None:
        if not _valid_id(pipeline_id):
            raise ServiceError("E_ID", "Id de pipeline inválido.", 422)
        folder = self._target_dir(user_id, root, scope)
        existing = self._existing_file(folder, pipeline_id)
        if existing is None:
            raise ServiceError("PIPELINE_NOT_FOUND", f"O pipeline `{pipeline_id}` não existe neste escopo.", 404)
        with self._write_lock(folder, pipeline_id):
            existing = self._existing_file(folder, pipeline_id)
            if existing is None:
                raise ServiceError("PIPELINE_NOT_FOUND", f"O pipeline `{pipeline_id}` não existe neste escopo.", 404)
            current = self._existing_hash(folder, existing)  # also refuses a link that leaves the folder
            if if_match is not None and if_match != current:
                raise ServiceError("PIPELINE_CONFLICT", "O arquivo mudou. Recarregue antes de apagar.", 409, {"current_hash": current or ""})
            existing.unlink()

    # ---------------------------------------------------------------------------- approval
    def _approvable(self, user_id: str, root: Path | None, pipeline_id: str, definition_hash: str) -> PipelineDefinition:
        self._require_enabled(root)
        definition = self._usable(self._entry(user_id, root, pipeline_id))
        if definition.source == "builtin":
            raise ServiceError("PIPELINE_APPROVAL_NOT_NEEDED", "Pipelines prontos não precisam de aprovação.", 422)
        if definition.definition_hash != definition_hash:
            raise ServiceError("PIPELINE_CHANGED", "O arquivo mudou depois que você revisou os comandos. Revise de novo.", 409,
                               {"current_hash": definition.definition_hash})
        return definition

    def approve(self, user_id: str, root: Path | None, pipeline_id: str, definition_hash: str) -> dict[str, Any]:
        definition = self._approvable(user_id, root, pipeline_id, definition_hash)
        self.approvals.approve(user_id, root, definition)
        return {"pipeline_id": pipeline_id, "definition_hash": definition.definition_hash, "approved": True}

    def revoke(self, user_id: str, root: Path | None, pipeline_id: str, definition_hash: str) -> dict[str, Any]:
        definition = self._approvable(user_id, root, pipeline_id, definition_hash)
        self.approvals.revoke(user_id, root, definition)
        return {"pipeline_id": pipeline_id, "definition_hash": definition.definition_hash, "approved": False}

    # ------------------------------------------------------------------- preparing a start
    def _prepare(
        self,
        user_id: str,
        conversation_id: str,
        root: Path | None,
        pipeline_id: str,
        supplied: Mapping[str, Any] | None,
        *,
        from_agent: bool = False,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        settings = self._require_enabled(root)
        if from_agent and not settings.agent_may_start:
            raise ServiceError("PIPELINES_AGENT_START_DISABLED", "O agente não pode iniciar pipelines (`pipelines.agentMayStart` está desligado).", 403)
        entry = self._entry(user_id, root, pipeline_id)
        definition = self._usable(entry)
        if expected_hash is not None and definition.definition_hash != expected_hash:
            raise ServiceError("PIPELINE_CHANGED", "O pipeline mudou depois do pedido.", 409)
        if not self.approvals.is_approved(user_id, root, definition, for_agent=from_agent):
            raise ServiceError(
                "PIPELINE_NOT_APPROVED",
                f"O pipeline `{pipeline_id}` ({definition.source}) ainda não foi aprovado: revise os comandos de gate e aprove.",
                409,
                {"review": review_dict(definition), "source": definition.source},
            )
        header = self._header(user_id, conversation_id)
        provider, model = str(header.get("provider") or ""), str(header.get("model_id") or "")
        if str(header.get("permission_mode") or "") == "plan_mode":
            raise ServiceError("PIPELINE_PLAN_MODE", "Este pipeline não roda no modo de planejamento (somente leitura).", 409)
        context = validate_context(definition, self.validation_context(user_id, root, provider=provider or None))
        if not context.ok:
            raise ServiceError("PIPELINE_INVALID", "O pipeline não passou na validação.", 422, {"errors": [issue(i) for i in context.errors]})
        favorites = self.favorites_for(user_id)
        try:
            resolved = resolve_inputs(
                definition, supplied, from_agent=from_agent, favorites=favorites,
                catalog=FavoritesModelCatalog(favorites, tiers=None, rejected=self._rejected_probe(user_id, root)),
                author=ModelRef(provider, model) if provider and model else None,
            )
        except InputError as error:
            raise ServiceError(error.code, error.message, 422, {"input": error.name}) from error
        return {
            "definition": definition, "entry": entry, "resolved": resolved, "settings": settings, "header": header,
            "provider": provider, "permission_mode": str(header.get("permission_mode") or ""),
        }

    def _rejected_probe(self, user_id: str, root: Path | None) -> Callable[[str, str], bool] | None:
        if self.quota_service is None:
            return None
        effective = self.effective(root)
        try:
            from agentos.quota.policy import QuotaPolicy

            policy = QuotaPolicy(self.quota_service)
        except Exception:  # noqa: BLE001
            return None

        def rejected(provider: str, _model: str) -> bool:
            try:
                return not policy.check_background(user_id, provider, settings=effective).allowed
            except Exception:  # noqa: BLE001 - quota is advice
                return False

        return rejected

    def _quota(self, user_id: str, root: Path | None, providers: Sequence[str]) -> dict[str, Any]:
        if self.quota_service is None:
            return {"available": False, "warn": [], "blocked": []}
        from .adapters.catalog import SessionQuota

        try:
            from agentos.quota.policy import QuotaPolicy

            assessed = SessionQuota(QuotaPolicy(self.quota_service), user_id=user_id, settings=self.effective(root)).assess(providers)
        except Exception:  # noqa: BLE001
            logger.warning("could not assess the quota", exc_info=True)
            return {"available": False, "warn": [], "blocked": []}
        return {"available": True, "warn": list(assessed.warn), "blocked": list(assessed.blocked)}

    @staticmethod
    def _effective_budget(definition: PipelineDefinition, ceiling: Any) -> dict[str, Any]:
        from . import schema_v1 as sv

        default = Budget(sv.DEFAULT_BUDGET_TOKENS, sv.DEFAULT_BUDGET_COST_USD, sv.DEFAULT_BUDGET_MINUTES)
        declared = definition.budget
        is_default = declared is None or declared.is_empty
        asked = default if is_default else declared  # the default ceiling (G8) is lowered by the user's ceiling too
        capped = cap_budget_to_ceiling(definition, ceiling).budget
        budget = asked if capped is None or capped.is_empty else capped
        return {"max_tokens": budget.max_tokens, "max_cost_usd": budget.max_cost_usd, "max_minutes": budget.max_minutes,
                "is_default": is_default, "lowered_by_user_ceiling": budget != asked}

    def estimate(self, user_id: str, conversation_id: str | None, root: Path | None, pipeline_id: str, supplied: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Who would spend what: steps, models, subscriptions, parallelism and budget. Never a token estimate (R-C11b)."""
        prepared = self._prepare(user_id, conversation_id or "", root, pipeline_id, supplied)
        definition: PipelineDefinition = prepared["definition"]
        resolved = prepared["resolved"]
        default_provider = prepared["provider"] or definition.defaults.provider
        steps: list[dict[str, Any]] = []
        pairs: dict[tuple[str, str], list[str]] = {}
        max_parallel = 1
        for step in definition.steps:
            row: dict[str, Any] = {"id": step.id, "name": step.name, "kind": step.kind}
            if step.kind == "agent":
                provider = step.provider or definition.defaults.provider or default_provider
                model = step.model or definition.defaults.model
                parallel = step.map.max_parallel if step.map is not None else 1
                max_parallel = max(max_parallel, parallel)
                row.update(agent=step.agent, provider=provider, model=model, effort=step.effort or definition.defaults.effort,
                           isolation=step.isolation, map=step.map is not None, max_parallel=parallel)
                if "${{" in str(provider or "") + str(model or ""):
                    for models in resolved.models.values():
                        for ref in models:
                            pairs.setdefault((ref["provider"], ref["model"]), []).append(step.id)
                elif provider:
                    pairs.setdefault((str(provider), str(model or "")), []).append(step.id)
            steps.append(row)
        providers = sorted({provider for provider, _ in pairs})
        permission = prepared["permission_mode"]
        notices = ["Isto lista os modelos e as assinaturas envolvidos; não é uma estimativa de tokens."]
        if permission in {"", "ask_permissions", "default"}:
            notices.append("O pipeline pode pedir aprovações durante a execução; considere o modo Aceitar edições.")
        return {
            "pipeline_id": pipeline_id, "definition_hash": definition.definition_hash, "source": definition.source, "steps": steps,
            "subscriptions": [{"provider": p, "model": m, "steps": s} for (p, m), s in sorted(pairs.items())],
            "max_parallel": max_parallel, "budget": self._effective_budget(definition, ceiling_from_settings(self.effective(root))),
            "quota": self._quota(user_id, root, providers), "inputs": resolved.values,
            "degraded": [asdict(note) for note in resolved.degraded], "permission_mode": permission, "notices": notices,
        }

    # --------------------------------------------------------------------------- starting
    def _emit(self, turn_id: str, event: Any) -> None:
        try:
            from .goal import StoreGoalEvidence

            turn = StoreGoalEvidence(self.chat.store).turn_row(turn_id)
            if turn is not None:
                self.chat.store._activity(dict(turn), event.type, event.summary, dict(event.payload))
        except Exception:  # noqa: BLE001 - an event is advice, never a reason to lose a started run
            logger.warning("could not record %s", getattr(event, "type", "event"), exc_info=True)

    def start(
        self,
        user_id: str,
        conversation_id: str,
        root: Path | None,
        pipeline_id: str,
        supplied: Mapping[str, Any] | None = None,
        *,
        from_agent: bool = False,
        expected_hash: str | None = None,
    ) -> dict[str, Any]:
        prepared = self._prepare(user_id, conversation_id, root, pipeline_id, supplied, from_agent=from_agent, expected_hash=expected_hash)
        definition: PipelineDefinition = prepared["definition"]
        entry: DiscoveryEntry = prepared["entry"]
        resolved = prepared["resolved"]
        text = entry.text  # the text discovery validated and hashed (review P3): the snapshot is what was approved
        if not text or compute_definition_hash(text) != definition.definition_hash:
            raise ServiceError("PIPELINE_CHANGED", "O arquivo mudou durante a preparação. Tente de novo.", 409)
        run_id = f"run-{uuid.uuid4().hex[:20]}"
        self.store.create_run(
            run_id=run_id, user_id=user_id, conversation_id=conversation_id, workspace_root=str(root) if root else "",
            pipeline_id=pipeline_id, definition_hash=definition.definition_hash, source=definition.source,
            inputs=resolved.values, definition_snapshot=text,
        )
        try:
            receipt = self.chat.send_pipeline(user_id, conversation_id, run_id, attempt=1)
        except Exception:
            self.store.set_run_state(run_id, "cancelled", stop_reason="send_failed")
            raise
        turn_id = str(getattr(receipt, "turn_id", "") or "")
        if turn_id:
            self.store.bind_turn(run_id, turn_id)
            for note in resolved.degraded:
                self._emit(turn_id, pipeline_events.degraded(run_id=run_id, step_id=None, reason=note.reason, detail=note.detail, effect=note.effect))
        providers = sorted({ref["provider"] for models in resolved.models.values() for ref in models} | ({prepared["provider"]} if prepared["provider"] else set()))
        return {
            "run_id": run_id, "turn_id": turn_id, "pipeline_id": pipeline_id, "state": "running", "inputs": resolved.values,
            "degraded": [asdict(note) for note in resolved.degraded],
            "budget": self._effective_budget(definition, ceiling_from_settings(self.effective(root))),
            "quota": self._quota(user_id, root, providers),
        }

    # ------------------------------------------------------------------------------ runs
    def _own_run(self, user_id: str, run_id: str, conversation_id: str | None = None) -> RunRecord:
        run = self.store.get_run(run_id)
        if run is None or run.user_id != user_id or run.pipeline_id == GOAL_PIPELINE_ID or (conversation_id and run.conversation_id != conversation_id):
            raise ServiceError("PIPELINE_RUN_NOT_FOUND", f"A execução `{run_id}` não existe.", 404)
        return run

    @staticmethod
    def run_dict(run: RunRecord) -> dict[str, Any]:
        return {
            "run_id": run.run_id, "pipeline_id": run.pipeline_id, "source": run.source, "state": run.state, "stop_reason": run.stop_reason,
            "conversation_id": run.conversation_id, "turn_id": run.turn_id, "definition_hash": run.definition_hash, "inputs": dict(run.inputs),
            "used_tokens": run.used_tokens, "used_cost_usd": _float(run.used_cost_usd), "used_seconds": run.used_seconds,
            "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
            "resumable": run.state in RESUMABLE_STATES, "needs_budget_override": run.state == "budget_exceeded",
            "live_here": run.run_id in active_run_ids(),
        }

    def list_runs(self, user_id: str, *, conversation_id: str | None = None, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        if conversation_id:
            runs = self.store.list_runs(conversation_id, limit=limit * 2)
        else:
            statement = select(pipeline_runs.c.run_id).where(pipeline_runs.c.user_id == user_id).order_by(pipeline_runs.c.started_at.desc()).limit(limit * 2)
            with self.engine.connect() as connection:
                ids = [row[0] for row in connection.execute(statement)]
            runs = [run for run in (self.store.get_run(item) for item in ids) if run is not None]
        kept = [r for r in runs if r.user_id == user_id and r.pipeline_id != GOAL_PIPELINE_ID and (state is None or r.state == state)]
        return [self.run_dict(run) for run in kept[:limit]]

    def get_run(self, user_id: str, run_id: str, conversation_id: str | None = None) -> dict[str, Any]:
        run = self._own_run(user_id, run_id, conversation_id)
        steps = [
            {"step_id": s.step_id, "item_key": s.item_key, "attempt": s.attempt, "state": s.state, "agent_id": s.agent_id,
             "loop_count": s.loop_count, "input_tokens": s.input_tokens, "output_tokens": s.output_tokens, "cost_usd": _float(s.cost_usd),
             "error": s.error, "has_output": s.output is not None,
             "output_ref": s.output["ref"] if isinstance(s.output, Mapping) and set(s.output) == {"ref"} else None,
             "started_at": _iso(s.started_at), "finished_at": _iso(s.finished_at)}
            for s in self.store.steps_of(run_id)
        ]
        return {**self.run_dict(run), "steps": steps, "summary": run_summary(self.store, run_id).to_dict()}

    def step_output(self, user_id: str, run_id: str, step_id: str, item_key: str = "", attempt: int | None = None) -> dict[str, Any]:
        import json

        self._own_run(user_id, run_id)
        rows = [s for s in self.store.steps_of(run_id) if s.step_id == step_id and s.item_key == item_key and (attempt is None or s.attempt == attempt)]
        if not rows:
            raise ServiceError("PIPELINE_STEP_NOT_FOUND", "Esta etapa não tem execução registrada.", 404)
        row = max(rows, key=lambda item: item.attempt)
        try:
            value, _worktree = unwrap_envelope(self.store.load_output(run_id, row.output))
        except PipelineStoreError as error:
            raise ServiceError("PIPELINE_OUTPUT_UNAVAILABLE", "A saída desta etapa não está mais disponível.", 410) from error
        text = json.dumps(value, ensure_ascii=False)
        base = {"step_id": step_id, "item_key": item_key, "attempt": row.attempt, "state": row.state, "bytes": len(text.encode("utf-8"))}
        if base["bytes"] > OUTPUT_LIMIT_BYTES:
            return {**base, "output": None, "truncated": True, "preview": text.encode("utf-8")[:OUTPUT_LIMIT_BYTES].decode("utf-8", "ignore")}
        return {**base, "output": value, "truncated": False}

    # ------------------------------------------------------------------ pause, cancel, resume
    def pause(self, user_id: str, run_id: str) -> dict[str, Any]:
        run = self._own_run(user_id, run_id)
        if run.state != "running":
            raise ServiceError("PIPELINE_NOT_RUNNING", f"A execução está `{run.state}` e não pode ser pausada.", 409)
        if pause_run(run_id):
            return {"run_id": run_id, "pause_requested": True, "where": "this_process"}
        with self.engine.begin() as connection:  # the worker is another process: its driver polls this flag
            connection.execute(
                update(pipeline_runs).where(pipeline_runs.c.run_id == run_id, pipeline_runs.c.state == "running").values(stop_reason=PAUSE_REQUESTED)
            )
        return {"run_id": run_id, "pause_requested": True, "where": "worker"}

    def cancel(self, user_id: str, run_id: str) -> dict[str, Any]:
        run = self._own_run(user_id, run_id)
        if run.state == "running":
            if cancel_run(run_id):
                return {"run_id": run_id, "cancel_requested": True, "where": "this_process"}
            self.chat.cancel(run.conversation_id, user_id)  # the pipeline turn is what occupies the conversation
            return {"run_id": run_id, "cancel_requested": True, "where": "worker"}
        if run.state == "interrupted":
            self.store.set_run_state(run_id, "cancelled", stop_reason="cancelled")
            return {"run_id": run_id, "cancel_requested": True, "where": "journal"}
        raise ServiceError("PIPELINE_NOT_CANCELLABLE", f"A execução está `{run.state}` e não pode ser cancelada.", 409)

    def _current_hash(self, user_id: str, root: Path | None, pipeline_id: str) -> str | None:
        try:
            return self._entry(user_id, root, pipeline_id).definition_hash
        except ServiceError:
            return None

    def resume(
        self, user_id: str, run_id: str, *, definition: str | None = None, rerun_gates: bool = False, budget_override: bool = False
    ) -> dict[str, Any]:
        """Continue a stopped run from its journal. ``definition``: ``original`` (the snapshot) or ``new_run`` when the file changed."""
        run = self._own_run(user_id, run_id)
        root = Path(run.workspace_root) if run.workspace_root else None
        self._require_enabled(root)
        if run.state not in RESUMABLE_STATES:
            raise ServiceError("PIPELINE_NOT_RESUMABLE", f"A execução está `{run.state}` e não pode ser retomada.", 409)
        if run.state == "budget_exceeded" and not budget_override:
            raise ServiceError("PIPELINE_BUDGET_OVERRIDE_REQUIRED", "O orçamento acabou. Para continuar mesmo assim, confirme o novo gasto (o contador recomeça do zero).", 409)
        if not run.definition_snapshot.strip():
            raise ServiceError("PIPELINE_SNAPSHOT_MISSING", "A execução não guardou a definição do pipeline e não pode ser retomada.", 422)
        current = self._current_hash(user_id, root, run.pipeline_id)
        if current != run.definition_hash:
            if definition == "new_run":
                if current is None:
                    raise ServiceError("PIPELINE_NOT_FOUND", f"O pipeline `{run.pipeline_id}` não existe mais.", 404)
                return self.start(user_id, run.conversation_id, root, run.pipeline_id, dict(run.inputs))
            if definition != "original":
                options = [{"id": "original", "label": "Retomar com a definição original (a do início da execução)"}]
                if current is not None:
                    options.append({"id": "new_run", "label": "Iniciar uma nova execução com a definição atual"})
                raise ServiceError(
                    "PIPELINE_DEFINITION_CHANGED", "A definição do pipeline mudou desde o início da execução.", 409,
                    {"options": options, "stored_hash": run.definition_hash, "current_hash": current},
                )
        snapshot, report = validate_structure(run.definition_snapshot, run.source)
        if snapshot is None:
            raise ServiceError("PIPELINE_INVALID", "A definição guardada não é mais válida.", 422, {"errors": [issue(i) for i in report.errors]})
        if snapshot.source == "project" and (root is None or not self.trust.is_trusted(user_id, root) or not self.approvals.is_approved(user_id, root, snapshot)):
            raise ServiceError("PIPELINE_NOT_APPROVED", "O pipeline do projeto não está mais aprovado nesta pasta.", 409, {"review": review_dict(snapshot)})
        receipt = self.chat.send_pipeline(
            user_id, run.conversation_id, run_id, resume=True, rerun_gates=bool(rerun_gates), budget_override=bool(budget_override), attempt=int(time.time()),
        )
        turn_id = str(getattr(receipt, "turn_id", "") or "")
        if turn_id:
            self.store.bind_turn(run_id, turn_id)
        return {"run_id": run_id, "turn_id": turn_id, "state": run.state, "resumed": True, "definition": definition or "same"}

    # ------------------------------------------------------------------------------ /goal
    def goals(self, root: Path | None = None) -> GoalController:
        controller = self._goal or GoalController.from_store(self.chat.store)
        controller.deps.default_max_tokens = self.settings_for(root).goal_default_max_tokens
        return controller

    def goal_status(self, user_id: str, conversation_id: str, root: Path | None = None) -> dict[str, Any]:
        status = self.goals(root).status(conversation_id, user_id)
        return {"goal": status.to_dict() if status else None}

    def goal_start(self, user_id: str, conversation_id: str, root: Path | None, objective: str, max_tokens: int | None = None) -> dict[str, Any]:
        started = self.goals(root).start(
            user_id=user_id, conversation_id=conversation_id, objective=objective, max_tokens=max_tokens, workspace_root=str(root) if root else ""
        )
        return {"run_id": started.run_id, "turn_id": started.turn_id, "max_tokens": started.max_tokens}

    def goal_clear(self, user_id: str, conversation_id: str, root: Path | None = None, *, cancel_turn: bool = False) -> dict[str, Any]:
        """``/goal clear`` ends the objective but, by decision of the C10, does NOT cancel the turn in progress.

        The turn is ordinary conversation work the person may want to keep; when it ends the hook finds no goal, so
        nothing is evaluated or continued. ``cancel_turn=True`` (the interface's "clear and stop") also cancels it.
        """
        status = self.goals(root).clear(conversation_id, user_id)
        if status is None:
            raise GoalError("GOAL_NOT_FOUND", "Não há objetivo ativo ou parado nesta conversa.", 404)
        if cancel_turn:
            self.chat.cancel(conversation_id, user_id)
        return {"goal": status.to_dict(), "turn_cancelled": bool(cancel_turn)}

    def goal_resume(self, user_id: str, conversation_id: str, root: Path | None = None, max_tokens: int | None = None) -> dict[str, Any]:
        return {"goal": self.goals(root).resume(user_id=user_id, conversation_id=conversation_id, max_tokens=max_tokens).to_dict()}

    # ------------------------------------------------------------------- RunPipeline (G17)
    def agent_startable(self, user_id: str, root: Path | None) -> list[dict[str, Any]]:
        """The pipelines an agent may start: builtin, or user/project already approved by hash for the agent."""
        self._require_enabled(root)
        listed = self.list_pipelines(user_id, root)["pipelines"]
        return [
            {"id": p["id"], "name": p["name"], "description": p["description"], "source": p["source"],
             "inputs": [i for i in p["inputs"]]}
            for p in listed if p["valid"] and p["agent_approved"]
        ]

    def request_agent_start(
        self, user_id: str, conversation_id: str, turn_id: str, root: Path | None, pipeline_id: str, supplied: Mapping[str, Any] | None
    ) -> tuple[PendingStart, dict[str, Any]]:
        """Validate now, register a pending start; nothing runs inside the agent's turn."""
        prepared = self._prepare(user_id, conversation_id, root, pipeline_id, supplied, from_agent=True)
        definition: PipelineDefinition = prepared["definition"]
        with _PENDING_LOCK:
            queue = _PENDING.setdefault(turn_id, [])
            if len(queue) >= MAX_PENDING_PER_TURN:
                raise ServiceError("PIPELINE_PENDING_LIMIT", f"No máximo {MAX_PENDING_PER_TURN} pipelines por turno.", 429)
            pending = PendingStart(
                f"pending-{uuid.uuid4().hex[:12]}", user_id, conversation_id, turn_id, str(root) if root else "", pipeline_id,
                # the agent's own inputs: resolving again after the turn keeps the defaults of program inputs and the
                # `auto` notes (pipeline.degraded) instead of refusing them as typed by the agent (review P2)
                dict(supplied or {}), definition.definition_hash,
            )
            queue.append(pending)
        providers = sorted({r["provider"] for models in prepared["resolved"].models.values() for r in models})
        return pending, {
            "inputs": dict(prepared["resolved"].values),  # what will run; the pending start keeps the agent's own inputs
            "budget": self._effective_budget(definition, ceiling_from_settings(self.effective(root))),
            "providers": providers, "degraded": [asdict(n) for n in prepared["resolved"].degraded],
        }

    def run_pending(self, turn: Mapping[str, Any], result_state: str) -> list[dict[str, Any]]:
        """The after-turn hook: start what the agent asked for, only when the turn ended well."""
        items = _take_pending(str(turn.get("turn_id") or ""))
        started: list[dict[str, Any]] = []
        for item in items:
            if result_state not in COMPLETED_TURN_STATES:
                logger.info("dropping pending pipeline start %s: the turn ended as %s", item.pending_id, result_state)
                continue
            root = Path(item.workspace_root) if item.workspace_root else None
            try:
                started.append(
                    self.start(item.user_id, item.conversation_id, root, item.pipeline_id, item.inputs, from_agent=True, expected_hash=item.definition_hash)
                )
            except (ServiceError, InputError) as error:
                logger.warning("pending pipeline start %s refused: %s", item.pending_id, getattr(error, "code", error))
                self._report_failed_start(turn, item, str(getattr(error, "message", error)))
        return started

    def _report_failed_start(self, turn: Mapping[str, Any], item: PendingStart, message: str) -> None:
        try:
            event = pipeline_events.degraded(run_id=item.pending_id, step_id=None, reason="agent_start_refused", detail=message[:300], effect="o pipeline não foi iniciado")
            self.chat.store._activity(dict(turn), event.type, event.summary, dict(event.payload))
        except Exception:  # noqa: BLE001
            logger.warning("could not record the refused start", exc_info=True)


def start_pending_handler(store: Any, turn: Mapping[str, Any], result_state: str) -> None:
    """Registered in ``after_turn``: start (or drop) the pipelines the agent asked for in the turn that just ended."""
    if getattr(store, "_engine", None) is None or not pending_for(str(turn.get("turn_id") or "")):
        return
    PipelineService.for_store(store).run_pending(turn, result_state)


def _valid_id(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value or ""))


def review_dict(definition: PipelineDefinition) -> dict[str, Any]:
    review = ApprovalStore.commands_for_review(definition)
    return {
        "pipeline_id": review.pipeline_id, "definition_hash": review.definition_hash, "notice": review.notice,
        "commands": [
            {"step_id": c.step_id, "command": c.command, "expect": c.expect, "timeout_seconds": c.timeout_seconds,
             "inputs": list(c.inputs), "whole_value": c.whole_value, "program_inputs": list(c.program_inputs)}
            for c in review.commands
        ],
    }
