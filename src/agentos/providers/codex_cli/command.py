from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence

from agentos.providers.cli_adapter import CliTurnRequest, ToolServerWiring

MIN_CODEX_VERSION: tuple[int, int, int] = (0, 167, 0)
MAX_CODEX_MAJOR: int = 1
ORIN_MCP_TOKEN_ENV: str = "ORIN_MCP_TOKEN"
DEFAULT_CODEX_MODEL: str = "gpt-6.1-sol"
VALID_EFFORT_LEVELS: frozenset[str] = frozenset(
    {"low", "medium", "high", "xhigh", "max", "ultra"}
)

_ALLOWLISTED_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "USERPROFILE",
    "HOME",
    "CODEX_HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "PATHEXT",
    "COMSPEC",
    "WINDIR",
)

_FORBIDDEN_API_KEY_ENVS: frozenset[str] = frozenset(
    {
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
)

_SAFE_MODEL_RE = re.compile(r"\A[A-Za-z0-9._:\-]{1,128}\Z")
_SAFE_SERVER_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
_SAFE_SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9._:\-]{1,128}\Z")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_WINDOWS_APPS_PKG_RE = re.compile(r"\AOpenAI\.Codex_(\d+(?:\.\d+)*)")

_SENSITIVE_CONFIG_TERMS: tuple[str, ...] = (
    "token",
    "key",
    "secret",
    "password",
    "bearer",
    "authorization",
    "model_instructions_file",
    "system_prompt",
)


def clean_codex_model_id(model: str | None) -> str | None:
    """Validate and normalize a Codex CLI model identifier, stripping optional `openai-codex/` prefix."""
    if model is None:
        return None
    if not isinstance(model, str):
        raise ValueError(f"Invalid model identifier for Codex CLI: {model!r}")
    cleaned = model.strip()
    if not cleaned:
        return None
    if "\n" in model or "\r" in model or "\x00" in model:
        raise ValueError(f"Invalid model identifier for Codex CLI: {model!r}")
    if cleaned.startswith("openai-codex/"):
        cleaned = cleaned[len("openai-codex/") :]
    if (
        not cleaned
        or cleaned.startswith("-")
        or not _SAFE_MODEL_RE.fullmatch(cleaned)
    ):
        raise ValueError(f"Invalid model identifier for Codex CLI: {model!r}")
    return cleaned


def _validate_path_arg(value: str | Path, label: str) -> str:
    if not isinstance(value, (str, Path)):
        raise ValueError(f"Invalid {label} path")
    raw = str(value)
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError(f"Invalid {label} path")
    text = raw.strip()
    if not text or text.startswith("-"):
        raise ValueError(f"Invalid {label} path")
    return text


def _extract_windows_apps_version(folder_name: str) -> tuple[int, ...]:
    match = _WINDOWS_APPS_PKG_RE.match(folder_name)
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return ()


def resolve_codex_binary(
    env: Mapping[str, str] | None = None,
    *,
    windows_apps_dirs: Sequence[Path] | None = None,
) -> str | None:
    """Dynamically resolve the Codex CLI binary without caching across calls.

    1. Checks `PATH` first via `shutil.which("codex", ...)`, upgrading `.cmd`/`.bat`/`.ps1`
       wrappers to their sibling native PE (`codex.exe`) when present.
    2. Falls back to `WindowsApps\\OpenAI.Codex_*\\app\\resources\\codex.exe`, picking
       the highest version dynamically.
    """
    source_env: Mapping[str, str] = os.environ if env is None else env
    path_arg = (
        os.environ.get("PATH") if env is None else source_env.get("PATH", "")
    )

    found = shutil.which("codex", path=path_arg)
    if found:
        found_path = Path(found)
        if found_path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            npm_vendor_exe = (
                found_path.parent
                / "node_modules"
                / "@openai"
                / "codex"
                / "node_modules"
                / "@openai"
                / "codex-win32-x64"
                / "vendor"
                / "x86_64-pc-windows-msvc"
                / "bin"
                / "codex.exe"
            )
            if npm_vendor_exe.is_file():
                return str(npm_vendor_exe)
            direct_exe = found_path.with_suffix(".exe")
            if direct_exe.is_file():
                return str(direct_exe)
        return str(found_path)

    candidate_roots: list[Path] = []
    if windows_apps_dirs is not None:
        candidate_roots.extend(Path(p) for p in windows_apps_dirs)
    else:
        override_dir = source_env.get("ORIN_WINDOWS_APPS_DIR", "").strip()
        if override_dir:
            candidate_roots.append(Path(override_dir))
        prog_files = source_env.get("ProgramFiles", "").strip()
        if prog_files:
            candidate_roots.append(Path(prog_files) / "WindowsApps")
        local_appdata = source_env.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            candidate_roots.append(
                Path(local_appdata) / "Microsoft" / "WindowsApps"
            )
        if os.name == "nt":
            default_win_apps = Path(r"C:\Program Files\WindowsApps")
            if default_win_apps not in candidate_roots:
                candidate_roots.append(default_win_apps)

    matches: list[tuple[tuple[int, ...], float, str]] = []
    for root in candidate_roots:
        try:
            if not root.is_dir():
                continue
            pkg_dirs = list(root.glob("OpenAI.Codex_*"))
        except OSError:
            continue

        for pkg_dir in pkg_dirs:
            try:
                candidate_exe = pkg_dir / "app" / "resources" / "codex.exe"
                if not candidate_exe.is_file():
                    continue
                ver_tuple = _extract_windows_apps_version(pkg_dir.name)
                try:
                    mtime = float(candidate_exe.stat().st_mtime)
                except OSError:
                    mtime = 0.0
                matches.append((ver_tuple, mtime, str(candidate_exe)))
            except OSError:
                continue

    if not matches:
        return None

    matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return matches[0][2]


def build_isolated_codex_env(
    base: Mapping[str, str] | None = None,
    *,
    mcp_token: str | None = None,
) -> dict[str, str]:
    """Build a strict allowlisted environment for `codex exec`, stripping cloud API keys."""
    source: Mapping[str, str] = os.environ if base is None else base
    isolated: dict[str, str] = {}

    # Case-insensitive lookup helper for Windows environment mappings while preserving canonical key names
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

    isolated["PYTHONIOENCODING"] = "utf-8"
    if mcp_token is not None and str(mcp_token):
        token_str = str(mcp_token)
        if "\x00" in token_str or "\n" in token_str or "\r" in token_str:
            raise ValueError("Invalid mcp_token containing control characters")
        isolated[ORIN_MCP_TOKEN_ENV] = token_str

    return isolated


def codex_version(
    binary: str | None = None,
    *,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int, int] | None:
    """Return `(major, minor, patch)` of `codex --version` or `None` if unavailable."""
    resolved = binary or resolve_codex_binary(env)
    if not resolved:
        return None

    isolated_env = build_isolated_codex_env(env)
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


def is_codex_version_supported(
    version: tuple[int, int, int] | None,
) -> bool:
    """Return True when `version` is within `[MIN_CODEX_VERSION, (MAX_CODEX_MAJOR, 0, 0))`."""
    return (
        version is not None
        and MIN_CODEX_VERSION <= version < (MAX_CODEX_MAJOR, 0, 0)
    )


def wire_codex_tool_server(
    handle: Any,
    server_name: str = "orin",
) -> ToolServerWiring:
    """Produce `-c mcp_servers.<name>...` overrides and env var mapping for the Orin MCP server."""
    srv = str(server_name or "").strip()
    if not srv or not _SAFE_SERVER_NAME_RE.fullmatch(srv):
        raise ValueError(f"Invalid Codex MCP server_name: {server_name!r}")

    raw_url = getattr(handle, "url", None)
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("Invalid MCP handle.url: must be a non-empty string")
    if "\x00" in raw_url or "\n" in raw_url or "\r" in raw_url:
        raise ValueError("Invalid MCP handle.url: control characters forbidden")

    raw_token = getattr(handle, "token", None)
    if not isinstance(raw_token, str) or not raw_token.strip():
        raise ValueError("Invalid MCP handle.token: must be a non-empty string")
    if "\x00" in raw_token or "\n" in raw_token or "\r" in raw_token:
        raise ValueError(
            "Invalid MCP handle.token: control characters forbidden"
        )

    argv = (
        "-c",
        f"mcp_servers.{srv}.url={json.dumps(str(raw_url))}",
        "-c",
        f'mcp_servers.{srv}.bearer_token_env_var="{ORIN_MCP_TOKEN_ENV}"',
        "-c",
        f"mcp_servers.{srv}.required=true",
    )
    return ToolServerWiring(
        argv=argv,
        env={ORIN_MCP_TOKEN_ENV: str(raw_token)},
        files=(),
    )


def build_codex_command(
    request: CliTurnRequest,
    wiring: ToolServerWiring | None = None,
    *,
    supported_efforts: Sequence[str] | None = None,
) -> list[str]:
    """Build the isolated `codex exec` command line for a new or resumed turn."""
    binary_str = _validate_path_arg(request.binary, "binary")
    prompt_file_str = _validate_path_arg(
        request.system_prompt_file, "system_prompt_file"
    )
    model = clean_codex_model_id(request.model) or DEFAULT_CODEX_MODEL

    cmd: list[str] = [binary_str, "exec"]

    if request.resume_session_id is not None:
        raw_resume = str(request.resume_session_id)
        if "\x00" in raw_resume or "\n" in raw_resume or "\r" in raw_resume:
            raise ValueError(
                f"Invalid resume_session_id for Codex CLI: {request.resume_session_id!r}"
            )
        resume_id = raw_resume.strip()
        if (
            not resume_id
            or resume_id.startswith("-")
            or not _SAFE_SESSION_ID_RE.fullmatch(resume_id)
        ):
            raise ValueError(
                f"Invalid resume_session_id for Codex CLI: {request.resume_session_id!r}"
            )
        cmd.extend(["resume", resume_id])

    cmd.extend(
        [
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
        ]
    )

    if not request.persist_session:
        cmd.append("--ephemeral")

    cmd.extend(["-m", model])

    base_c_values: list[str] = [
        f"model_instructions_file={json.dumps(prompt_file_str)}",
        "features.shell_tool=false",
        'web_search="disabled"',
        "features.view_image=false",
        'sandbox_mode="read-only"',
        'approval_policy="never"',
    ]

    if request.effort is not None and str(request.effort).strip():
        effort = str(request.effort).strip().lower()
        allowed_by_model = (
            supported_efforts is None
            or len(supported_efforts) == 0
            or effort in supported_efforts
        )
        if effort in VALID_EFFORT_LEVELS and allowed_by_model:
            base_c_values.append(f'model_reasoning_effort="{effort}"')

    seen_c_values: set[str] = set()
    for c_val in base_c_values:
        cmd.extend(["-c", c_val])
        seen_c_values.add(c_val)

    eff_wiring = (
        wiring
        if wiring is not None
        else (request.tool_server_wiring or request.wiring)
    )
    if eff_wiring is not None and eff_wiring.argv:
        idx = 0
        wiring_argv = list(eff_wiring.argv)
        while idx < len(wiring_argv):
            token = str(wiring_argv[idx])
            if token == "-c" and idx + 1 < len(wiring_argv):
                val = str(wiring_argv[idx + 1])
                if val not in seen_c_values:
                    cmd.extend(["-c", val])
                    seen_c_values.add(val)
                idx += 2
            else:
                cmd.append(token)
                idx += 1

    cmd.append("-")
    return cmd


def redact_codex_argv(argv: Sequence[str]) -> list[str]:
    """Redact sensitive `-c`/`--config` values and file flags before logging."""
    redacted: list[str] = []
    check_c_next = False
    skip_next = False

    for item in argv:
        text = str(item)
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        if check_c_next:
            check_c_next = False
            lowered = text.lower()
            if any(term in lowered for term in _SENSITIVE_CONFIG_TERMS):
                redacted.append("<redacted>")
            else:
                redacted.append(text)
            continue
        if text in {"--mcp-config", "--system-prompt-file"}:
            redacted.append(text)
            skip_next = True
            continue
        if text.startswith(("--mcp-config=", "--system-prompt-file=")):
            prefix = text.split("=", 1)[0]
            redacted.append(f"{prefix}=<redacted>")
            continue
        if text in {"-c", "--config"}:
            redacted.append(text)
            check_c_next = True
            continue
        if text.startswith(("-c=", "--config=")):
            lowered = text.lower()
            if any(term in lowered for term in _SENSITIVE_CONFIG_TERMS):
                prefix = text.split("=", 1)[0]
                redacted.append(f"{prefix}=<redacted>")
                continue
        redacted.append(text)

    return redacted


def encode_codex_user_turn(content: list[dict[str, Any]] | str) -> bytes:
    """Encode user turn content as single-line UTF-8 bytes terminated by `\\n`."""
    if isinstance(content, str):
        raw_text = content
    else:
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping):
                btype = block.get("type")
                if btype in (None, "text", "input_text") and "text" in block:
                    text_val = block.get("text")
                    if text_val is not None:
                        parts.append(str(text_val))
            elif isinstance(block, str):
                parts.append(block)
        raw_text = "\n".join(parts)

    single_line = (
        raw_text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    )
    return (single_line + "\n").encode("utf-8")


__all__ = [
    "DEFAULT_CODEX_MODEL",
    "MAX_CODEX_MAJOR",
    "MIN_CODEX_VERSION",
    "ORIN_MCP_TOKEN_ENV",
    "VALID_EFFORT_LEVELS",
    "build_codex_command",
    "build_isolated_codex_env",
    "clean_codex_model_id",
    "codex_version",
    "encode_codex_user_turn",
    "is_codex_version_supported",
    "redact_codex_argv",
    "resolve_codex_binary",
    "wire_codex_tool_server",
]
