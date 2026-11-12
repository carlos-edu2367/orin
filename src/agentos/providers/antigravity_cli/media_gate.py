"""PreToolUse media gate for Google Antigravity CLI (`agy`) native `read_file`.

This module MUST remain pure Python standard library at import and execution time
so it can be invoked directly via ``python -I <path>/media_gate.py`` in development
as well as via ``orin.exe internal-agy-media-gate`` in frozen PyInstaller builds.
"""

from __future__ import annotations

from dataclasses import dataclass
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

_OUTSIDE_REASONS = frozenset(
    {
        "Path is outside the allowed workspace root.",
        "Resolved path is outside the allowed workspace root.",
    }
)
_ROOT_CONFIG_REASON_PREFIX = "Allowed workspace root"
# agy's own conversation store holds the media of every conversation: it is never readable, not even
# when the turn runs without restrictions (a tool image is handed over under `.orin/tool-media`).
_FORBIDDEN_DIR_SEQUENCE: tuple[str, ...] = (".gemini", "antigravity-cli", "brain")


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


def _parse_candidate(raw_path: object) -> tuple[str, Path | None, str]:
    """Syntactic checks shared by every mode; returns ``(cleaned, candidate, reason)``."""
    if not isinstance(raw_path, str):
        return "", None, "Path parameter is missing or not a string."
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_path):
        return "", None, "Path contains control characters."
    cleaned_raw = raw_path.strip()
    if not cleaned_raw:
        return "", None, "Path is empty."

    # Reject UNC and Windows device paths (`\\?\`, `\\.\`, `\\server\share`, `//server/share`).
    if cleaned_raw.startswith(("\\\\", "//")):
        return "", None, "UNC and device paths are not allowed."

    # Reject Alternate Data Streams (`:` anywhere after an optional Windows drive letter).
    after_drive = (
        cleaned_raw[2:] if _DRIVE_PREFIX_RE.match(cleaned_raw) else cleaned_raw
    )
    if ":" in after_drive:
        return "", None, "Alternate data streams are not allowed."

    # Reject dot/dot-dot traversal segments and Windows trailing dot/space tricks in raw input.
    raw_parts = [p for p in re.split(r"[\\/]+", after_drive) if p != ""]
    for part in raw_parts:
        if part in (".", ".."):
            return "", None, "Relative path traversal segments are not allowed."
        if part.endswith((" ", ".")):
            return "", None, "Path segments may not end with a space or period."

    candidate = Path(cleaned_raw)
    if not candidate.is_absolute():
        return "", None, "Path must be an absolute path inside the workspace."
    if os.name == "nt":
        if not candidate.drive or candidate.drive.startswith(("\\\\", "//")):
            return "", None, "Path must include a local drive letter."
    return cleaned_raw, candidate, ""


def _in_forbidden_store(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    n = len(_FORBIDDEN_DIR_SEQUENCE)
    return any(
        tuple(lowered[i : i + n]) == _FORBIDDEN_DIR_SEQUENCE
        for i in range(len(lowered) - n + 1)
    )


def _resolve_in_root(
    cleaned_raw: str, candidate: Path, root_value: object
) -> tuple[Path | None, str]:
    """Resolve ``candidate`` strictly inside one root; ``(None, reason)`` when it is not."""
    root_str = str(root_value).strip() if root_value is not None else ""
    if not root_str or root_str.startswith(("\\\\", "//")):
        return None, "Allowed workspace root is invalid."
    root_path = Path(root_str)
    if not root_path.is_absolute():
        return None, "Allowed workspace root must be an absolute path."
    if _is_reparse_or_symlink(root_path):
        return None, "Allowed workspace root may not be a symlink or junction."

    try:
        root_resolved = Path(_strip_extended_prefix(str(root_path.resolve(strict=True))))
    except OSError:
        return None, "Allowed workspace root does not exist."
    if not root_resolved.is_dir():
        return None, "Allowed workspace root is not a directory."

    # Check lexical containment before following the filesystem.
    lexical_candidate = Path(_strip_extended_prefix(os.path.abspath(cleaned_raw)))
    lexical_root = Path(_strip_extended_prefix(os.path.abspath(str(root_resolved))))
    if not lexical_candidate.is_relative_to(lexical_root) or lexical_candidate == lexical_root:
        return None, "Path is outside the allowed workspace root."

    # Walk from lexical candidate up to lexical root to catch any symlink/junction before resolve().
    cur = lexical_candidate
    while True:
        if _is_reparse_or_symlink(cur):
            return None, "Symlinks and junctions are not allowed."
        if cur == lexical_root or cur.parent == cur:
            break
        cur = cur.parent

    try:
        target = Path(_strip_extended_prefix(str(candidate.resolve(strict=True))))
    except FileNotFoundError:
        return None, "File does not exist."
    except OSError:
        return None, "Unable to resolve file path."

    if not target.is_relative_to(root_resolved) or target == root_resolved:
        return None, "Resolved path is outside the allowed workspace root."

    # Verify no symlink/junction on resolved chain either.
    cur_resolved = target
    while True:
        if _is_reparse_or_symlink(cur_resolved):
            return None, "Symlinks and junctions are not allowed."
        if cur_resolved == root_resolved or cur_resolved.parent == cur_resolved:
            break
        cur_resolved = cur_resolved.parent
    return target, ""


def _resolve_unrestricted(cleaned_raw: str, candidate: Path) -> tuple[Path | None, str]:
    """Resolve ``candidate`` anywhere on a local drive (a turn running without restrictions).

    No root bounds it, so what is left to refuse is a link anywhere in the chain (the resolved path must
    equal the lexical one) and agy's own conversation store.
    """
    lexical = Path(_strip_extended_prefix(os.path.abspath(cleaned_raw)))
    if _in_forbidden_store(lexical):
        return None, "agy's conversation store is not readable."
    if _is_reparse_or_symlink(lexical):
        return None, "Symlinks and junctions are not allowed."
    try:
        target = Path(_strip_extended_prefix(str(candidate.resolve(strict=True))))
    except FileNotFoundError:
        return None, "File does not exist."
    except OSError:
        return None, "Unable to resolve file path."
    if os.path.normcase(str(target)) != os.path.normcase(str(lexical)):
        return None, "Symlinks and junctions are not allowed."
    if _in_forbidden_store(target):
        return None, "agy's conversation store is not readable."
    return target, ""


def _reason_priority(reason: str) -> int:
    if reason in _OUTSIDE_REASONS:
        return 1
    if reason.startswith(_ROOT_CONFIG_REASON_PREFIX):
        return 2
    return 0


def collect_media_roots(
    allowed_root: object = None, allowed_roots: object = None
) -> list[str]:
    """The configured roots as strings: ``allowed_roots`` first, then the legacy single ``allowed_root``."""
    roots: list[str] = []
    if isinstance(allowed_roots, (list, tuple, set, frozenset)):
        for item in allowed_roots:
            if isinstance(item, (str, Path)) and str(item).strip():
                text = str(item).strip()
                if text not in roots:
                    roots.append(text)
    elif isinstance(allowed_roots, (str, Path)) and str(allowed_roots).strip():
        roots.append(str(allowed_roots).strip())
    if isinstance(allowed_root, (str, Path)) and str(allowed_root).strip():
        legacy = str(allowed_root).strip()
        if legacy not in roots:
            roots.append(legacy)
    return roots


def is_allowed_media_path(
    raw_path: object,
    *,
    allowed_root: str | Path | None = None,
    allowed_roots: Iterable[str | Path] | None = None,
    unrestricted: bool = False,
    allowed_extensions: Iterable[str] | None = None,
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES,
) -> tuple[bool, str]:
    """Validate whether ``raw_path`` is an allowed media file inside one of the allowed roots.

    ``allowed_root`` (a single root) stays for configurations written before ``allowed_roots`` existed.
    ``unrestricted`` lifts only the root bound: the extension, size, absolute-path, UNC/device-path and
    link checks all still apply.
    """
    try:
        cleaned_raw, candidate, reason = _parse_candidate(raw_path)
        if candidate is None:
            return False, reason

        target: Path | None
        if unrestricted:
            target, reason = _resolve_unrestricted(cleaned_raw, candidate)
        else:
            roots = collect_media_roots(allowed_root, allowed_roots)
            if not roots:
                return False, "Allowed workspace root is not configured."
            target = None
            reason = ""
            best_priority = 99
            for root_value in roots:
                found, why = _resolve_in_root(cleaned_raw, candidate, root_value)
                if found is not None:
                    target = found
                    break
                priority = _reason_priority(why)
                if priority < best_priority:
                    best_priority, reason = priority, why
        if target is None:
            return False, reason

        exts = _normalize_extensions(allowed_extensions)
        if not exts:
            return False, "No media extensions are permitted."
        ext = target.suffix.lower()
        if not ext or ext not in exts or candidate.suffix.lower() != ext:
            return (
                False,
                f"Extension {ext or '<none>'} is not allowed by native read_file for this model; "
                "use Orin's MCP tools for text, code and documents "
                "(native read_file is only for images, and for audio/video on models that hear them).",
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


@dataclass(frozen=True)
class MediaReadPolicy:
    """What the native ``read_file`` of one agy process may open: the same rules the hook applies."""

    roots: tuple[str, ...] = ()
    unrestricted: bool = False
    extensions: frozenset[str] = ALLOWED_MEDIA_EXTENSIONS
    max_bytes: int = DEFAULT_MAX_MEDIA_BYTES

    def check(self, raw_path: object) -> tuple[bool, str]:
        return is_allowed_media_path(
            raw_path,
            allowed_roots=self.roots,
            unrestricted=self.unrestricted,
            allowed_extensions=self.extensions,
            max_bytes=self.max_bytes,
        )


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
        raw_roots = config.get("allowed_roots")
        if raw_roots is not None and (
            not isinstance(raw_roots, (list, tuple))
            or not all(isinstance(item, str) for item in raw_roots)
        ):
            return {"decision": "deny", "reason": "Invalid allowed_roots configuration."}
        raw_unrestricted = config.get("unrestricted", False)
        if not isinstance(raw_unrestricted, bool):
            return {"decision": "deny", "reason": "Invalid unrestricted configuration."}
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
            allowed_root=config.get("allowed_root"),
            allowed_roots=raw_roots,
            unrestricted=raw_unrestricted,
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
