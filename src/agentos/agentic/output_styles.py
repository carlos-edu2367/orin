"""Output styles: how Orin Code talks, never what it may do.

Built-ins plus markdown files in ``output-styles/`` of the user and project
directories (Orin and Claude Code). A file's frontmatter may set ``name``,
``description`` and ``keep-coding-instructions`` (default false for files: a
custom style replaces the coding guidance unless it asks to keep it).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentos.memory_files import frontmatter_bool, split_frontmatter
from agentos.settings.paths import claude_user_dir, orin_user_dir

MAX_STYLE_BYTES = 32 * 1024
MAX_STYLE_FILES = 64

EXPLANATORY_INSTRUCTIONS = (
    "While you work, help the user understand the codebase and your choices. Before or after a significant "
    "step, add a short insight: why this approach, how the pieces connect, which trade-off you made. Keep "
    "each insight brief and specific to this code, and skip what the user obviously already knows."
)
LEARNING_INSTRUCTIONS = (
    "Work with the user as a collaborator who wants to learn. Do the routine parts yourself, but when a piece "
    "of the change involves a meaningful design decision (a small function, a branch of logic, a test case), "
    "leave it to the user: explain what is needed and why, mark the exact spot with a TODO(human) comment, and "
    "stop so they can write it. Then review what they wrote. Share brief insights about the codebase as you go."
)


@dataclass(frozen=True, slots=True)
class OutputStyle:
    name: str
    description: str
    instructions: str
    keep_coding_instructions: bool
    source: str


BUILTIN_STYLES: dict[str, OutputStyle] = {
    "default": OutputStyle("Default", "Concise, task-focused replies.", "", True, "builtin"),
    "explanatory": OutputStyle("Explanatory", "Explains the reasoning and the codebase while working.", EXPLANATORY_INSTRUCTIONS, True, "builtin"),
    "learning": OutputStyle("Learning", "Leaves small, meaningful pieces for the user to write.", LEARNING_INSTRUCTIONS, True, "builtin"),
}


def _style_dirs(workspace_root: Path | str, trusted: bool) -> tuple[tuple[Path, str], ...]:
    root = Path(workspace_root)
    dirs: list[tuple[Path, str]] = [
        (claude_user_dir() / "output-styles", "user"),
        (orin_user_dir() / "output-styles", "user"),
    ]
    # A repository's own style files replace prompt text, so they count only for a trusted workspace.
    if trusted:
        dirs += [
            (root / ".claude" / "output-styles", "project"),
            (root / ".orin" / "output-styles", "project"),
        ]
    return tuple(dirs)


def load_output_styles(workspace_root: Path | str, *, trusted: bool = True,
                       plugin_dirs: tuple[tuple[str, Path], ...] | list[tuple[str, Path]] = ()) -> dict[str, OutputStyle]:
    """Built-ins, then active plugins' styles (named ``plugin:style``, W5), then user files, then project files
    (trusted workspaces only); a later file with the same name wins."""
    styles = dict(BUILTIN_STYLES)
    plugin_sources = [(Path(directory), "plugin", f"{plugin_id}:") for plugin_id, directory in plugin_dirs]
    for directory, source, prefix in [*plugin_sources, *((directory, source, "") for directory, source in _style_dirs(workspace_root, trusted))]:
        try:
            if not directory.is_dir():
                continue
            candidates = sorted(directory.glob("*.md"))[:MAX_STYLE_FILES]
        except OSError:
            continue
        for path in candidates:
            try:
                with path.open("rb") as handle:
                    data = handle.read(MAX_STYLE_BYTES)
            except OSError:
                continue
            if b"\x00" in data:
                continue
            meta, body = split_frontmatter(data.decode("utf-8", errors="replace"))
            name = prefix + str(meta.get("name") or path.stem).strip()[:80]
            if not name or not body.strip():
                continue
            styles[name.lower()] = OutputStyle(
                name, str(meta.get("description") or "").strip()[:300], body.strip(),
                frontmatter_bool(meta, "keep-coding-instructions", False), source,
            )
    return styles


def resolve_output_style(settings: object, workspace_root: Path | str,
                         plugin_dirs: tuple[tuple[str, Path], ...] | list[tuple[str, Path]] = ()) -> OutputStyle | None:
    """The ``outputStyle`` in force, or None for the default (or an unknown name)."""
    getter = getattr(settings, "get", None)
    raw = getter("outputStyle") if callable(getter) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    key = raw.strip().lower()
    if key == "default":
        return None
    style = load_output_styles(workspace_root, trusted=bool(getattr(settings, "trusted", False)), plugin_dirs=plugin_dirs).get(key)
    return style if style is not None and style.instructions else None


__all__ = ["BUILTIN_STYLES", "OutputStyle", "load_output_styles", "resolve_output_style"]
