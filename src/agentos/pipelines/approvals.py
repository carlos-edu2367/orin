"""Approval of pipeline commands by file hash (spec 7.4, S3, P6).

A project pipeline runs only after the person approved the exact bytes of its file
(``definition_hash``): changing one byte asks again. User and built-in pipelines need
no approval when the *person* starts them; when an *agent* starts one (``RunPipeline``),
only built-in pipelines are free, because an agent with write permission could have
authored the user file. The store is ``<orin user dir>/pipeline-approvals.json``,
written atomically with the same helper as ``trusted-workspaces.json``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from agentos.settings.writer import SettingsWriteError, update_settings_file

from . import paths as _paths
from .command_quoting import ROLE_PROGRAM, ROLE_WHOLE, classify_references
from .expressions import ExpressionError, split_template
from .model import CommandGate, GateStep, PipelineDefinition

USER_LEVEL_KEY = "@user"


def _root_key(root: Path | str | None) -> str:
    """Same folder key as ``permissions.trust`` (resolved, case-normalized)."""
    if root is None:
        return ""
    return os.path.normcase(str(Path(root).resolve()))


def _entry_key(definition: PipelineDefinition, root: Path | str | None) -> str:
    return _root_key(root) if definition.source == "project" else USER_LEVEL_KEY


@dataclass(frozen=True, slots=True)
class CommandReview:
    step_id: str
    command: str
    expect: str
    timeout_seconds: int
    inputs: tuple[str, ...]
    whole_value: bool
    program_inputs: tuple[str, ...] = ()  # inputs that choose the program (the whole command or its first word)


@dataclass(frozen=True, slots=True)
class ReviewInfo:
    """What the interface shows before the first run of a project pipeline."""

    pipeline_id: str
    definition_hash: str
    commands: tuple[CommandReview, ...]
    notice: str


class ApprovalStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else _paths.approvals_path()

    # ------------------------------------------------------------------ reads
    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _entries(self, user_id: str) -> list[dict]:
        users = self._read().get("users")
        entries = users.get(str(user_id)) if isinstance(users, dict) else None
        return [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []

    def is_approved(
        self,
        user_id: str,
        root: Path | str | None,
        definition: PipelineDefinition,
        *,
        for_agent: bool = False,
    ) -> bool:
        if definition.source == "builtin":
            return True
        if definition.source == "user" and not for_agent:
            return True
        if definition.source == "project" and root is None:
            return False
        key = _entry_key(definition, root)
        return any(
            item.get("root_key") == key
            and item.get("pipeline_id") == definition.id
            and item.get("definition_hash") == definition.definition_hash
            for item in self._entries(user_id)
        )

    # ----------------------------------------------------------------- writes
    def _mutate(self, user_id: str, key: str, pipeline_id: str, entry: dict | None) -> None:
        def change(data: dict) -> None:
            data["version"] = 1
            users = data.get("users")
            if not isinstance(users, dict):
                users = {}
                data["users"] = users
            entries = users.get(str(user_id))
            if not isinstance(entries, list):
                entries = []
            kept = [
                item
                for item in entries
                if isinstance(item, dict) and not (item.get("root_key") == key and item.get("pipeline_id") == pipeline_id)
            ]
            if entry is not None:
                kept.append(entry)
            users[str(user_id)] = kept

        try:
            update_settings_file(self.path, change)
        except SettingsWriteError:
            if entry is None:
                raise
            # a corrupt file is replaced rather than leaving the person unable to approve
            self.path.unlink(missing_ok=True)
            update_settings_file(self.path, change)

    def approve(self, user_id: str, root: Path | str | None, definition: PipelineDefinition) -> None:
        if definition.source == "builtin":
            return
        key = _entry_key(definition, root)
        self._mutate(
            user_id,
            key,
            definition.id,
            {
                "root_key": key,
                "pipeline_id": definition.id,
                "definition_hash": definition.definition_hash,
                "approved_at": datetime.now(UTC).isoformat(),
            },
        )

    def revoke(self, user_id: str, root: Path | str | None, definition: PipelineDefinition) -> None:
        self._mutate(user_id, _entry_key(definition, root), definition.id, None)

    # ------------------------------------------------------------------ review
    @staticmethod
    def commands_for_review(definition: PipelineDefinition) -> ReviewInfo:
        commands: list[CommandReview] = []
        for step in definition.steps:
            if not isinstance(step, GateStep):
                continue
            for item in step.gate.items:
                if not isinstance(item, CommandGate):
                    continue
                names, whole, program = _inputs_in(item.command)
                commands.append(CommandReview(step.id, item.command, item.expect, item.timeout_seconds, names, whole, program))
        return ReviewInfo(definition.id, definition.definition_hash, tuple(commands), _notice(commands))


def _inputs_in(command: str) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
    """``(inputs the command uses, whether one is the whole command, inputs that choose the program)``.

    The roles come from ``command_quoting.classify_references``, the same function the gate renderer uses.
    """
    try:
        roles = classify_references(split_template(command))
    except ExpressionError:
        return (), False, ()

    def name_of(role) -> str | None:  # noqa: ANN001
        segments = role.ref.path.segments
        return segments[1] if len(segments) > 1 and segments[0] == "inputs" else None

    names = tuple(dict.fromkeys(name for role in roles if (name := name_of(role))))
    whole = any(role.role == ROLE_WHOLE for role in roles)
    program = tuple(dict.fromkeys(name for role in roles if role.role in {ROLE_WHOLE, ROLE_PROGRAM} and (name := name_of(role))))
    return names, whole, program


def _notice(commands: list[CommandReview]) -> str:
    if not commands:
        return "Este pipeline não tem nenhum comando de gate: nenhum comando de shell será executado por ele."
    lines = [
        "Ao aprovar, estes comandos poderão rodar nesta pasta, sob as suas regras de permissão. "
        "Qualquer mudança no arquivo, até em comentário, pede uma nova aprovação."
    ]
    whole = [item for item in commands if item.whole_value]
    program = {name for item in commands if not item.whole_value for name in item.program_inputs}
    inline = {name for item in commands for name in item.inputs} - program - {name for item in whole for name in item.inputs}
    if inline:
        lines.append(f"As entradas ({', '.join(sorted(inline))}) entram no comando entre aspas, como um argumento só.")
    if whole:
        names = ", ".join(sorted({name for item in whole for name in item.inputs}))
        lines.append(f"A entrada ({names}) é o comando inteiro: quem inicia o pipeline escolhe o programa; o valor é separado em palavras e cada palavra vai entre aspas.")
    if program:
        lines.append(
            f"ATENÇÃO: a entrada ({', '.join(sorted(program))}) ocupa a posição do programa (a primeira palavra do comando): "
            "quem inicia o pipeline escolhe o que será executado. O agente não pode preencher essa entrada."
        )
    return " ".join(lines)


__all__ = ["ApprovalStore", "CommandReview", "ReviewInfo"]
