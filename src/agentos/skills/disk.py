"""Skills and slash commands read from disk, in Claude Code's format (W5).

Sources, highest precedence first (a name defined twice keeps the first one and records the other as shadowed):

1. managed: ``<managed dir>/skills`` and ``<managed dir>/commands`` (Orin's and Claude Code's managed folders);
2. user: ``~/.orin/skills``, ``~/.claude/skills``, ``~/.orin/commands``, ``~/.claude/commands``;
3. project, only once the workspace is trusted: ``.orin/skills``, ``.claude/skills``, ``.orin/commands``, ``.claude/commands``.

A skill is a folder holding ``SKILL.md``; a command is a ``.md`` file under ``commands/`` (a subfolder becomes a
``dir:name`` prefix). Frontmatter is YAML (``yaml.safe_load``); unknown keys are ignored, so a skill written for
Claude Code loads unchanged. Nothing here runs anything: ``!`cmd``` blocks are executed by the Skill tool, through
the Bash permission pipeline, when the skill is invoked.
"""
from __future__ import annotations

import functools
import hashlib
import itertools
import logging
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

MAX_DISK_SKILLS = 200
# A folder is listed at most this far before sorting, so a huge folder cannot stall a turn.
MAX_FOLDER_ENTRIES = 2_000
MAX_SKILL_FILE_BYTES = 200_000
MAX_DESCRIPTION_CHARS = 1_024
CATALOG_DESCRIPTION_CHARS = 250
CATALOG_BUDGET_CHARS = 6_000
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}(?::[a-z0-9][a-z0-9._-]{0,63})*$")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
# W5-R11: frontmatter is capped before it is parsed, and an anchor or an alias in it is refused (see _frontmatter_loader).
MAX_FRONTMATTER_BYTES = 64 * 1024
_ORIGINS = ("managed", "user", "project")
# ``!`cmd``` in a skill body: the command runs when the skill is invoked and its output replaces the block.
INLINE_COMMAND = re.compile(r"!`([^`\n]{1,2000})`")


@dataclass(frozen=True, slots=True)
class DiskSkill:
    name: str
    description: str
    body: str
    path: Path
    origin: str  # managed | user | project
    kind: str  # skill | command
    argument_hint: str = ""
    model_invocable: bool = True
    user_invocable: bool = True
    allowed_tools: tuple[str, ...] = ()
    model: str | None = None
    effort: str | None = None
    context: str | None = None  # "fork" runs the skill in a subagent
    agent: str | None = None
    hooks: Mapping[str, Any] | None = None
    digest: str = ""

    @property
    def id(self) -> str:
        return self.name

    @property
    def directory(self) -> Path:
        return self.path.parent


@dataclass(slots=True)
class DiskSkillCatalog:
    skills: dict[str, DiskSkill] = field(default_factory=dict)
    shadowed: list[tuple[str, str, str]] = field(default_factory=list)  # (name, kept path, ignored path)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)

    def get(self, name: str) -> DiskSkill | None:
        clean = str(name or "").strip().lstrip("/").lower()
        return self.skills.get(clean)

    def model_invocable(self) -> list[DiskSkill]:
        return [item for item in self.skills.values() if item.model_invocable]

    def catalog_items(self, budget: int = CATALOG_BUDGET_CHARS) -> list["CatalogItem"]:
        """Name + short description of every skill the model may invoke, within ``budget`` characters."""
        items: list[CatalogItem] = []
        used = 0
        for skill in sorted(self.model_invocable(), key=lambda item: (_ORIGINS.index(item.origin), item.name)):
            description = _one_line(skill.description)[:CATALOG_DESCRIPTION_CHARS]
            cost = len(skill.name) * 2 + len(description) + 8
            if used + cost > budget:
                break
            used += cost
            items.append(CatalogItem(skill.name, skill.name, description))
        return items


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """Shaped like the skill library's metadata, so the prompt renders both the same way."""

    id: str
    name: str
    description: str


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


@functools.lru_cache(maxsize=1)
def _frontmatter_loader() -> type:
    """A YAML safe loader that refuses anchors and aliases: an alias shares one structure among many references, and
    a reader that walks it expands it (a few hundred bytes became thousands of tool entries)."""
    import yaml

    class FrontmatterLoader(yaml.SafeLoader):
        def compose_node(self, parent, index):  # type: ignore[override]
            if getattr(self.peek_event(), "anchor", None):  # an AliasEvent, or a node with an anchor
                raise yaml.YAMLError("anchors and aliases are not allowed in frontmatter")
            return super().compose_node(parent, index)

    return FrontmatterLoader


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """YAML frontmatter (a mapping, or empty when absent or malformed) and the body after it."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    if len(match.group(1).encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise ValueError("the frontmatter is larger than 64 KB")
    try:
        import yaml

        loaded = yaml.load(match.group(1), Loader=_frontmatter_loader())
    except Exception:  # noqa: BLE001 - malformed frontmatter is reported by the caller as an error
        raise ValueError("the frontmatter is not valid YAML") from None
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError("the frontmatter must be a mapping")
    return {str(key): value for key, value in loaded.items()}, text[match.end():]


def _flag(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1", "false", "no", "0"}:
        return value.strip().lower() in {"true", "yes", "1"}
    return default


def _tools(value: object) -> tuple[str, ...]:
    """``allowed-tools`` as a YAML list or a comma/space separated string of rules like ``Bash(git:*)``."""
    if isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    elif isinstance(value, str):
        raw = re.findall(r"[A-Za-z_][\w:.-]*(?:\([^)]*\))?", value)
    else:
        return ()
    return tuple(dict.fromkeys(item.strip() for item in raw if item.strip()))[:64]


def _optional(value: object, maximum: int = 200) -> str | None:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()[:maximum]
    return None


def parse_disk_skill(path: Path, *, name: str, origin: str, kind: str) -> DiskSkill:
    if path.stat().st_size > MAX_SKILL_FILE_BYTES:
        raise ValueError(f"larger than {MAX_SKILL_FILE_BYTES} bytes")
    data = path.read_bytes()
    text = data.decode("utf-8-sig")
    meta, body = split_frontmatter(text)
    declared = _optional(meta.get("name"), 64)
    # A skill's folder name is its name unless the frontmatter names it (lower-cased, like Claude Code's).
    final = (declared or name).strip().lower()
    if kind == "command":
        final = name  # a command is always named by its path
    if not _NAME.fullmatch(final):
        raise ValueError(f"invalid name '{final[:64]}'")
    description = _optional(meta.get("description"), MAX_DESCRIPTION_CHARS)
    if not description:
        first = next((line.strip().lstrip("#").strip() for line in body.splitlines() if line.strip()), "")
        description = first[:MAX_DESCRIPTION_CHARS] or final
    context = _optional(meta.get("context"), 20)
    hooks = meta.get("hooks") if isinstance(meta.get("hooks"), dict) else None
    return DiskSkill(
        name=final, description=description, body=body.strip(), path=path, origin=origin, kind=kind,
        argument_hint=_optional(meta.get("argument-hint"), 200) or "",
        model_invocable=not _flag(meta.get("disable-model-invocation"), False),
        user_invocable=_flag(meta.get("user-invocable"), True),
        allowed_tools=_tools(meta.get("allowed-tools")),
        model=_optional(meta.get("model"), 120), effort=_optional(meta.get("effort"), 20),
        context="fork" if context and context.lower() == "fork" else None,
        agent=_optional(meta.get("agent"), 64), hooks=hooks,
        digest=hashlib.sha256(data).hexdigest(),
    )


def skill_roots(*, workspace_root: Path | None, trusted: bool, personal: bool = True) -> list[tuple[str, str, Path]]:
    """``(origin, kind, folder)`` in precedence order. ``personal=False`` leaves out the person's own folders (W5-R5)."""
    from agentos.settings.paths import claude_managed_dir, claude_user_dir, orin_managed_dir, orin_user_dir

    managed = [orin_managed_dir(), claude_managed_dir()]
    roots: list[tuple[str, str, Path]] = []
    for origin, bases in (
        ("managed", managed),
        ("user", [orin_user_dir(), claude_user_dir()] if personal else []),
        ("project", [workspace_root / ".orin", workspace_root / ".claude"] if (workspace_root is not None and trusted) else []),
    ):
        for base in bases:
            roots.append((origin, "skill", Path(base) / "skills"))
        for base in bases:
            roots.append((origin, "command", Path(base) / "commands"))
    return roots


def _candidates(kind: str, folder: Path) -> Iterable[tuple[str, Path]]:
    if not folder.is_dir():
        return
    if kind == "skill":
        for child in sorted(itertools.islice(folder.iterdir(), MAX_FOLDER_ENTRIES), key=lambda item: item.name):
            target = child / "SKILL.md"
            # A link (the folder or its SKILL.md) could point anywhere: only real files are read.
            if child.is_dir() and not child.is_symlink() and target.is_file() and not target.is_symlink():
                yield child.name, target
        return
    for path in sorted(itertools.islice(folder.rglob("*.md"), MAX_FOLDER_ENTRIES)):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(folder).with_suffix("")
        if len(relative.parts) > 3:
            continue
        yield ":".join(part.lower() for part in relative.parts), path


def load_disk_skills(*, workspace_root: Path | None, trusted: bool, personal: bool = True) -> DiskSkillCatalog:
    catalog = DiskSkillCatalog()
    for origin, kind, folder in skill_roots(workspace_root=workspace_root, trusted=trusted, personal=personal):
        try:
            candidates = list(_candidates(kind, folder))
        except OSError as error:
            catalog.errors.append((str(folder), f"unreadable: {type(error).__name__}"))
            continue
        for name, path in candidates:
            if len(catalog.skills) >= MAX_DISK_SKILLS:
                catalog.errors.append((str(path), f"more than {MAX_DISK_SKILLS} skills and commands; the rest are ignored"))
                return catalog
            try:
                skill = parse_disk_skill(path, name=name, origin=origin, kind=kind)
            except (OSError, ValueError, UnicodeDecodeError) as error:
                catalog.errors.append((str(path), str(error)[:200]))
                continue
            kept = catalog.skills.get(skill.name)
            if kept is not None:
                catalog.shadowed.append((skill.name, str(kept.path), str(skill.path)))
                continue
            catalog.skills[skill.name] = skill
    for name, kept, ignored in catalog.shadowed:
        logger.info("skill %s at %s is shadowed by %s", name, ignored, kept)
    return catalog


def split_arguments(arguments: str) -> list[str]:
    try:
        return shlex.split(arguments, posix=True)
    except ValueError:
        return arguments.split()


class UnsafeCommandArgument(ValueError):
    """An argument that cannot be passed to an inline command without changing what the command runs (W5-R7)."""


def shell_word(value: str) -> str:
    """``value`` as one word of an inline command (W5-R7).

    POSIX: shell quoting. Windows: the command runs through ``cmd.exe`` (the Bash tool starts it with ``shell=True``), where
    a double quote cannot be kept inside double quotes and a percent sign expands, so such a value is refused, not escaped.
    """
    if sys.platform == "win32":
        if any(char in value for char in '"%\r\n\x00'):
            raise UnsafeCommandArgument("an argument has a double quote, a percent sign or a line break")
        return f'"{value}"'
    return shlex.quote(value)


def escape_backticks(value: str) -> str:
    """``value`` as plain text in a skill body, where a backtick must never start an inline command (W5-R7)."""
    return value.replace("`", "\\`")


def split_inline_commands(body: str) -> list[tuple[bool, str]]:
    """The body as ``(is_command, text)`` pieces, found in the skill's own text before any argument is inserted (W5-R7):
    each ``!`cmd``` is a command piece, everything else is text."""
    pieces: list[tuple[bool, str]] = []
    last = 0
    for match in INLINE_COMMAND.finditer(body):
        pieces.append((False, body[last:match.start()]))
        pieces.append((True, match.group(1)))
        last = match.end()
    pieces.append((False, body[last:]))
    return pieces


def substitute(body: str, *, arguments: str, session_id: str, skill_dir: Path,
               quote: Callable[[str], str] | None = None) -> tuple[str, bool]:
    """Claude Code's substitutions; returns the text and whether ``$ARGUMENTS`` (in any form) was used.

    ``$ARGUMENTS[N]`` and its shorthand ``$N`` are 0-based. They are replaced only when arguments were given, so a
    shell example such as ``awk '{print $1}'`` in a skill invoked without arguments is left intact. ``quote`` shapes each
    inserted argument (W5-R7: one shell word inside a command, escaped text elsewhere); an empty argument stays empty.
    """
    words = split_arguments(arguments)
    used = False

    def shaped(value: str) -> str:
        return quote(value) if quote is not None and value else value

    def one(match: re.Match[str]) -> str:
        nonlocal used
        token = match.group(0)
        if token == "$ARGUMENTS":
            used = True
            return shaped(arguments)
        if match.group(3) is not None:
            return session_id if match.group(3) == "SESSION_ID" else skill_dir.as_posix()
        if not words:
            return token  # without arguments, $N and $ARGUMENTS[N] stay as written
        used = True
        index = int(match.group(1) if match.group(1) is not None else match.group(2))
        return shaped(words[index]) if index < len(words) else ""

    # One pass over the body: text an argument brings in is never substituted again.
    pattern = r"\$ARGUMENTS\[(\d+)\]|\$ARGUMENTS|(?<![\w$])\$(\d{1,2})(?![\w])|\$\{(?:ORIN|CLAUDE)_(SESSION_ID|SKILL_DIR)\}"
    return re.sub(pattern, one, body), used


__all__ = [
    "CATALOG_BUDGET_CHARS", "CatalogItem", "DiskSkill", "DiskSkillCatalog", "INLINE_COMMAND", "MAX_DISK_SKILLS",
    "UnsafeCommandArgument", "escape_backticks", "load_disk_skills", "parse_disk_skill", "shell_word", "skill_roots",
    "split_arguments", "split_frontmatter", "split_inline_commands", "substitute",
]
