"""PreToolUse media gate for Google Antigravity CLI (`agy`) native `read_file`.

This module MUST remain pure Python standard library at import and execution time
so it can be invoked directly via ``python -I <path>/media_gate.py`` in development
as well as via ``orin.exe internal-agy-media-gate`` in frozen PyInstaller builds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Iterable, Mapping, Sequence

MEDIA_GATE_CONFIG_FILENAME = "orin_media_gate.json"
DEFAULT_MAX_MEDIA_BYTES = 200 * 1024 * 1024  # 200 MiB

ALLOWED_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".wav",
        ".ogg",
        ".opus",
        ".m4a",
        ".aac",
        ".flac",
        ".webm",
    }
)
ALLOWED_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".m4v",
        ".webm",
    }
)
ALLOWED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
    }
)
ALLOWED_MEDIA_EXTENSIONS: frozenset[str] = (
    ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS
)

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _strip_extended_prefix(path_str: str) -> str:
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[len("\\\\?\\UNC\\") :]
    if path_str.startswith("\\\\?\\"):
        return path_str[len("\\\\?\\") :]
    return path_str


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    is_junction_fn = getattr(path, "is_junction", None)
    if callable(is_junction_fn):
        try:
            if is_junction_fn():
                return True
        except OSError:
            return True
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    if getattr(st, "st_reparse_tag", 0):
        return True
    if getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    return False


def _normalize_extensions(raw_extensions: Iterable[str] | None) -> frozenset[str]:
    if raw_extensions is None:
        return ALLOWED_MEDIA_EXTENSIONS
    if isinstance(raw_extensions, (str, bytes)):
        return frozenset()
    normalized: set[str] = set()
    for item in raw_extensions:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower()
        if not cleaned:
            continue
        if not cleaned.startswith("."):
            cleaned = f".{cleaned}"
        normalized.add(cleaned)
    return frozenset(normalized)


def is_allowed_media_path(
    raw_path: object,
    *,
    allowed_root: str | Path | None,
    allowed_extensions: Iterable[str] | None = None,
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> tuple[bool, str]:
    """Validate whether ``raw_path`` is an allowed media file inside ``allowed_root``."""
    try:
        if not isinstance(raw_path, str):
            return False, "Path parameter is missing or not a string."
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_path):
            return False, "Path contains control characters."
        cleaned_raw = raw_path.strip()
        if not cleaned_raw:
            return False, "Path is empty."

        # Reject UNC and Windows device paths (`\\?\`, `\\.\`, `\\server\share`, `//server/share`).
        if cleaned_raw.startswith(("\\\\", "//")):
            return False, "UNC and device paths are not allowed."

        # Reject Alternate Data Streams (`:` anywhere after an optional Windows drive letter).
        after_drive = (
            cleaned_raw[2:]
            if _DRIVE_PREFIX_RE.match(cleaned_raw)
            else cleaned_raw
        )
        if ":" in after_drive:
            return False, "Alternate data streams are not allowed."

        # Reject dot/dot-dot traversal segments and Windows trailing dot/space tricks in raw input.
        raw_parts = [p for p in re.split(r"[\\/]+", after_drive) if p != ""]
        for part in raw_parts:
            if part in (".", ".."):
                return False, "Relative path traversal segments are not allowed."
            if part.endswith((" ", ".")):
                return False, "Path segments may not end with a space or period."

        candidate = Path(cleaned_raw)
        if not candidate.is_absolute():
            return False, "Path must be an absolute path inside the workspace."
        if os.name == "nt":
            if not candidate.drive or candidate.drive.startswith(("\\\\", "//")):
                return False, "Path must include a local drive letter."

        if allowed_root is None:
            return False, "Allowed workspace root is not configured."
        root_str = str(allowed_root).strip()
        if not root_str or root_str.startswith(("\\\\", "//")):
            return False, "Allowed workspace root is invalid."
        root_path = Path(root_str)
        if not root_path.is_absolute():
            return False, "Allowed workspace root must be an absolute path."
        if _is_reparse_or_symlink(root_path):
            return False, "Allowed workspace root may not be a symlink or junction."

        try:
            root_resolved = Path(_strip_extended_prefix(str(root_path.resolve(strict=True))))
        except OSError:
            return False, "Allowed workspace root does not exist."
        if not root_resolved.is_dir():
            return False, "Allowed workspace root is not a directory."

        # Check lexical containment before following the filesystem.
        lexical_candidate = Path(_strip_extended_prefix(os.path.abspath(cleaned_raw)))
        lexical_root = Path(_strip_extended_prefix(os.path.abspath(str(root_resolved))))
        if not lexical_candidate.is_relative_to(lexical_root) or lexical_candidate == lexical_root:
            return False, "Path is outside the allowed workspace root."

        # Walk from lexical candidate up to lexical root to catch any symlink/junction before resolve().
        cur = lexical_candidate
        while True:
            if _is_reparse_or_symlink(cur):
                return False, "Symlinks and junctions are not allowed."
            if cur == lexical_root or cur.parent == cur:
                break
            cur = cur.parent

        try:
            target = Path(_strip_extended_prefix(str(candidate.resolve(strict=True))))
        except FileNotFoundError:
            return False, "File does not exist."
        except OSError:
            return False, "Unable to resolve file path."

        if not target.is_relative_to(root_resolved) or target == root_resolved:
            return False, "Resolved path is outside the allowed workspace root."

        # Verify no symlink/junction on resolved chain either.
        cur_resolved = target
        while True:
            if _is_reparse_or_symlink(cur_resolved):
                return False, "Symlinks and junctions are not allowed."
            if cur_resolved == root_resolved or cur_resolved.parent == cur_resolved:
                break
            cur_resolved = cur_resolved.parent

        exts = _normalize_extensions(allowed_extensions)
        if not exts:
            return False, "No media extensions are permitted."
        ext = target.suffix.lower()
        if not ext or ext not in exts or candidate.suffix.lower() != ext:
            return (
                False,
                f"Extension {ext or '<none>'} is not allowed by native read_file; "
                "use Orin's MCP tools for text and code files.",
            )

        try:
            st = os.lstat(target)
        except OSError:
            return False, "Unable to stat target file."

        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return False, "Target is not a regular file."

        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            return False, "Invalid maximum file size configuration."
        if st.st_size < 0 or st.st_size > max_bytes:
            return False, f"File size ({st.st_size} bytes) exceeds the {max_bytes}-byte limit."

        return True, ""
    except Exception:
        return False, "Media gate validation failed."


def decide(payload: Any, config: Any) -> dict[str, str]:
    """Return an Antigravity PreToolUse decision dict ``{"decision": "allow"|"deny", "reason": "..."}``."""
    try:
        if not isinstance(payload, Mapping):
            return {"decision": "deny", "reason": "Invalid hook payload."}
        if not isinstance(config, Mapping):
            return {"decision": "deny", "reason": "Invalid media gate configuration."}

        tool_call = payload.get("toolCall")
        if not isinstance(tool_call, Mapping):
            return {"decision": "deny", "reason": "Missing toolCall in hook payload."}

        tool_name = str(tool_call.get("name") or "").strip()
        if tool_name != "read_file":
            return {
                "decision": "deny",
                "reason": f"Native tool {tool_name!r} is not allowed.",
            }

        args = tool_call.get("args")
        if not isinstance(args, Mapping):
            return {"decision": "deny", "reason": "Missing toolCall.args in hook payload."}

        raw_path = args.get("Path") if "Path" in args else args.get("AbsolutePath")
        allowed_root = config.get("allowed_root")
        raw_exts = config.get("allowed_extensions")
        if raw_exts is not None and (
            not isinstance(raw_exts, Iterable) or isinstance(raw_exts, (str, bytes))
        ):
            return {"decision": "deny", "reason": "Invalid allowed_extensions configuration."}

        max_bytes = config.get("max_bytes", DEFAULT_MAX_MEDIA_BYTES)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            return {"decision": "deny", "reason": "Invalid max_bytes configuration."}

        allowed, reason = is_allowed_media_path(
            raw_path,
            allowed_root=allowed_root,
            allowed_extensions=raw_exts,
            max_bytes=max_bytes,
        )
        if allowed:
            return {"decision": "allow", "reason": ""}
        return {"decision": "deny", "reason": reason or "Access denied by Orin media gate."}
    except Exception:
        return {"decision": "deny", "reason": "Denied due to unexpected media gate error."}


def _windows_short_path(path_str: str) -> str:
    if os.name != "nt" or " " not in path_str:
        return path_str
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(path_str, buf, 1024)
        if n and " " not in buf.value:
            return buf.value
    except Exception:
        pass
    return path_str


def _windows_cmd_executable_token(path_str: str) -> str:
    short = _windows_short_path(path_str)
    if " " not in short:
        return short
    return short.replace(" ", "^ ")


def build_media_gate_hook_command(
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
    script_path: str | Path | None = None,
) -> str:
    """Build the command string placed in ``.agents/hooks.json`` for PreToolUse.

    On Windows, ``agy`` invokes hook commands via Go's ``exec.Command("cmd", "/c", cmd)``,
    which passes ``cmd`` through ``syscall.EscapeArg`` and turns any ``"`` into ``\\"``.
    ``cmd.exe`` does not unescape ``\\"`` on the executable token, so we must avoid ``"``
    in the command string on Windows (using direct path when space-free, 8.3 short path,
    or caret-escaped executable + env var for paths with spaces).
    """
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    exe_str = str(Path(executable if executable is not None else sys.executable).resolve())
    if os.name == "nt":
        exe_tok = _windows_cmd_executable_token(exe_str)
        if is_frozen:
            return f"{exe_tok} internal-agy-media-gate"
        script_resolved = _windows_short_path(
            str(Path(script_path if script_path is not None else __file__).resolve())
        )
        if " " not in script_resolved:
            return f"{exe_tok} -I {script_resolved}"
        return (
            f"set ORIN_AGY_GATE={script_resolved}&& "
            f"{exe_tok} -I -c "
            "__import__('runpy').run_path(__import__('os').environ['ORIN_AGY_GATE'],run_name='__main__')"
        )

    import shlex

    if is_frozen:
        return f"{shlex.quote(exe_str)} internal-agy-media-gate"
    script_resolved = str(
        Path(script_path if script_path is not None else __file__).resolve()
    )
    return f"{shlex.quote(exe_str)} -I {shlex.quote(script_resolved)}"


def _load_gate_config(cwd: Path | None = None) -> Mapping[str, Any] | None:
    base = cwd if cwd is not None else Path.cwd()
    candidates = (
        base / MEDIA_GATE_CONFIG_FILENAME,
        base / ".agents" / MEDIA_GATE_CONFIG_FILENAME,
    )
    for cfg_path in candidates:
        try:
            if not cfg_path.exists():
                continue
            if _is_reparse_or_symlink(cfg_path):
                return None
            st = os.lstat(cfg_path)
            if not stat.S_ISREG(st.st_mode):
                return None
            raw = cfg_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, Mapping):
                return parsed
            return None
        except Exception:
            return None
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint for ``python -I media_gate.py`` and ``orin.exe internal-agy-media-gate``."""
    _ = argv
    try:
        raw_stdin = sys.stdin.read()
        if not raw_stdin or not raw_stdin.strip():
            out = {"decision": "deny", "reason": "Empty hook stdin payload."}
        else:
            try:
                payload = json.loads(raw_stdin)
            except Exception:
                payload = None
            config = _load_gate_config()
            if payload is None:
                out = {"decision": "deny", "reason": "Malformed JSON on hook stdin."}
            elif config is None:
                out = {"decision": "deny", "reason": "Missing or invalid media gate config."}
            else:
                out = decide(payload, config)
    except Exception:
        out = {"decision": "deny", "reason": "Unhandled error in media gate."}

    try:
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
