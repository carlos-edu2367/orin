"""Find pipeline files in the three places and apply precedence and trust (spec 7.2, AC8).

Precedence by ``id``: project, then user, then built-in. The project level is read only
from a workspace the person trusts (``WorkspaceTrustStore``); for an untrusted workspace
the result carries just how many files were skipped, never their names or content.
This module is the only reader of pipeline files.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentos.permissions.trust import WorkspaceTrustStore

from . import paths as _paths
from .errors import PipelineError
from .model import PipelineDefinition, Source, compute_definition_hash
from .validator import validate_structure

_PRECEDENCE: tuple[Source, ...] = ("project", "user", "builtin")


@dataclass(frozen=True, slots=True)
class DiscoveryEntry:
    id: str
    source: Source
    path: Path
    definition: PipelineDefinition | None
    errors: tuple[PipelineError, ...] = ()
    warnings: tuple[PipelineError, ...] = ()
    definition_hash: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    entries: tuple[DiscoveryEntry, ...] = ()
    shadowed: tuple[DiscoveryEntry, ...] = ()
    project_skipped: int = 0


class DiscoveryDisabled(Exception):
    """Pipelines are switched off (``pipelines.enabled = false``)."""

    def __init__(self) -> None:
        self.error = PipelineError("E_DISABLED", "os pipelines estão desligados (`pipelines.enabled`)")
        super().__init__(self.error.format())


def _failed(path: Path, source: Source, code: str, message: str, text_hash: str = "") -> DiscoveryEntry:
    return DiscoveryEntry(path.stem, source, path, None, (PipelineError(code, message),), (), text_hash)


def _read_entry(path: Path, base: Path, source: Source) -> DiscoveryEntry:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return _failed(path, source, "E_YAML_SYNTAX", "não foi possível abrir o arquivo")
    if not resolved.is_relative_to(base):
        return _failed(path, source, "E_YAML_FORBIDDEN", "o arquivo é um atalho que aponta para fora da pasta de pipelines e foi ignorado")
    limit = _paths.MAX_FILE_BYTES
    try:
        if resolved.stat().st_size > limit:
            return _failed(path, source, "E_TOO_LARGE", f"o arquivo passa de {limit // 1024} KB e foi ignorado")
        with resolved.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return _failed(path, source, "E_YAML_SYNTAX", "não foi possível ler o arquivo")
    if len(raw) > limit:
        return _failed(path, source, "E_TOO_LARGE", f"o arquivo passa de {limit // 1024} KB e foi ignorado")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _failed(path, source, "E_YAML_SYNTAX", "o arquivo não está em UTF-8")
    digest = compute_definition_hash(text)
    definition, report = validate_structure(text, source)
    errors = report.errors
    if definition is not None and definition.id != path.stem:
        errors = (
            PipelineError(
                "E_ID",
                f"o `id` do arquivo (`{definition.id}`) deve ser igual ao nome do arquivo (`{path.stem}`)",
                *definition.where("id"),
                "id",
            ),
        )
        definition = None
    return DiscoveryEntry(path.stem, source, path, definition, errors, report.warnings, digest)


def _scan(directory: Path | None, source: Source) -> list[DiscoveryEntry]:
    if directory is None or not directory.is_dir():
        return []
    base = directory.resolve()
    found: list[DiscoveryEntry] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if path.suffix.lower() not in _paths.SUFFIXES or not path.is_file():
            continue
        found.append(_read_entry(path, base, source))
    return found


def _stays_inside(directory: Path, root: Path) -> bool:
    """False when the pipelines folder (or a parent such as ``.orin``) is a link that resolves outside the workspace."""
    try:
        return directory.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for item in directory.iterdir() if item.suffix.lower() in _paths.SUFFIXES and item.is_file())


def discover(
    user_id: str,
    workspace_root: Path | str | None,
    *,
    trust_store: WorkspaceTrustStore | None = None,
    enabled: bool = True,
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
) -> DiscoveryResult:
    """List pipelines. Trust comes from the trust store, never from the caller."""
    if not enabled:
        return DiscoveryResult()
    levels: dict[Source, list[DiscoveryEntry]] = {"project": [], "user": [], "builtin": []}
    skipped = 0
    if workspace_root is not None:
        root = Path(workspace_root)
        store = trust_store if trust_store is not None else WorkspaceTrustStore()
        project = _paths.project_dir(root)
        if store.is_trusted(user_id, root) and _stays_inside(project, root):
            levels["project"] = _scan(project, "project")
        else:
            # untrusted, or `.orin/pipelines` is a link/junction that leaves the project: list nothing, only count
            skipped = _count_files(project)
    levels["user"] = _scan(user_dir if user_dir is not None else _paths.user_dir(), "user")
    levels["builtin"] = _scan(builtin_dir if builtin_dir is not None else _paths.builtin_dir(), "builtin")
    winners: dict[str, DiscoveryEntry] = {}
    shadowed: list[DiscoveryEntry] = []
    order: list[str] = []
    for source in _PRECEDENCE:
        for entry in levels[source]:
            if entry.id in winners:
                shadowed.append(entry)
            else:
                winners[entry.id] = entry
                order.append(entry.id)
    return DiscoveryResult(tuple(winners[key] for key in order), tuple(shadowed), skipped)


def get_pipeline(user_id: str, workspace_root: Path | str | None, pipeline_id: str, *, enabled: bool = True, **kwargs: object) -> DiscoveryEntry | None:
    """The winning entry for ``pipeline_id`` or ``None``; raises ``DiscoveryDisabled`` when pipelines are off."""
    if not enabled:
        raise DiscoveryDisabled()
    result = discover(user_id, workspace_root, enabled=True, **kwargs)  # type: ignore[arg-type]
    for entry in result.entries:
        if entry.id == pipeline_id:
            return entry
    return None


__all__ = [
    "DiscoveryDisabled",
    "DiscoveryEntry",
    "DiscoveryResult",
    "discover",
    "get_pipeline",
]
