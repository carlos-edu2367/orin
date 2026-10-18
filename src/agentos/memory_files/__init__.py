"""ORIN.md / CLAUDE.md instruction files: layers, rules, imports and on-demand subdirectory memory."""

from .loader import (
    LOCAL,
    MANAGED,
    MAX_IMPORT_DEPTH,
    MAX_TOTAL_CHARS,
    PROJECT,
    USER,
    MemoryFile,
    MemoryFiles,
    frontmatter_bool,
    frontmatter_list,
    glob_match,
    instructions_payload,
    split_frontmatter,
)

__all__ = [
    "LOCAL", "MANAGED", "MAX_IMPORT_DEPTH", "MAX_TOTAL_CHARS", "PROJECT", "USER", "MemoryFile", "MemoryFiles",
    "frontmatter_bool", "frontmatter_list", "glob_match", "instructions_payload", "split_frontmatter",
]
