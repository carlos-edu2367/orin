"""The four pipelines that ship with Orin (spec 7.5, C-RF5), read through the discovery reader.

There is no second way of reading a pipeline file: this module asks ``discovery.discover`` for the built-in level
only (no workspace, so the project level is never touched, and a user folder that does not exist, so the user level
adds nothing) and insists that the whole set is present and valid. A missing or broken built-in is a packaging bug,
not something to hide, so it raises ``BuiltinPipelineError`` instead of returning a partial list.
"""
from __future__ import annotations

from pathlib import Path

from . import paths as _paths
from .discovery import DiscoveryEntry, discover
from .model import PipelineDefinition

BUILTIN_IDS: frozenset[str] = frozenset({"implementar-verificar", "revisao-multimodelo", "corrigir-bug", "pesquisa"})

_NO_USER_LEVEL = ".no-user-level"  # a folder name that never exists next to the built-ins: the user level reads nothing


class BuiltinPipelineError(RuntimeError):
    """A shipped pipeline is missing or does not validate."""


def load_builtin_entries(builtin_dir: Path | None = None) -> tuple[DiscoveryEntry, ...]:
    """Every file of the built-in folder as a ``DiscoveryEntry`` (valid or not), in the reader's order."""
    folder = Path(builtin_dir) if builtin_dir is not None else _paths.builtin_dir()
    result = discover("", None, enabled=True, builtin_dir=folder, user_dir=folder / _NO_USER_LEVEL)
    return tuple(entry for entry in (*result.entries, *result.shadowed) if entry.source == "builtin")


def load_builtin(builtin_dir: Path | None = None) -> dict[str, PipelineDefinition]:
    """The built-in definitions by id. Raises ``BuiltinPipelineError`` for a broken file or a missing id."""
    entries = load_builtin_entries(builtin_dir)
    problems: list[str] = []
    found: dict[str, PipelineDefinition] = {}
    for entry in entries:
        if entry.definition is None or entry.errors:
            detail = "; ".join(error.format() for error in entry.errors) or "arquivo inválido"
            problems.append(f"{entry.id}: {detail}")
        else:
            found[entry.id] = entry.definition
    for missing in sorted(BUILTIN_IDS - set(found) - {entry.id for entry in entries}):
        problems.append(f"{missing}: arquivo ausente")
    if problems:
        raise BuiltinPipelineError("pipelines prontos inválidos: " + " | ".join(problems))
    return found


__all__ = ["BUILTIN_IDS", "BuiltinPipelineError", "load_builtin", "load_builtin_entries"]
