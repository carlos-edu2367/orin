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


def _render_orin_agent_md(
    *,
    server_name: str,
    url: str,
    token: str,
    system_prompt: str,
    timeout_seconds: int | None = None,
) -> str:
    srv, clean_url, clean_token = _validate_mcp_server_params(
        server_name=server_name,
        url=url,
        token=token,
    )
    auth_val = f"Bearer {clean_token}"
    timeout_line = (
        f"    timeoutSeconds: {int(timeout_seconds)}\n"
        if isinstance(timeout_seconds, int)
        and not isinstance(timeout_seconds, bool)
        and timeout_seconds > 0
        else ""
    )
    return (
        "---\n"
        "name: orin\n"
        "description: Orin isolated CLI agent\n"
        "mainAgent: true\n"
        "excludeDefaultComponents: true\n"
        "inheritMcp: false\n"
        "mcpServers:\n"
        f"  - name: {srv}\n"
        f"    url: {json.dumps(clean_url, ensure_ascii=True)}\n"
        "    headers:\n"
        f"      Authorization: {json.dumps(auth_val, ensure_ascii=True)}\n"
        f"{timeout_line}"
        "---\n\n"
        "# Instructions\n\n"
        f"{system_prompt.strip()}\n"
    )


def _update_agent_md_instructions(
    agent_md_path: Path,
    system_prompt: str,
    *,
    timeout_seconds: int | None = None,
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
    if marker in existing:
        prefix = existing.split(marker, 1)[0]
        if (
            isinstance(timeout_seconds, int)
            and not isinstance(timeout_seconds, bool)
            and timeout_seconds > 0
            and "timeoutSeconds:" not in prefix
            and "\n---\n" in prefix
        ):
            head, tail = prefix.rsplit("\n---\n", 1)
            prefix = f"{head}\n    timeoutSeconds: {int(timeout_seconds)}\n---\n{tail}"
        updated = f"{prefix}{marker}\n\n{system_prompt.strip()}\n"
    else:
        updated = f"{existing.rstrip()}\n\n{marker}\n\n{system_prompt.strip()}\n"
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
) -> ToolServerWiring:
    """Create isolated `.agents/agents/orin.md`, `.agents/mcp_config.json`, and `.agents/orin_settings.json`."""
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
    wiring_files = (agent_md_path, mcp_config_path, settings_file)

    try:
        for fpath in wiring_files:
            if fpath.is_symlink():
                raise ValueError(f"Refusing to write to symlink: {fpath}")

        agent_md_content = _render_orin_agent_md(
            server_name=srv,
            url=clean_url,
            token=clean_token,
            system_prompt=sys_prompt,
            timeout_seconds=timeout_seconds,
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

        settings_payload = {
            "trustedWorkspaces": [resolved_workdir],
            "allowNonWorkspaceAccess": False,
            "enableTerminalSandbox": True,
            "disableSlashCommands": True,
            "permissions": {
                "allow": [f"mcp({srv}/*)"],
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
    model = (
        clean_antigravity_model_id(request.model) or DEFAULT_ANTIGRAVITY_MODEL
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
    "Para ver um arquivo visual, chame a ferramenta view_file com o caminho dele.]"
)

_MEDIA_KEYS: tuple[str, ...] = (
    "image",
    "image_url",
    "input_image",
    "document",
    "audio",
    "video",
)


def encode_antigravity_user_turn(
    content: list[dict[str, Any]] | str,
) -> bytes:
    """Encode a user turn for `agy --input-format stream-json`: text only.

    agy 1.5.x rejects any non-text block in a user message (`only "text"`); it sees an
    image only as an MCP tool result (view_file). A media block here is history replayed
    after a restart (the image a view_file call returned, kept as a user message) or the
    text-protocol fallback: it becomes a short text note, never bytes and never an error.
    Raising here made every restart of a turn that had viewed an image fail and opened
    the native-loop circuit breaker for every agy conversation.
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
    "DEFAULT_ANTIGRAVITY_MODEL",
    "MAX_ANTIGRAVITY_MAJOR",
    "MIN_ANTIGRAVITY_VERSION",
    "VALID_EFFORT_LEVELS",
    "antigravity_version",
    "build_antigravity_command",
    "build_isolated_antigravity_env",
    "clean_antigravity_model_id",
    "encode_antigravity_user_turn",
    "is_antigravity_version_supported",
    "redact_antigravity_argv",
    "resolve_antigravity_binary",
    "resolve_antigravity_home",
    "wire_antigravity_tool_server",
]
