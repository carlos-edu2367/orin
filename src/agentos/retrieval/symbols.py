"""Language identity, import extraction, and import resolution.

Resolution is deliberately incomplete: an import that cannot be pointed at an
indexed file is dropped rather than guessed. The graph is used to nudge ranking,
so a missing edge costs a little relevance while a wrong edge costs correctness.
"""
from __future__ import annotations

from posixpath import normpath
import re


_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".md": "markdown",
    ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".sh": "shell", ".ps1": "powershell",
    ".sql": "sql", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".html": "html", ".css": "css", ".scss": "css",
}

# Extensions tried, in order, when a JavaScript-family import omits its suffix.
_JS_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js")

_IMPORT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "python": (
        re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", re.MULTILINE),
        re.compile(r"^\s*from\s+(\.*[A-Za-z_][\w.]*|\.+)\s+import\s", re.MULTILINE),
    ),
    "javascript": (
        re.compile(r"""^\s*import\s+[^'"]*?from\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE),
        re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
        re.compile(r"""^\s*export\s+[^'"]*?from\s+['"]([^'"]+)['"]""", re.MULTILINE),
    ),
}
_IMPORT_PATTERNS["typescript"] = _IMPORT_PATTERNS["javascript"]

MAX_IMPORTS_PER_FILE = 200


def language_for(path: str) -> str | None:
    """The language of a path, or None when it is not worth parsing."""
    lowered = path.lower()
    for extension, language in _EXTENSIONS.items():
        if lowered.endswith(extension):
            return language
    return None


def extract_imports(language: str | None, text: str) -> tuple[str, ...]:
    """Raw import targets, in order of first appearance, without duplicates."""
    patterns = _IMPORT_PATTERNS.get(language or "", ())
    seen: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            target = match.group(1).strip()
            if target and target not in seen:
                seen.append(target)
                if len(seen) >= MAX_IMPORTS_PER_FILE:
                    return tuple(seen)
    return tuple(seen)


def _python_module_key(path: str) -> str:
    without_extension = path[: -len(".py")] if path.endswith(".py") else path
    parts = [part for part in without_extension.split("/") if part not in {"src", ""}]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def resolve_import(language: str | None, target: str, importer_path: str, known_paths: tuple[str, ...] | frozenset[str]) -> str | None:
    """Point an import target at an indexed path, or give up.

    Python targets match by module key, exactly or as a suffix, so
    ``agentos.retrieval.store`` finds ``src/agentos/retrieval/store.py``.
    JavaScript-family relative targets are normalized against the importer's
    directory and then tried with the usual suffixes. Anything else is dropped.
    """
    known = frozenset(known_paths)
    if language == "python":
        if target.startswith("."):
            return None
        for candidate in known:
            key = _python_module_key(candidate)
            if key and (key == target or key.endswith(f".{target}")):
                return candidate
        return None
    if language in {"javascript", "typescript"}:
        if not target.startswith("."):
            return None
        directory = importer_path.rsplit("/", 1)[0] if "/" in importer_path else ""
        base = normpath(f"{directory}/{target}") if directory else normpath(target)
        if base.startswith(".."):
            return None
        for suffix in _JS_SUFFIXES:
            candidate = f"{base}{suffix}"
            if candidate in known:
                return candidate
        return None
    return None


__all__ = ["MAX_IMPORTS_PER_FILE", "extract_imports", "language_for", "resolve_import"]
