"""Read a plugin's ``commands/`` directory into declarative contributions.

A command is a markdown prompt template, not code: the body becomes the user's
prompt when the command is invoked. Frontmatter is read leniently on purpose —
authors in the wild invent their own keys (``category``, ``trigger-mode``,
``triggers_pt``), and a plugin must not fail because of one.
"""
from __future__ import annotations

from pathlib import Path

from .models import CommandContribution, plugin_id_from_name

MAX_COMMANDS = 200


def _frontmatter(document: str) -> tuple[dict[str, str], str]:
    if not document.startswith("---\n"):
        return {}, document
    try:
        header, body = document[4:].split("\n---\n", 1)
    except ValueError:
        return {}, document
    values: dict[str, str] = {}
    for line in header.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip().lower()] = value.strip().strip("'\"")
    return values, body


def parse_commands(root: Path, *, plugin_id: str) -> tuple[tuple[CommandContribution, ...], tuple[str, ...]]:
    root = Path(root)
    if not root.is_dir():
        return (), ()
    files = sorted(root.glob("*.md"))
    warnings: list[str] = []
    if len(files) > MAX_COMMANDS:
        warnings.append(f"o plugin declara mais de {MAX_COMMANDS} comandos; o restante foi ignorado")
    commands: list[CommandContribution] = []
    for item in files[:MAX_COMMANDS]:
        try:
            values, body = _frontmatter(item.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            warnings.append(f"comando quebrado ignorado: {item.name}")
            continue
        if not body.strip():
            warnings.append(f"comando sem corpo ignorado: {item.name}")
            continue
        try:
            slug = plugin_id_from_name(item.stem)
        except ValueError:
            warnings.append(f"comando com nome inválido ignorado: {item.name}")
            continue
        commands.append(CommandContribution(
            f"{plugin_id}:{slug}", slug,
            values.get("description", "")[:1024],
            values.get("argument-hint", "")[:120],
            item.relative_to(root).as_posix(),
        ))
    return tuple(commands), tuple(warnings)
