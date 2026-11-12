from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from agentos.providers.antigravity_cli.media_gate import (
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_MEDIA_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    DEFAULT_MAX_MEDIA_BYTES,
    MEDIA_GATE_CONFIG_FILENAME,
    MediaReadPolicy,
    build_media_gate_hook_command,
    collect_media_roots,
    is_allowed_media_path,
)
from agentos.providers.cli_adapter import CliTurnRequest, ToolServerWiring

MIN_ANTIGRAVITY_VERSION: tuple[int, int, int] = (1, 1, 1)
MAX_ANTIGRAVITY_MAJOR: int = 1
DEFAULT_ANTIGRAVITY_MODEL: str = "gemini-4-argon-medium"
VALID_EFFORT_LEVELS: frozenset[str] = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)

_ALLOWLISTED_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "LANG",
    "LC_ALL",
)

_FORBIDDEN_API_KEY_ENVS: frozenset[str] = frozenset(
    {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
    }
)

_SAFE_MODEL_RE = re.compile(r"\A[A-Za-z0-9._:\-]{1,128}\Z")
_SAFE_SERVER_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_SAFE_SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9._:\-]{1,128}\Z")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

_SENSITIVE_TERMS: tuple[str, ...] = (
    "token",
    "key",
    "secret",
    "password",
    "bearer",
    "authorization",
    "system_prompt",
)

_UNSUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "image",
        "image_url",
        "input_image",
        "document",
        "audio",
        "video",
        "file",
        "media",
    }
)


def clean_antigravity_model_id(model: str | None) -> str | None:
    """Validate and normalize an Antigravity CLI model identifier, stripping optional `google-antigravity/` prefix."""
    if model is None:
        return None
    if not isinstance(model, str):
        raise ValueError(f"Invalid model identifier for Antigravity CLI: {model!r}")
    if "\n" in model or "\r" in model or "\x00" in model:
        raise ValueError(f"Invalid model identifier for Antigravity CLI: {model!r}")
    cleaned = model.strip()
    if not cleaned:
        return None
    if cleaned.startswith("google-antigravity/"):
        cleaned = cleaned[len("google-antigravity/") :]
    if (
        not cleaned
        or cleaned.startswith("-")
        or not _SAFE_MODEL_RE.fullmatch(cleaned)
    ):
        raise ValueError(f"Invalid model identifier for Antigravity CLI: {model!r}")
    return cleaned


def _has_forbidden_control_or_linebreak(value: str) -> bool:
    return any(
        ord(ch) < 32 or ord(ch) == 127 or ch in {"\x85", "\u2028", "\u2029"}
        for ch in value
    )


def _validate_path_arg(value: str | Path, label: str) -> str:
    if not isinstance(value, (str, Path)):
        raise ValueError(f"Invalid {label} path")
    raw = str(value)
    if _has_forbidden_control_or_linebreak(raw):
        raise ValueError(f"Invalid {label} path")
    text = raw.strip()
    if not text or text.startswith("-"):
        raise ValueError(f"Invalid {label} path")
    return text


def resolve_antigravity_home(
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the local Antigravity CLI state directory (`~/.gemini/antigravity-cli` by default)."""
    source_env: Mapping[str, str] = os.environ if env is None else env
    raw_override = str(source_env.get("ANTIGRAVITY_CLI_HOME") or "").strip()
    if raw_override:
        return Path(raw_override).expanduser()
    if env is not None:
        for key in ("USERPROFILE", "HOME"):
            raw_home = str(env.get(key) or "").strip()
            if raw_home:
                return Path(raw_home).expanduser() / ".gemini" / "antigravity-cli"
    return Path.home() / ".gemini" / "antigravity-cli"


def resolve_antigravity_binary(
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Dynamically resolve the `agy` CLI binary without module-level caching.

    1. Checks `PATH` first via `shutil.which("agy", path=...)`.
    2. Falls back to `%LOCALAPPDATA%\\agy\\bin\\agy.exe` on Windows (or when
       `LOCALAPPDATA` is explicitly provided in `env`). When `env` is explicitly
       passed, never falls back to host `os.environ`.
    """
    source_env: Mapping[str, str] = os.environ if env is None else env
    path_arg = (
        os.environ.get("PATH") if env is None else source_env.get("PATH", "")
    )

    found = shutil.which("agy", path=path_arg)
    if found:
        found_path = Path(found)
        if found_path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            direct_exe = found_path.with_suffix(".exe")
            if direct_exe.is_file():
                return str(direct_exe)
        return str(found_path)

    local_appdata_candidates: list[Path] = []
    raw_local = str(source_env.get("LOCALAPPDATA") or "").strip()
    if raw_local:
        local_appdata_candidates.append(Path(raw_local))
    elif (env is not None and "USERPROFILE" in env) or os.name == "nt":
        raw_profile = str(source_env.get("USERPROFILE") or "").strip()
        if raw_profile:
            local_appdata_candidates.append(
                Path(raw_profile) / "AppData" / "Local"
            )

    for base_dir in local_appdata_candidates:
        candidate = base_dir / "agy" / "bin" / "agy.exe"
        try:
            if not candidate.is_symlink() and candidate.is_file():
                return str(candidate)
        except OSError:
            continue

    return None


def build_isolated_antigravity_env(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a strict allowlisted environment for `agy`, stripping cloud API keys."""
    source: Mapping[str, str] = os.environ if base is None else base
    isolated: dict[str, str] = {}

    upper_map: dict[str, str] = {
        str(k).upper(): str(v)
        for k, v in source.items()
        if k is not None and v is not None
    }
    for key in _ALLOWLISTED_ENV_KEYS:
        val = source.get(key)
        if val is None:
            val = upper_map.get(key.upper())
        if val is not None:
            isolated[key] = str(val)

    for forbidden in list(isolated.keys()):
        if forbidden.upper() in _FORBIDDEN_API_KEY_ENVS:
            isolated.pop(forbidden, None)

    isolated["NO_COLOR"] = "1"
    isolated["TERM"] = "dumb"
    isolated["PYTHONIOENCODING"] = "utf-8"
    return isolated


def antigravity_version(
    binary: str | None = None,
    *,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int, int] | None:
    """Return `(major, minor, patch)` of `agy --version` or `None` if unavailable."""
    resolved = binary or resolve_antigravity_binary(env)
    if not resolved:
        return None

    isolated_env = build_isolated_antigravity_env(env)
    if os.name == "nt":
        for sys_key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
            if sys_key not in isolated_env and sys_key in os.environ:
                isolated_env[sys_key] = os.environ[sys_key]

    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    version_cmd = (
        [sys.executable, resolved, "--version"]
        if str(resolved).lower().endswith(".py")
        else [resolved, "--version"]
    )
    try:
        completed = subprocess.run(
            version_cmd,
            env=isolated_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            check=False,
        )
    except Exception:
        return None

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
    match = _VERSION_RE.search(output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_antigravity_version_supported(
    version: tuple[int, int, int] | None,
) -> bool:
    """Return True when `version >= MIN_ANTIGRAVITY_VERSION` and `version[0] <= MAX_ANTIGRAVITY_MAJOR`."""
    return (
        version is not None
        and version >= MIN_ANTIGRAVITY_VERSION
        and version[0] <= MAX_ANTIGRAVITY_MAJOR
    )


def _is_symlink_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junc = getattr(path, "is_junction", None)
    if callable(is_junc):
        try:
            return bool(is_junc())
        except OSError:
            return False
    return False


def _write_owner_only_file(path: Path, content: str) -> Path:
    if _is_symlink_or_junction(path):
        raise ValueError(f"Refusing to write to symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return path


def _validate_mcp_server_params(
    *,
    server_name: str,
    url: Any,
    token: Any,
) -> tuple[str, str, str]:
    srv = str(server_name or "").strip()
    if not srv or not _SAFE_SERVER_NAME_RE.fullmatch(srv):
        raise ValueError(f"Invalid Antigravity MCP server_name: {server_name!r}")

    if not isinstance(url, str) or not url.strip():
        raise ValueError("Invalid MCP handle.url: must be a non-empty string")
    if _has_forbidden_control_or_linebreak(url):
        raise ValueError("Invalid MCP handle.url: control characters forbidden")
    clean_url = url.strip()
    if any(ch.isspace() for ch in clean_url) or not clean_url.startswith(
        ("http://", "https://")
    ):
        raise ValueError(
            "Invalid MCP handle.url: must be an HTTP(S) URL without whitespace"
        )

    if not isinstance(token, str) or not token.strip():
        raise ValueError("Invalid MCP handle.token: must be a non-empty string")
    if _has_forbidden_control_or_linebreak(token):
        raise ValueError(
            "Invalid MCP handle.token: control characters forbidden"
        )
    clean_token = token.strip()
    if any(ch.isspace() for ch in clean_token):
        raise ValueError(
            "Invalid MCP handle.token: whitespace characters forbidden"
        )

    return srv, clean_url, clean_token


def _coerce_timeout_seconds(tool_timeout_ms: int | None) -> int | None:
    if (
        isinstance(tool_timeout_ms, int)
        and not isinstance(tool_timeout_ms, bool)
        and tool_timeout_ms > 0
    ):
        return max(1, -(-tool_timeout_ms // 1000))
    return None


def is_antigravity_gemini_model(model: str | None) -> bool:
    """Return True when ``model`` resolves to a Gemini chat model on Antigravity."""
    try:
        cleaned = clean_antigravity_model_id(model)
    except ValueError:
        return False
    if not cleaned or cleaned == "antigravity-image":
        return False
    return cleaned.lower().startswith("gemini-")


def antigravity_native_media_extensions(model: str | None) -> frozenset[str]:
    """File extensions the native `read_file` of an agy model may open (empty: it is off for this model).

    Follows what the model perceives: Gemini sees images and hears audio/video; Claude (and any model that
    sees images) only images; a text-only model and `antigravity-image` get nothing.
    """
    try:
        cleaned = clean_antigravity_model_id(model)
    except ValueError:
        return frozenset()
    if not cleaned or cleaned == "antigravity-image":
        return frozenset()
    from agentos.providers.antigravity_cli.catalog import antigravity_input_modalities

    modalities = antigravity_input_modalities(cleaned)
    extensions: set[str] = set()
    if "image" in modalities:
        extensions |= ALLOWED_IMAGE_EXTENSIONS
    if "audio" in modalities:
        extensions |= ALLOWED_AUDIO_EXTENSIONS
    if "video" in modalities:
        extensions |= ALLOWED_VIDEO_EXTENSIONS
    return frozenset(extensions)


def resolve_media_roots(
    workspace_root: Path | str | None,
    additional_roots: Sequence[Path | str] = (),
) -> tuple[Path, ...]:
    """The workspace first, then the extra folders the person added (`/add-dir`): absolute, existing, deduplicated."""
    resolved: list[Path] = []
    candidates: list[tuple[Path | str, bool]] = []
    if workspace_root is not None:
        candidates.append((workspace_root, True))
    candidates.extend((item, False) for item in (additional_roots or ()))
    for item, is_workspace in candidates:
        try:
            text = str(item)
            if not text.strip() or _has_forbidden_control_or_linebreak(text):
                continue
            path = Path(text)
            try:
                path = path.resolve()
            except OSError:
                path = path.absolute()
            if path in resolved:
                continue
            if is_workspace or path.is_dir():
                resolved.append(path)
        except (OSError, ValueError):
            continue
    return tuple(resolved)


def antigravity_native_media_policy(
    model: str | None,
    workspace_root: Path | str | None,
    *,
    additional_roots: Sequence[Path | str] = (),
    unrestricted: bool = False,
) -> MediaReadPolicy | None:
    """The native `read_file` policy of one agy turn, or None when it must stay off."""
    extensions = antigravity_native_media_extensions(model)
    if not extensions or workspace_root is None:
        return None
    roots = resolve_media_roots(workspace_root, additional_roots)
    if not roots:
        return None
    return MediaReadPolicy(
        roots=tuple(str(root) for root in roots),
        unrestricted=bool(unrestricted),
        extensions=extensions,
    )


def antigravity_media_prompt_note(
    workspace_root: Path | str,
    *,
    extensions: Sequence[str] | frozenset[str] | None = None,
    additional_roots: Sequence[Path | str] = (),
    unrestricted: bool = False,
) -> str:
    """System-prompt instruction for the native `read_file` of an agy turn (image; audio/video when the model hears)."""
    root_posix = Path(workspace_root).resolve().as_posix()
    exts = (
        ALLOWED_MEDIA_EXTENSIONS
        if extensions is None
        else frozenset(str(e).lower() for e in extensions)
    )
    hears = bool(exts & (ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS))
    if unrestricted:
        scope = (
            f"inside the conversation workspace (`{root_posix}`) or anywhere else on this machine"
        )
    else:
        extras = [
            Path(item).resolve().as_posix()
            for item in additional_roots
            if str(item).strip() and Path(item).resolve().as_posix() != root_posix
        ]
        scope = f"inside the conversation workspace (`{root_posix}`)"
        if extras:
            scope += " and these folders the person added: " + ", ".join(
                f"`{item}`" for item in extras
            )
    if hears:
        kinds = "image, audio and video files"
        verbs = "see an image, hear an audio file or watch a video"
    else:
        kinds = "image files"
        verbs = "see an image"
    note = (
        f"To {verbs}, call your native `read_file` tool with the file's ABSOLUTE path "
        f"(for example, `{root_posix}/...`); a relative path is denied. "
        f"Orin's view_file/Read tools do NOT show you a picture on this CLI (it keeps large "
        f"images out of the conversation): for an image they only report the file's absolute "
        f"path, to open with `read_file`. "
        f"Your native `read_file` tool ONLY accepts {kinds} {scope}; "
        f"for all text, code, PDF, and document files, keep using Orin's MCP tools."
    )
    if not hears:
        note += (
            " Your model does not hear audio or watch video through `read_file`: for an audio or "
            "video file call Orin's view_file with its path, and Orin returns a transcription "
            "made for you."
        )
    return note


def _media_note_from_gate_config(
    cfg: Mapping[str, Any], fallback_root: Path | str | None
) -> str | None:
    roots = collect_media_roots(cfg.get("allowed_root"), cfg.get("allowed_roots"))
    primary: Path | str | None = roots[0] if roots else fallback_root
    if primary is None:
        return None
    raw_exts = cfg.get("allowed_extensions")
    exts = (
        [str(e) for e in raw_exts]
        if isinstance(raw_exts, list)
        else None
    )
    return antigravity_media_prompt_note(
        primary,
        extensions=exts,
        additional_roots=roots[1:],
        unrestricted=cfg.get("unrestricted") is True,
    )


def _render_orin_agent_md(
    *,
    server_name: str,
    url: str,
    token: str,
    system_prompt: str,
    timeout_seconds: int | None = None,
    allow_native_media_read: bool = False,
    workspace_root: Path | None = None,
    media_note: str | None = None,
) -> str:
    srv, clean_url, clean_token = _validate_mcp_server_params(
        server_name=server_name,
        url=url,
        token=token,
    )
    auth_val = f"Bearer {clean_token}"
    tools_line = "tools: [read_file]\n" if allow_native_media_read else ""
    timeout_line = (
        f"    timeoutSeconds: {int(timeout_seconds)}\n"
        if isinstance(timeout_seconds, int)
        and not isinstance(timeout_seconds, bool)
        and timeout_seconds > 0
        else ""
    )
    instructions_body = system_prompt.strip()
    if allow_native_media_read and workspace_root is not None:
        if media_note is None:
            media_note = antigravity_media_prompt_note(workspace_root)
        if media_note not in instructions_body:
            instructions_body = (
                f"{instructions_body}\n\n{media_note}"
                if instructions_body
                else media_note
            )
    return (
        "---\n"
        "name: orin\n"
        "description: Orin isolated CLI agent\n"
        "mainAgent: true\n"
        "excludeDefaultComponents: true\n"
        "inheritMcp: false\n"
        f"{tools_line}"
        "mcpServers:\n"
        f"  - name: {srv}\n"
        f"    url: {json.dumps(clean_url, ensure_ascii=True)}\n"
        "    headers:\n"
        f"      Authorization: {json.dumps(auth_val, ensure_ascii=True)}\n"
        f"{timeout_line}"
        "---\n\n"
        "# Instructions\n\n"
        f"{instructions_body}\n"
    )


def _update_agent_md_instructions(
    agent_md_path: Path,
    system_prompt: str,
    *,
    timeout_seconds: int | None = None,
    workspace_root: Path | None = None,
) -> None:
    if agent_md_path.is_symlink():
        raise ValueError(f"Refusing to write to symlink: {agent_md_path}")
    if not agent_md_path.is_file():
        return
    workdir = agent_md_path.parent.parent.parent
    for doc_name in ("AGENTS.md", "GEMINI.md"):
        doc_candidate = workdir / doc_name
        if doc_candidate.is_symlink():
            raise ValueError(f"Refusing symlinked {doc_name}: {doc_candidate}")

    existing = agent_md_path.read_text(encoding="utf-8")
    marker = "# Instructions"
    prefix = existing.split(marker, 1)[0] if marker in existing else ""
    instructions_body = system_prompt.strip()
    if "tools: [read_file]" in prefix:
        media_note: str | None = None
        gate_cfg_path = agent_md_path.parent.parent / MEDIA_GATE_CONFIG_FILENAME
        if gate_cfg_path.is_file() and not gate_cfg_path.is_symlink():
            try:
                gate_cfg = json.loads(gate_cfg_path.read_text(encoding="utf-8"))
                if isinstance(gate_cfg, Mapping):
                    media_note = _media_note_from_gate_config(gate_cfg, workspace_root)
            except Exception:
                media_note = None
        if media_note is None and workspace_root is not None:
            media_note = antigravity_media_prompt_note(workspace_root)
        if media_note is not None:
            if media_note not in instructions_body:
                instructions_body = (
                    f"{instructions_body}\n\n{media_note}"
                    if instructions_body
                    else media_note
                )

    if marker in existing:
        if (
            isinstance(timeout_seconds, int)
            and not isinstance(timeout_seconds, bool)
            and timeout_seconds > 0
            and "timeoutSeconds:" not in prefix
            and "\n---\n" in prefix
        ):
            head, tail = prefix.rsplit("\n---\n", 1)
            prefix = f"{head}\n    timeoutSeconds: {int(timeout_seconds)}\n---\n{tail}"
        updated = f"{prefix}{marker}\n\n{instructions_body}\n"
    else:
        updated = f"{existing.rstrip()}\n\n{marker}\n\n{instructions_body}\n"
    _write_owner_only_file(agent_md_path, updated)


def _update_mcp_config_timeout(
    mcp_config_path: Path,
    timeout_seconds: int,
) -> None:
    if mcp_config_path.is_symlink():
        raise ValueError(f"Refusing to write to symlink: {mcp_config_path}")
    if not mcp_config_path.is_file():
        return
    try:
        payload = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return
    for srv_cfg in servers.values():
        if isinstance(srv_cfg, dict):
            srv_cfg["timeoutSeconds"] = int(timeout_seconds)
    _write_owner_only_file(
        mcp_config_path,
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
    )


def wire_antigravity_tool_server(
    handle: Any,
    directory: Path,
    *,
    server_name: str = "orin",
    system_prompt_file: Path | None = None,
    tool_timeout_ms: int | None = None,
    model: str | None = None,
    workspace_root: Path | str | None = None,
    additional_roots: Sequence[Path | str] = (),
    unrestricted: bool = False,
) -> ToolServerWiring:
    """Create isolated `.agents/agents/orin.md`, `.agents/mcp_config.json`, and `.agents/orin_settings.json`.

    For a model that perceives media, the native `read_file` is turned on behind a PreToolUse hook (the
    media gate): only media files (the model's own kinds), absolute paths, inside the workspace and the
    folders the person added (`additional_roots`); with `unrestricted`, anywhere on a local drive.
    """
    srv, clean_url, clean_token = _validate_mcp_server_params(
        server_name=server_name,
        url=getattr(handle, "url", None),
        token=getattr(handle, "token", None),
    )
    eff_timeout_ms = (
        tool_timeout_ms
        if tool_timeout_ms is not None
        else getattr(handle, "tool_timeout_ms", None)
    )
    timeout_seconds = _coerce_timeout_seconds(eff_timeout_ms)

    _validate_path_arg(directory, "directory")
    workdir = Path(directory)
    if _is_symlink_or_junction(workdir):
        raise ValueError(f"Refusing symlinked working directory: {workdir}")
    workdir.mkdir(parents=True, exist_ok=True)
    for doc_name in ("AGENTS.md", "GEMINI.md"):
        doc_candidate = workdir / doc_name
        if _is_symlink_or_junction(doc_candidate):
            raise ValueError(f"Refusing symlinked {doc_name}: {doc_candidate}")

    git_dir = workdir / ".git"
    if _is_symlink_or_junction(git_dir):
        raise ValueError(f"Refusing symlinked .git directory: {git_dir}")
    git_dir.mkdir(exist_ok=True)

    enable_media_gate = False
    resolved_ws_root: Path | None = None
    media_roots: tuple[Path, ...] = ()
    media_extensions: frozenset[str] = frozenset()
    if model is not None or workspace_root is not None:
        eff_model = model if model is not None else DEFAULT_ANTIGRAVITY_MODEL
        media_extensions = antigravity_native_media_extensions(eff_model)
        if media_extensions:
            raw_ws = workspace_root if workspace_root is not None else workdir
            _validate_path_arg(raw_ws, "workspace_root")
            ws_path = Path(raw_ws)
            if _is_symlink_or_junction(ws_path):
                raise ValueError(
                    f"Refusing symlinked workspace_root: {ws_path}"
                )
            ws_path.mkdir(parents=True, exist_ok=True)
            try:
                resolved_ws_root = ws_path.resolve()
            except OSError:
                resolved_ws_root = ws_path.absolute()
            extra_roots: list[Path | str] = []
            for extra in additional_roots or ():
                try:
                    _validate_path_arg(extra, "additional_root")
                except ValueError:
                    continue
                if not _is_symlink_or_junction(Path(extra)):
                    extra_roots.append(extra)
            media_roots = resolve_media_roots(resolved_ws_root, extra_roots)
            enable_media_gate = True

    sys_prompt = "You are a helpful assistant."
    if system_prompt_file is not None:
        sys_file_path = Path(system_prompt_file)
        if sys_file_path.is_symlink():
            raise ValueError(
                f"Refusing symlinked system_prompt_file: {sys_file_path}"
            )
        if sys_file_path.is_file():
            try:
                read_sys = sys_file_path.read_text(encoding="utf-8").strip()
                if read_sys:
                    sys_prompt = read_sys
            except Exception:
                pass

    agents_dir = workdir / ".agents"
    if _is_symlink_or_junction(agents_dir):
        raise ValueError(f"Refusing symlinked .agents directory: {agents_dir}")
    agents_subdir = agents_dir / "agents"
    if _is_symlink_or_junction(agents_subdir):
        raise ValueError(
            f"Refusing symlinked .agents/agents directory: {agents_subdir}"
        )
    agent_md_path = agents_subdir / "orin.md"
    mcp_config_path = agents_dir / "mcp_config.json"
    settings_file = agents_dir / "orin_settings.json"
    hooks_file = agents_dir / "hooks.json"
    gate_config_file = agents_dir / MEDIA_GATE_CONFIG_FILENAME
    wiring_files: tuple[Path, ...] = (
        (
            agent_md_path,
            mcp_config_path,
            settings_file,
            hooks_file,
            gate_config_file,
        )
        if enable_media_gate
        else (agent_md_path, mcp_config_path, settings_file)
    )

    try:
        for fpath in wiring_files:
            if fpath.is_symlink() or _is_symlink_or_junction(fpath):
                raise ValueError(f"Refusing to write to symlink: {fpath}")

        agent_md_content = _render_orin_agent_md(
            server_name=srv,
            url=clean_url,
            token=clean_token,
            system_prompt=sys_prompt,
            timeout_seconds=timeout_seconds,
            allow_native_media_read=enable_media_gate,
            workspace_root=resolved_ws_root,
            media_note=(
                antigravity_media_prompt_note(
                    resolved_ws_root,
                    extensions=media_extensions,
                    additional_roots=media_roots[1:],
                    unrestricted=bool(unrestricted),
                )
                if enable_media_gate and resolved_ws_root is not None
                else None
            ),
        )
        _write_owner_only_file(agent_md_path, agent_md_content)

        srv_entry: dict[str, Any] = {
            "url": clean_url,
            "headers": {"Authorization": f"Bearer {clean_token}"},
        }
        if timeout_seconds is not None:
            srv_entry["timeoutSeconds"] = timeout_seconds
        mcp_config_payload = {"mcpServers": {srv: srv_entry}}
        _write_owner_only_file(
            mcp_config_path,
            json.dumps(mcp_config_payload, ensure_ascii=True, indent=2) + "\n",
        )

        try:
            resolved_workdir = str(workdir.resolve())
        except OSError:
            resolved_workdir = str(workdir.absolute())

        if enable_media_gate and resolved_ws_root is not None:
            allow_rules = [f"mcp({srv}/*)"]
            if unrestricted:
                allow_rules.append("read_file(*)")
            else:
                allow_rules.extend(f"read_file({root.as_posix()})" for root in media_roots)
            deny_rules = [
                "command(*)",
                "write_file(*)",
                "read_url(*)",
                "execute_url(*)",
                "unsandboxed(*)",
            ]
        else:
            allow_rules = [f"mcp({srv}/*)"]
            deny_rules = [
                "command(*)",
                "read_file(*)",
                "write_file(*)",
                "read_url(*)",
                "execute_url(*)",
                "unsandboxed(*)",
            ]

        settings_payload = {
            "trustedWorkspaces": [resolved_workdir],
            "allowNonWorkspaceAccess": False,
            "enableTerminalSandbox": True,
            "disableSlashCommands": True,
            "permissions": {
                "allow": allow_rules,
                "deny": deny_rules,
            },
        }
        _write_owner_only_file(
            settings_file,
            json.dumps(settings_payload, ensure_ascii=True, indent=2) + "\n",
        )

        if enable_media_gate and resolved_ws_root is not None:
            hooks_payload = {
                "orin-media-gate": {
                    "PreToolUse": [
                        {
                            "matcher": "read_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": build_media_gate_hook_command(),
                                    "timeout": 10,
                                }
                            ],
                        }
                    ]
                }
            }
            _write_owner_only_file(
                hooks_file,
                json.dumps(hooks_payload, ensure_ascii=True, indent=2) + "\n",
            )
            gate_config_payload = {
                # `allowed_root` (the workspace) stays for readers of the single-root format.
                "allowed_root": str(resolved_ws_root),
                "allowed_roots": [str(root) for root in media_roots],
                "unrestricted": bool(unrestricted),
                "allowed_extensions": sorted(media_extensions),
                "max_bytes": DEFAULT_MAX_MEDIA_BYTES,
            }
            _write_owner_only_file(
                gate_config_file,
                json.dumps(gate_config_payload, ensure_ascii=True, indent=2)
                + "\n",
            )
    except Exception:
        for fpath in wiring_files:
            try:
                if not fpath.is_symlink():
                    fpath.unlink(missing_ok=True)
            except Exception:
                pass
        raise

    return ToolServerWiring(
        argv=("--agent", "orin", "--settings", str(settings_file)),
        env={},
        files=wiring_files,
    )


def wire_antigravity_no_tools(
    directory: Path,
    *,
    system_prompt_file: Path | None = None,
) -> ToolServerWiring:
    """Create locked-down `.agents/agents/orin.md` and `.agents/orin_settings.json` with zero tools for non-native stream fallback."""
    _validate_path_arg(directory, "directory")
    workdir = Path(directory)
    if _is_symlink_or_junction(workdir):
        raise ValueError(f"Refusing symlinked working directory: {workdir}")
    workdir.mkdir(parents=True, exist_ok=True)
    git_dir = workdir / ".git"
    if _is_symlink_or_junction(git_dir):
        raise ValueError(f"Refusing symlinked .git directory: {git_dir}")
    git_dir.mkdir(exist_ok=True)

    sys_prompt = "You are a helpful assistant."
    if system_prompt_file is not None:
        sys_file_path = Path(system_prompt_file)
        if _is_symlink_or_junction(sys_file_path):
            raise ValueError(f"Refusing symlinked system_prompt_file: {sys_file_path}")
        if sys_file_path.is_file():
            try:
                read_sys = sys_file_path.read_text(encoding="utf-8").strip()
                if read_sys:
                    sys_prompt = read_sys
            except Exception:
                pass

    agents_dir = workdir / ".agents"
    if _is_symlink_or_junction(agents_dir):
        raise ValueError(f"Refusing symlinked .agents directory: {agents_dir}")
    agents_subdir = agents_dir / "agents"
    if _is_symlink_or_junction(agents_subdir):
        raise ValueError(f"Refusing symlinked .agents/agents directory: {agents_subdir}")
    agent_md_path = agents_subdir / "orin.md"
    settings_file = agents_dir / "orin_settings.json"
    wiring_files = (agent_md_path, settings_file)

    try:
        agent_md_content = (
            "---\n"
            "name: orin\n"
            "description: Orin isolated CLI agent\n"
            "mainAgent: true\n"
            "excludeDefaultComponents: true\n"
            "inheritMcp: false\n"
            "tools: []\n"
            "---\n\n"
            "# Instructions\n\n"
            f"{sys_prompt.strip()}\n"
        )
        _write_owner_only_file(agent_md_path, agent_md_content)
        try:
            resolved_workdir = str(workdir.resolve())
        except OSError:
            resolved_workdir = str(workdir.absolute())
        settings_payload = {
            "trustedWorkspaces": [resolved_workdir],
            "allowNonWorkspaceAccess": False,
            "enableTerminalSandbox": True,
            "disableSlashCommands": True,
            "permissions": {
                "allow": [],
                "deny": [
                    "command(*)",
                    "read_file(*)",
                    "write_file(*)",
                    "read_url(*)",
                    "execute_url(*)",
                    "unsandboxed(*)",
                ],
            },
        }
        _write_owner_only_file(
            settings_file,
            json.dumps(settings_payload, ensure_ascii=True, indent=2) + "\n",
        )
    except Exception:
        for fpath in wiring_files:
            try:
                if not _is_symlink_or_junction(fpath):
                    fpath.unlink(missing_ok=True)
            except Exception:
                pass
        raise

    return ToolServerWiring(
        argv=("--agent", "orin", "--settings", str(settings_file)),
        env={},
        files=wiring_files,
    )


def build_antigravity_command(
    request: CliTurnRequest,
    wiring: ToolServerWiring | None = None,
    *,
    supported_efforts: Sequence[str] | None = None,
) -> list[str]:
    """Build the isolated `agy` command line for headless `stream-json` execution."""
    binary_str = _validate_path_arg(request.binary, "binary")
    _validate_path_arg(request.system_prompt_file, "system_prompt_file")
    cleaned_model = clean_antigravity_model_id(request.model)
    model = (
        DEFAULT_ANTIGRAVITY_MODEL
        if not cleaned_model or cleaned_model == "antigravity-image"
        else cleaned_model
    )

    if request.session_id is not None:
        raw_sess = str(request.session_id)
        if "\x00" in raw_sess or "\n" in raw_sess or "\r" in raw_sess:
            raise ValueError(
                f"Invalid session_id for Antigravity CLI: {request.session_id!r}"
            )
        sess_id = raw_sess.strip()
        if (
            not sess_id
            or sess_id.startswith("-")
            or not _SAFE_SESSION_ID_RE.fullmatch(sess_id)
        ):
            raise ValueError(
                f"Invalid session_id for Antigravity CLI: {request.session_id!r}"
            )

    eff_wiring = (
        wiring
        if wiring is not None
        else (request.tool_server_wiring or request.wiring)
    )
    if eff_wiring is None and request.tool_server_config_file is not None:
        cfg_path = Path(
            _validate_path_arg(
                request.tool_server_config_file, "tool_server_config_file"
            )
        )
        if cfg_path.name == "orin.md":
            agents_root = cfg_path.parent.parent
            settings_candidate = agents_root / "orin_settings.json"
            mcp_candidate = agents_root / "mcp_config.json"
            eff_wiring = ToolServerWiring(
                argv=("--agent", "orin", "--settings", str(settings_candidate)),
                env={},
                files=(cfg_path, mcp_candidate, settings_candidate),
            )
        elif cfg_path.name in ("mcp_config.json", "orin_settings.json"):
            agents_root = cfg_path.parent
            agent_candidate = agents_root / "agents" / "orin.md"
            settings_candidate = agents_root / "orin_settings.json"
            mcp_candidate = agents_root / "mcp_config.json"
            eff_wiring = ToolServerWiring(
                argv=("--agent", "orin", "--settings", str(settings_candidate)),
                env={},
                files=(agent_candidate, mcp_candidate, settings_candidate),
            )

    prompt_file = Path(request.system_prompt_file)
    if prompt_file.is_symlink():
        raise ValueError(f"Refusing symlinked system_prompt_file: {prompt_file}")

    cmd: list[str] = [
        binary_str,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--disable-slash-commands",
        "--model",
        model,
    ]

    if request.effort is not None and str(request.effort).strip():
        raw_effort = str(request.effort)
        if "\x00" in raw_effort or "\n" in raw_effort or "\r" in raw_effort:
            raise ValueError(
                f"Invalid effort level for Antigravity CLI: {request.effort!r}"
            )
        effort = raw_effort.strip().lower()
        if effort not in VALID_EFFORT_LEVELS:
            raise ValueError(
                f"Invalid effort level for Antigravity CLI: {request.effort!r}"
            )
        model_effort_suffix: str | None = None
        lower_model = model.lower()
        for eff_name in VALID_EFFORT_LEVELS:
            if lower_model.endswith(f"-{eff_name}"):
                model_effort_suffix = eff_name
                break
        effective_efforts: Sequence[str] = (
            supported_efforts
            if supported_efforts is not None and len(supported_efforts) > 0
            else (
                (model_effort_suffix,)
                if model_effort_suffix is not None
                else ("low", "medium", "high")
            )
        )
        allowed_by_model = effort in effective_efforts and (
            model_effort_suffix is None or effort == model_effort_suffix
        )
        if allowed_by_model:
            cmd.extend(["--effort", effort])

    if request.resume_session_id is not None:
        raw_resume = str(request.resume_session_id)
        if "\x00" in raw_resume or "\n" in raw_resume or "\r" in raw_resume:
            raise ValueError(
                f"Invalid resume_session_id for Antigravity CLI: {request.resume_session_id!r}"
            )
        resume_id = raw_resume.strip()
        if (
            not resume_id
            or resume_id.startswith("-")
            or not _SAFE_SESSION_ID_RE.fullmatch(resume_id)
        ):
            raise ValueError(
                f"Invalid resume_session_id for Antigravity CLI: {request.resume_session_id!r}"
            )
        cmd.extend(["--conversation", resume_id])

    if eff_wiring is not None and eff_wiring.argv:
        wiring_argv = list(eff_wiring.argv)
        idx = 0
        while idx < len(wiring_argv):
            token = str(wiring_argv[idx])
            if token == "--agent" and idx + 1 < len(wiring_argv):
                agent_val = str(wiring_argv[idx + 1])
                if agent_val != "orin":
                    raise ValueError(
                        f"ToolServerWiring for Antigravity CLI only permits '--agent orin', got: {agent_val!r}"
                    )
                cmd.extend(["--agent", "orin"])
                idx += 2
            elif token == "--settings" and idx + 1 < len(wiring_argv):
                settings_val = _validate_path_arg(
                    wiring_argv[idx + 1], "settings"
                )
                cmd.extend(["--settings", settings_val])
                idx += 2
            else:
                raise ValueError(
                    f"ToolServerWiring for Antigravity CLI only permits '--agent orin' and '--settings <path>', got: {token!r}"
                )

    req_timeout_seconds = _coerce_timeout_seconds(request.tool_timeout_ms)
    if prompt_file.is_file():
        try:
            prompt_text = prompt_file.read_text(encoding="utf-8").strip()
        except Exception:
            prompt_text = ""
        if prompt_text and eff_wiring is not None:
            for fpath in eff_wiring.files:
                if Path(fpath).name == "orin.md":
                    _update_agent_md_instructions(
                        Path(fpath),
                        prompt_text,
                        timeout_seconds=req_timeout_seconds,
                        workspace_root=request.workspace_root,
                    )
                    break
    if req_timeout_seconds is not None and eff_wiring is not None:
        for fpath in eff_wiring.files:
            if Path(fpath).name == "mcp_config.json":
                _update_mcp_config_timeout(Path(fpath), req_timeout_seconds)
                break

    return cmd


ANTIGRAVITY_OMITTED_MEDIA_NOTE = (
    "[Imagem/mídia omitida: o Antigravity CLI não recebe imagens na mensagem. "
    "Para ver um arquivo visual, chame sua ferramenta nativa read_file com o caminho ABSOLUTO dele "
    "(a ferramenta view_file do Orin informa esse caminho).]"
)

_MEDIA_KEYS: tuple[str, ...] = (
    "image",
    "image_url",
    "input_image",
    "document",
    "audio",
    "video",
)

_AUDIO_VIDEO_EXTENSIONS: frozenset[str] = (
    ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS
)


def format_antigravity_workspace_media_note(abs_path: Path | str) -> str:
    """Return a pt-BR note directing the model to use native `read_file` for a workspace image/audio/video file."""
    path = Path(abs_path).resolve()
    resolved_posix = path.as_posix()
    if path.suffix.lower() in ALLOWED_IMAGE_EXTENSIONS:
        return (
            f"[Imagem no workspace: {resolved_posix} — "
            f"o Antigravity CLI não recebe imagem embutida na mensagem. "
            f'Para ver a imagem, chame sua ferramenta nativa read_file com o caminho absoluto "{resolved_posix}".]'
        )
    return (
        f"[Arquivo de áudio/vídeo no workspace: {resolved_posix} — "
        f"o Antigravity CLI não recebe áudio/vídeo embutido na mensagem. "
        f'Para ouvir o áudio ou assistir ao vídeo, chame sua ferramenta nativa read_file com o caminho absoluto "{resolved_posix}"; '
        f"se o seu modelo não ouve áudio/vídeo, chame a ferramenta view_file do Orin com esse mesmo caminho, "
        f"que devolve a transcrição.]"
    )


def _extract_workspace_media_path(
    block: Mapping[str, Any],
    workspace_root: Path | str | None,
) -> Path | None:
    raw_candidates: list[str] = []
    for key in ("path", "file_path", "workspace_path", "uri", "url"):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            raw_candidates.append(val.strip())

    for nested_key in ("source", "audio", "video", "document"):
        nested = block.get(nested_key)
        if isinstance(nested, str) and nested.strip():
            raw_candidates.append(nested.strip())
        elif isinstance(nested, Mapping):
            for key in ("path", "file_path", "workspace_path", "uri", "url"):
                val = nested.get(key)
                if isinstance(val, str) and val.strip():
                    raw_candidates.append(val.strip())

    if not raw_candidates:
        return None

    ws_root_path: Path | None = None
    if workspace_root is not None:
        try:
            ws_root_path = Path(workspace_root).resolve()
        except OSError:
            ws_root_path = None

    for raw in raw_candidates:
        cleaned = raw
        if cleaned.startswith("workspace://"):
            cleaned = cleaned[len("workspace://") :].lstrip("/")
        elif cleaned.startswith(("http://", "https://", "data:")):
            continue
        elif cleaned.startswith("file://"):
            cleaned = cleaned[len("file://") :]
            if os.name == "nt" and cleaned.startswith("/") and len(cleaned) >= 3 and cleaned[2] == ":":
                cleaned = cleaned[1:]

        if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
            continue
        try:
            cand = Path(cleaned)
        except Exception:
            continue

        if not cand.is_absolute():
            if ws_root_path is None:
                continue
            cand = ws_root_path / cand

        eff_root = ws_root_path if ws_root_path is not None else cand.parent
        ok, _ = is_allowed_media_path(
            str(cand),
            allowed_root=eff_root,
            allowed_extensions=ALLOWED_MEDIA_EXTENSIONS,
        )
        if ok:
            try:
                return cand.resolve(strict=True)
            except OSError:
                continue
    return None


def encode_antigravity_user_turn(
    content: list[dict[str, Any]] | str,
    *,
    workspace_root: Path | str | None = None,
) -> bytes:
    """Encode a user turn for `agy --input-format stream-json`: text only.

    agy 1.5.x rejects any non-text block in a user message (`only "text"`); it sees an
    image only as an MCP tool result (view_file). A media block here is history replayed
    after a restart (the image a view_file call returned, kept as a user message) or the
    text-protocol fallback: it becomes a short text note, never bytes and never an error.
    When an image/audio/video block points to a file on disk in the workspace, the note includes
    its absolute path and instructs the model to call its native `read_file` tool.
    """
    text_blocks: list[dict[str, str]] = []
    if isinstance(content, str):
        text_blocks.append({"type": "text", "text": content})
    elif isinstance(content, Sequence):
        omitted_pending = False
        for block in content:
            if isinstance(block, str):
                text_blocks.append({"type": "text", "text": block})
                continue
            if not isinstance(block, Mapping):
                omitted_pending = True
                continue
            btype = str(block.get("type") or "text").strip().lower()
            if (
                btype in _UNSUPPORTED_MEDIA_TYPES
                or btype not in ("text", "input_text")
                or any(media_key in block for media_key in _MEDIA_KEYS)
            ):
                media_path = _extract_workspace_media_path(block, workspace_root)
                if media_path is not None:
                    if omitted_pending:
                        text_blocks.append(
                            {"type": "text", "text": ANTIGRAVITY_OMITTED_MEDIA_NOTE}
                        )
                        omitted_pending = False
                    text_blocks.append(
                        {
                            "type": "text",
                            "text": format_antigravity_workspace_media_note(
                                media_path
                            ),
                        }
                    )
                else:
                    omitted_pending = True
                continue
            if omitted_pending:
                text_blocks.append({"type": "text", "text": ANTIGRAVITY_OMITTED_MEDIA_NOTE})
                omitted_pending = False
            text_val = block.get("text")
            text_blocks.append(
                {
                    "type": "text",
                    "text": "" if text_val is None else str(text_val),
                }
            )
        if omitted_pending:
            text_blocks.append({"type": "text", "text": ANTIGRAVITY_OMITTED_MEDIA_NOTE})
    else:
        raise ValueError(
            "O Antigravity CLI (agy) aceita apenas blocos de texto no modo stream-json; "
            "envio de imagens ou mídia não é suportado."
        )

    payload = {
        "event": "user",
        "message": {
            "content": text_blocks,
        },
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def redact_antigravity_argv(argv: Sequence[str]) -> list[str]:
    """Redact `--settings` paths/JSON and sensitive flags before logging."""
    redacted: list[str] = []
    redact_settings_next = False
    redact_generic_next = False

    for item in argv:
        text = str(item)
        if redact_settings_next:
            redacted.append("<redacted-settings>")
            redact_settings_next = False
            continue
        if redact_generic_next:
            redacted.append("<redacted>")
            redact_generic_next = False
            continue
        if text == "--settings":
            redacted.append(text)
            redact_settings_next = True
            continue
        if text.startswith("--settings="):
            redacted.append("--settings=<redacted-settings>")
            continue
        if text in {
            "-p",
            "--prompt",
            "--system-prompt",
            "--mcp-config",
            "--system-prompt-file",
        }:
            redacted.append(text)
            redact_generic_next = True
            continue
        if text.startswith(
            (
                "--prompt=",
                "--system-prompt=",
                "--mcp-config=",
                "--system-prompt-file=",
            )
        ):
            prefix = text.split("=", 1)[0]
            redacted.append(f"{prefix}=<redacted>")
            continue
        lowered = text.lower()
        if any(term in lowered for term in _SENSITIVE_TERMS):
            if "=" in text and text.startswith("-"):
                prefix = text.split("=", 1)[0]
                redacted.append(f"{prefix}=<redacted>")
            elif text.startswith("-"):
                redacted.append(text)
                redact_generic_next = True
            else:
                redacted.append("<redacted>")
            continue
        redacted.append(text)

    return redacted


__all__ = [
    "ANTIGRAVITY_OMITTED_MEDIA_NOTE",
    "DEFAULT_ANTIGRAVITY_MODEL",
    "MAX_ANTIGRAVITY_MAJOR",
    "MIN_ANTIGRAVITY_VERSION",
    "VALID_EFFORT_LEVELS",
    "antigravity_media_prompt_note",
    "antigravity_native_media_extensions",
    "antigravity_native_media_policy",
    "antigravity_version",
    "build_antigravity_command",
    "build_isolated_antigravity_env",
    "clean_antigravity_model_id",
    "encode_antigravity_user_turn",
    "format_antigravity_workspace_media_note",
    "is_antigravity_gemini_model",
    "is_antigravity_version_supported",
    "redact_antigravity_argv",
    "resolve_antigravity_binary",
    "resolve_antigravity_home",
    "resolve_media_roots",
    "wire_antigravity_tool_server",
]
