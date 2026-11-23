"""Resolve the inputs of a run before the run exists (WS-C10; decisions G13 and G17).

``models`` inputs arrive as ``auto`` or as ``provider/model`` texts; the engine wants a list of
``{provider, model}`` objects (the contract of ``revisao-multimodelo``), so the conversion happens here, once, and the
result is what the run stores in ``inputs``: a resumed run sees the same reviewers (G13). ``auto`` picks reviewers of
different families with ``FavoritesModelCatalog.pick_reviewers`` (K21) and says so when it found fewer than asked.

An input that chooses the program of a gate command (it is the whole ``command: ${{ inputs.testes }}`` or sits in its
first word, ``command: ${{ inputs.prog }} --flag``) is "program-selecting": a value that comes from the agent is
refused and the YAML ``default`` is used instead (G17, R18; WS-C10 security review, P1 and S2).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import schema_v1 as sv
from .adapters.catalog import ModelCatalogPort, ModelRef
from .approvals import ApprovalStore
from .model import InputSpec, PipelineDefinition

MODELS_AUTO = "auto"
DEFAULT_STRING_MAX_LENGTH = 100_000
_INTEGER = re.compile(r"^-?\d{1,15}$")
MAX_INTEGER = 999_999_999_999_999  # 15 digits, for the text form and for the JSON number alike


class InputError(Exception):
    """An input the run cannot start with. ``code`` is stable; ``message`` is pt-BR and fit for the person."""

    def __init__(self, code: str, message: str, name: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.name = name


@dataclass(frozen=True, slots=True)
class DegradedNote:
    """Something a run starts with despite not getting everything asked (becomes ``pipeline.degraded``)."""

    reason: str
    detail: str
    effect: str


@dataclass(frozen=True, slots=True)
class ResolvedInputs:
    values: dict[str, Any]
    degraded: tuple[DegradedNote, ...] = ()
    models: dict[str, list[dict[str, str]]] = field(default_factory=dict)


def whole_command_inputs(definition: PipelineDefinition) -> frozenset[str]:
    """Inputs that are the entire ``command`` of a gate (a subset of ``program_inputs``)."""
    review = ApprovalStore.commands_for_review(definition)
    return frozenset(name for command in review.commands if command.whole_value for name in command.inputs)


def program_inputs(definition: PipelineDefinition) -> frozenset[str]:
    """Inputs that choose the program a gate runs (the whole command or its first word): the person types them, an agent may not."""
    review = ApprovalStore.commands_for_review(definition)
    return frozenset(name for command in review.commands for name in command.program_inputs)


def parse_model_ref(raw: Any) -> ModelRef:
    """``provider/model`` text or a ``{provider, model}`` object as a ``ModelRef``."""
    if isinstance(raw, ModelRef):
        return raw
    if isinstance(raw, Mapping):
        provider, model = str(raw.get("provider") or "").strip(), str(raw.get("model") or raw.get("model_id") or "").strip()
    elif isinstance(raw, str) and "/" in raw:
        provider, model = (part.strip() for part in raw.strip().split("/", 1))
    else:
        raise ValueError(f"`{str(raw)[:80]}` não é um modelo no formato provedor/modelo")
    if not provider or not model:
        raise ValueError(f"`{str(raw)[:80]}` não é um modelo no formato provedor/modelo")
    return ModelRef(provider, model)


def _models_value(
    name: str,
    value: Any,
    *,
    catalog: ModelCatalogPort | None,
    author: ModelRef | None,
    favorites: Mapping[str, Sequence[str]] | None,
    notes: list[DegradedNote],
    from_default: bool = False,
) -> list[dict[str, str]]:
    wanted = sv.MODELS_INPUT_MAX
    if isinstance(value, str) and value.strip().lower() == MODELS_AUTO:
        if catalog is None:
            raise InputError("PIPELINE_MODELS_UNAVAILABLE", f"A entrada `{name}` é `auto`, mas não há catálogo de modelos para escolher os revisores.", name)
        picked = catalog.pick_reviewers(wanted, author or ModelRef("", ""), True)
        if not picked:
            raise InputError(
                "PIPELINE_MODELS_UNAVAILABLE",
                f"A entrada `{name}` é `auto`, mas nenhum modelo favorito está disponível para revisar. Marque modelos favoritos ou informe `provedor/modelo`.",
                name,
            )
        if len(picked) < wanted:
            families = "uma família" if len(picked) == 1 else f"{len(picked)} famílias"
            effect = "sem consenso: há um só revisor" if len(picked) == 1 else "a revisão segue com menos revisores"
            notes.append(
                DegradedNote("models_auto_partial", f"só há {families} disponível(is) para `{name}` (pedido: {wanted})", effect)
            )
        return [{"provider": ref.provider, "model": ref.model} for ref in picked]
    items = [value] if isinstance(value, (str, Mapping)) else list(value) if isinstance(value, (list, tuple)) else None
    if items is None or not items:
        raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` deve ser `auto` ou uma lista de `provedor/modelo`.", name)
    if len(items) > wanted:
        raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` aceita até {wanted} modelos.", name)
    refs: list[ModelRef] = []
    for item in items:
        try:
            ref = parse_model_ref(item)
        except ValueError as error:
            raise InputError("PIPELINE_INPUT_INVALID", f"{error} (entrada `{name}`).", name) from error
        if favorites is not None and ref.model not in {str(m) for m in favorites.get(ref.provider, ())}:
            origin = ", valor padrão do pipeline" if from_default else ""
            raise InputError("PIPELINE_INPUT_INVALID", f"`{ref}` não é um modelo favorito (entrada `{name}`{origin}).", name)
        if ref not in refs:
            refs.append(ref)
    return [{"provider": ref.provider, "model": ref.model} for ref in refs]


def _scalar(spec: InputSpec, value: Any) -> Any:
    name = spec.name
    if spec.type == "string":
        if not isinstance(value, str):
            raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` deve ser um texto.", name)
        limit = spec.max_length or DEFAULT_STRING_MAX_LENGTH
        if len(value) > limit:
            raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` passa de {limit} caracteres.", name)
        return value
    if spec.type == "integer":
        if isinstance(value, bool):
            raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` deve ser um número inteiro.", name)
        if isinstance(value, int):
            if abs(value) > MAX_INTEGER:
                raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` passa de 15 dígitos.", name)
            return value
        if isinstance(value, str) and _INTEGER.match(value.strip()):
            return int(value.strip())
        raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` deve ser um número inteiro.", name)
    if spec.type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` deve ser verdadeiro ou falso.", name)
    raise InputError("PIPELINE_INPUT_INVALID", f"A entrada `{name}` tem um tipo desconhecido.", name)


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def resolve_inputs(
    definition: PipelineDefinition,
    supplied: Mapping[str, Any] | None,
    *,
    from_agent: bool = False,
    catalog: ModelCatalogPort | None = None,
    author: ModelRef | None = None,
    favorites: Mapping[str, Sequence[str]] | None = None,
) -> ResolvedInputs:
    """Check ``supplied`` against the declared inputs and return what the run stores, in declaration order."""
    given = dict(supplied or {})
    declared = {spec.name for spec in definition.inputs}
    unknown = sorted(str(name) for name in given if name not in declared)
    if unknown:
        raise InputError(
            "PIPELINE_INPUT_UNKNOWN",
            f"O pipeline não tem a(s) entrada(s): {', '.join(unknown)}. Entradas: {', '.join(sorted(declared)) or 'nenhuma'}.",
            unknown[0],
        )
    commands = program_inputs(definition) if from_agent else frozenset()
    values: dict[str, Any] = {}
    models: dict[str, list[dict[str, str]]] = {}
    notes: list[DegradedNote] = []
    for spec in definition.inputs:
        if spec.name in commands and spec.name in given:
            raise InputError(
                "PIPELINE_INPUT_NOT_ALLOWED",
                f"A entrada `{spec.name}` escolhe o programa de um gate (é o comando inteiro ou a primeira palavra dele): só quem inicia o pipeline pode escolhê-la. "
                "Omita-a para usar o valor padrão do pipeline.",
                spec.name,
            )
        value = given.get(spec.name) if spec.name not in commands else None
        from_default = False
        if _missing(value):
            value, from_default = spec.default, True
        if _missing(value):
            if spec.required:
                raise InputError("PIPELINE_INPUT_REQUIRED", f"A entrada `{spec.name}` é obrigatória.", spec.name)
            continue
        if spec.type == "models":
            # always checked against the favorites: the ``default`` of a file is no more trusted than a typed value
            resolved = _models_value(spec.name, value, catalog=catalog, author=author, favorites=favorites, notes=notes, from_default=from_default)
            values[spec.name] = resolved
            models[spec.name] = resolved
        else:
            values[spec.name] = _scalar(spec, value)
    return ResolvedInputs(values, tuple(notes), models)


__all__ = [
    "DegradedNote",
    "InputError",
    "MODELS_AUTO",
    "ResolvedInputs",
    "parse_model_ref",
    "program_inputs",
    "resolve_inputs",
    "whole_command_inputs",
]
