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


_FSCTL_GET_REPARSE_POINT = 0x000900A8
_IO_REPARSE_TAG_APPEXECLINK = 0x8000001B


def _read_app_exec_link_target(alias: Path) -> str | None:
    """Target path of a Windows app execution alias (an AppExecLink reparse point), or None.

    ``os.readlink`` does not read this reparse type, and ``C:\\Program Files\\WindowsApps`` cannot be
    listed by a normal user, so an alias is the only way to learn where a Store package is installed
    without spawning PowerShell.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.restype = wintypes.HANDLE
        create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        device_io_control = kernel32.DeviceIoControl
        device_io_control.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                      wintypes.LPVOID]
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        # No access rights needed to read reparse data; OPEN_EXISTING, OPEN_REPARSE_POINT | BACKUP_SEMANTICS.
        handle = create_file(str(alias), 0, 0x7, None, 3, 0x00200000 | 0x02000000, None)
        if not handle or handle == wintypes.HANDLE(-1).value:
            return None
        try:
            buffer = ctypes.create_string_buffer(16 * 1024)
            returned = wintypes.DWORD()
            if not device_io_control(handle, _FSCTL_GET_REPARSE_POINT, None, 0, buffer, len(buffer),
                                     ctypes.byref(returned), None):
                return None
            raw = buffer.raw[: returned.value]
        finally:
            close_handle(handle)
    except (OSError, AttributeError, ValueError):
        return None
    if len(raw) < 12 or int.from_bytes(raw[0:4], "little") != _IO_REPARSE_TAG_APPEXECLINK:
        return None
    # Version (4 bytes) then NUL-separated UTF-16 strings: package family, app user model id, target path, ...
    for part in raw[12:].decode("utf-16-le", errors="replace").split("\0"):
        candidate = part.strip()
        if candidate.lower().endswith(".exe") and Path(candidate).is_absolute():
            return candidate
    return None


def _codex_package_dirs_from_aliases(alias_root: Path) -> list[Path]:
    """Install folders of the Codex Store package, learned from its app execution aliases."""
    aliases: list[Path] = []
    try:
        if not alias_root.is_dir():
            return []
        for family_dir in alias_root.glob("OpenAI.Codex_*"):
            if family_dir.is_dir():
                aliases.extend(family_dir.glob("*.exe"))
        aliases.extend(alias_root.glob("*codex*.exe"))
    except OSError:
        return []
    package_dirs: list[Path] = []
    for alias in aliases:
        target = _read_app_exec_link_target(alias)
        if not target:
            continue
        for parent in Path(target).parents:
            if _WINDOWS_APPS_PKG_RE.match(parent.name) and parent.parent.name.lower() == "windowsapps":
                if parent not in package_dirs:
                    package_dirs.append(parent)
                break
    return package_dirs


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
        else:
            prog_files = source_env.get("ProgramFiles", "").strip()
            if prog_files:
                candidate_roots.append(Path(prog_files) / "WindowsApps")
            local_appdata = source_env.get("LOCALAPPDATA", "").strip()
            if local_appdata:
                candidate_roots.append(
                    Path(local_appdata) / "Microsoft" / "WindowsApps"
                )
            if env is None and os.name == "nt":
                default_win_apps = Path(r"C:\Program Files\WindowsApps")
                if default_win_apps not in candidate_roots:
                    candidate_roots.append(default_win_apps)

    alias_package_dirs: list[Path] = []
    if windows_apps_dirs is None and os.name == "nt":
        local_appdata = source_env.get("LOCALAPPDATA", "").strip()
        if local_appdata:
            # The package folder itself usually cannot be listed (WindowsApps ACL), and newer Codex app
            # packages ship no `codex.exe` alias; any of the package's aliases still names its folder.
            alias_package_dirs = _codex_package_dirs_from_aliases(
                Path(local_appdata) / "Microsoft" / "WindowsApps"
            )

    matches: list[tuple[tuple[int, ...], float, str]] = []
    package_groups: list[list[Path]] = []
    for root in candidate_roots:
        try:
            if not root.is_dir():
                continue
            package_groups.append(list(root.glob("OpenAI.Codex_*")))
        except OSError:
            continue
    package_groups.append(alias_package_dirs)

    seen_exes: set[str] = set()
    for pkg_dir in (pkg_dir for group in package_groups for pkg_dir in group):
        try:
            candidate_exe = pkg_dir / "app" / "resources" / "codex.exe"
            if str(candidate_exe) in seen_exes or not candidate_exe.is_file():
                continue
            seen_exes.add(str(candidate_exe))
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


# Config that keeps Codex from describing its own sandbox/environment to the model and from exposing native tools Orin
# does not use (verified on codex 0.167.0-alpha.4.1 and 0.167.2; Codex ignores unknown keys silently, so tests check
# the effect on the rollout, not only the flag). Do not add `features.code_mode_host=false` (it breaks every MCP call)
# or the `multi_agent_v2.*` keys (they differ between 0.167 builds).
CODEX_NEUTRAL_CONFIG: tuple[str, ...] = (
    "include_permissions_instructions=false",
    "include_environment_context=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    "features.unified_exec=false",
    "features.unified_exec_tty=false",
    "features.computer_use=false",
    "features.browser_use=false",
    "features.apps=false",
    "features.plugins=false",
    "features.sleep_tool=false",
    "features.skill_mcp_dependency_install=false",
    "features.skip_host_skill_discovery=true",
    # skip_host_skill_discovery is "under development": without this Codex emits a warning item (type "error") per run.
    "suppress_unstable_features_warning=true",
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
        # Orin asks for permission itself, inside each tool (the turn's permission broker), as it does for Claude
        # (--allowedTools) and Antigravity (permissions.allow). Left to Codex, a tool not marked read-only needs an
        # approval that approval_policy="never" turns into a refusal.
        "-c",
        f'mcp_servers.{srv}.default_tools_approval_mode="approve"',
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
    cleaned_model = clean_codex_model_id(request.model)
    model = (
        DEFAULT_CODEX_MODEL
        if not cleaned_model or cleaned_model == "codex-image"
        else cleaned_model
    )

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
        "features.image_generation=false",
        # Codex's own subagents would bypass Orin's tree, budget and permissions: delegation goes through Agent.
        "features.multi_agent=false",
        # sandbox/approval stay as they are: they hold back Codex's native apply_patch. What they make Codex tell the
        # model ("permissions instructions", the <environment_context> permission profile: read-only, commands
        # rejected) is turned off below, because the model believed it and refused to act through Orin's tools.
        'sandbox_mode="read-only"',
        'approval_policy="never"',
        *CODEX_NEUTRAL_CONFIG,
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

    locked_c_keys: set[str] = {
        "model_instructions_file",
        "features.shell_tool",
        "web_search",
        "features.view_image",
        "features.image_generation",
        "features.multi_agent",
        "sandbox_mode",
        "approval_policy",
        *(value.split("=", 1)[0] for value in CODEX_NEUTRAL_CONFIG),
    }
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
                c_key = val.split("=", 1)[0].strip()
                if c_key in locked_c_keys and val not in seen_c_values:
                    raise ValueError(
                        f"ToolServerWiring cannot override locked Codex config key: {c_key!r}"
                    )
                if val not in seen_c_values:
                    cmd.extend(["-c", val])
                    seen_c_values.add(val)
                idx += 2
            else:
                raise ValueError(
                    f"ToolServerWiring for Codex CLI only permits '-c key=val' pairs, got: {token!r}"
                )
        server = str(request.tool_server_name or "orin").strip()
        timeout_ms = request.tool_timeout_ms
        if (
            isinstance(timeout_ms, int)
            and not isinstance(timeout_ms, bool)
            and timeout_ms > 0
            and _SAFE_SERVER_NAME_RE.fullmatch(server)
        ):
            # Codex cuts an MCP call at ~60 s by default; an Agent call (a whole subagent) may take the rest of the
            # turn, as Claude's MCP_TOOL_TIMEOUT allows.
            cmd.extend(["-c", f"mcp_servers.{server}.tool_timeout_sec={max(1, -(-timeout_ms // 1000))}"])

    cmd.append("-")
    return cmd


def redact_codex_argv(argv: Sequence[str]) -> list[str]:
    """Redact sensitive `-c`/`--config` values and file flags before logging."""
    redacted: list[str] = []
    check_c_next = False
    skip_next = False

    for item in argv:
        text = str(item)
        if text in {"--mcp-config", "--system-prompt-file"}:
            redacted.append(text)
            skip_next = True
            check_c_next = False
            continue
        if text in {"-c", "--config"}:
            redacted.append(text)
            check_c_next = True
            skip_next = False
            continue
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
        if text.startswith(("--mcp-config=", "--system-prompt-file=")):
            prefix = text.split("=", 1)[0]
            redacted.append(f"{prefix}=<redacted>")
            continue
        if text.startswith(("-c=", "--config=")):
            lowered = text.lower()
            if any(term in lowered for term in _SENSITIVE_CONFIG_TERMS):
                prefix = text.split("=", 1)[0]
                redacted.append(f"{prefix}=<redacted>")
                continue
        if text.startswith("-c") and len(text) > 2 and "=" in text:
            lowered = text.lower()
            if any(term in lowered for term in _SENSITIVE_CONFIG_TERMS):
                redacted.append("-c<redacted>")
                continue
        redacted.append(text)

    return redacted


CODEX_OMITTED_MEDIA_NOTE = (
    "[Imagem/mídia omitida: o Codex CLI não recebe imagens na mensagem. "
    "Para ver um arquivo visual, chame a ferramenta de leitura de arquivos do Orin com o caminho dele.]"
)

_MEDIA_BLOCK_KEYS: tuple[str, ...] = (
    "image",
    "image_url",
    "input_image",
    "document",
    "audio",
    "video",
    "source",
)
_MEDIA_PATH_KEYS: tuple[str, ...] = ("path", "file_path", "workspace_path")


def _media_block_path(block: Mapping[str, Any]) -> str | None:
    """A file path carried by a media block, when it names a local file (never a URL or inline data)."""
    candidates: list[Any] = [block.get(key) for key in _MEDIA_PATH_KEYS]
    for nested_key in _MEDIA_BLOCK_KEYS:
        nested = block.get(nested_key)
        if isinstance(nested, str):
            candidates.append(nested)
        elif isinstance(nested, Mapping):
            candidates.extend(nested.get(key) for key in _MEDIA_PATH_KEYS)
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if (
            not text
            or len(text) > 1024
            or text.lower().startswith(("http://", "https://", "data:"))
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)
        ):
            continue
        if text.startswith("file://"):
            text = text[len("file://") :]
            if os.name == "nt" and text.startswith("/") and len(text) >= 3 and text[2] == ":":
                text = text[1:]
        return text
    return None


def _media_note(block: Mapping[str, Any] | None) -> str:
    path = _media_block_path(block) if block is not None else None
    if path is None:
        return CODEX_OMITTED_MEDIA_NOTE
    return (
        f"[Imagem/mídia no arquivo: {path} — o Codex CLI não recebe imagens na mensagem. "
        f'Para vê-la, chame a ferramenta de leitura de arquivos do Orin com o caminho "{path}".]'
    )


def encode_codex_user_turn(content: list[dict[str, Any]] | str) -> bytes:
    """Encode a user turn as UTF-8 text for `codex exec -`, which reads stdin until EOF.

    Line breaks are kept (`\\r\\n`/`\\r` become `\\n`) and the text ends with one `\\n`: flattening them into spaces
    glued the Markdown headings and the role markers of the replayed history into one line. A media block (history
    replayed after a restart or the text-protocol fallback) becomes a short text note, with its file path when it has
    one: it never raises, so it never drops the turn.
    """
    if isinstance(content, str):
        raw_text = content
    else:
        parts: list[str] = []
        omitted_pending = False
        for block in content:
            if isinstance(block, Mapping):
                btype = str(block.get("type") or "text").strip().lower()
                if btype not in ("text", "input_text") or any(k in block for k in _MEDIA_BLOCK_KEYS):
                    parts.append(_media_note(block))
                    continue
                text_val = block.get("text")
                if text_val is not None:
                    parts.append(str(text_val))
            elif isinstance(block, str):
                parts.append(block)
            else:
                omitted_pending = True
        if omitted_pending:
            parts.append(CODEX_OMITTED_MEDIA_NOTE)
        raw_text = "\n".join(parts)

    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    return (normalized.rstrip("\n") + "\n").encode("utf-8")


__all__ = [
    "CODEX_NEUTRAL_CONFIG",
    "CODEX_OMITTED_MEDIA_NOTE",
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
