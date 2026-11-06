from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Mapping, Sequence

from agentos.providers.claude_cli.errors import CliSecurityAbort, ProviderTransportError

ISOLATION_FLAGS: tuple[str, ...] = ("--restricted", "--strict-mcp-config")
IDLE_TIMEOUT_SECONDS: int = 180
MCP_TOOL_TIMEOUT_ENV = "MCP_TOOL_TIMEOUT"
TOOL_USE_ID_META_KEY = "claudecode/toolUseId"
VALIDATED_CLI_RANGE: tuple[str, str] = (">=2.1.309", "<2.2")
FORBIDDEN_FLAGS: frozenset[str] = frozenset(
    {"--bare", "--safe-mode", "--session-id", "--resume"}
)
VALID_EFFORT_LEVELS: frozenset[str] = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)
SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,128}$")
_SAFE_MCP_SERVER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_VERSION_CACHE: dict[str, tuple[int, int, int] | None] = {}

STRIPPED_ENV_EXACT: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_SIMPLE",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SSE_PORT",
        "MCP_TOOL_TIMEOUT",
        "MCP_TIMEOUT",
    }
)
STRIPPED_ENV_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "ANTHROPIC_DEFAULT_",
    "ANTHROPIC_API_",
    "ANTHROPIC_AUTH_",
    "ANTHROPIC_BEDROCK_",
    "ANTHROPIC_VERTEX_",
    "ANTHROPIC_FOUNDRY_",
    "CLAUDE_API_",
    "CLAUDE_CODE_",
    "OPENAI_",
)


def clean_model_id(model: str | None) -> str | None:
    """Clean a model identifier without translating 5.x to 4.x."""
    if model is None:
        return None
    cleaned = str(model).strip()
    if not cleaned:
        return None
    if "/" in cleaned:
        _, cleaned = cleaned.split("/", 1)
        cleaned = cleaned.strip()
    if not cleaned or cleaned.startswith("-") or not SAFE_MODEL_RE.match(cleaned):
        raise ValueError(f"Invalid model identifier for Claude CLI: {model!r}")
    return cleaned


def build_isolated_claude_env(
    base_env: Mapping[str, str] | None = None,
    *,
    max_output_tokens: int | None = None,
    mcp_tool_timeout_ms: int | None = None,
) -> dict[str, str]:
    """Return an environment stripped of API keys and shell variables that override OAuth."""
    source = os.environ if base_env is None else base_env
    isolated: dict[str, str] = {}
    for key, val in source.items():
        upper_key = str(key).upper()
        if upper_key in STRIPPED_ENV_EXACT:
            continue
        if any(upper_key.startswith(prefix) for prefix in STRIPPED_ENV_PREFIXES):
            continue
        isolated[str(key)] = str(val)

    isolated["PYTHONIOENCODING"] = "utf-8"
    isolated["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    isolated["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
    if max_output_tokens is not None:
        if (
            not isinstance(max_output_tokens, int)
            or isinstance(max_output_tokens, bool)
            or max_output_tokens <= 0
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        isolated["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(max_output_tokens))
    if mcp_tool_timeout_ms is not None:
        if (
            not isinstance(mcp_tool_timeout_ms, int)
            or isinstance(mcp_tool_timeout_ms, bool)
            or mcp_tool_timeout_ms <= 0
        ):
            raise ValueError("mcp_tool_timeout_ms must be a positive integer")
        isolated[MCP_TOOL_TIMEOUT_ENV] = str(int(mcp_tool_timeout_ms))
        isolated["MCP_TIMEOUT"] = "30000"
    return isolated


build_isolated_env = build_isolated_claude_env


def merge_with_system_env(
    overrides: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    """Merge caller env overrides on top of `os.environ` so OS/home/winsock variables are preserved."""
    if overrides is None:
        return None
    merged = dict(os.environ)
    merged.update(overrides)
    return merged


def resolve_claude_binary(env: Mapping[str, str] | None = None) -> str | None:
    """Locate the native `claude.exe` binary, prioritizing native PE executables over `.cmd` wrappers."""
    source_env = os.environ if env is None else env
    appdata = source_env.get("APPDATA")
    userprofile = source_env.get("USERPROFILE")
    localappdata = source_env.get("LOCALAPPDATA")
    home = source_env.get("HOME")

    exe_candidates: list[Path] = []
    if appdata:
        exe_candidates.append(
            Path(appdata)
            / "npm"
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
    if userprofile:
        exe_candidates.append(Path(userprofile) / ".local" / "bin" / "claude.exe")
    if localappdata:
        exe_candidates.append(
            Path(localappdata) / "Programs" / "claude" / "claude.exe"
        )
    if home:
        exe_candidates.append(Path(home) / ".local" / "bin" / "claude.exe")

    for candidate in exe_candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("claude", path=source_env.get("PATH"))
    if found:
        found_path = Path(found)
        sibling_npm_exe = (
            found_path.parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "bin"
            / "claude.exe"
        )
        if sibling_npm_exe.is_file():
            return str(sibling_npm_exe)
        if found_path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            direct_exe = found_path.with_suffix(".exe")
            if direct_exe.is_file():
                return str(direct_exe)
        return str(found_path)

    if appdata:
        for shim_name in ("claude.cmd", "claude.ps1"):
            cmd_candidate = Path(appdata) / "npm" / shim_name
            if cmd_candidate.is_file():
                return str(cmd_candidate)
    if home:
        posix_candidate = Path(home) / ".local" / "bin" / "claude"
        if posix_candidate.is_file():
            return str(posix_candidate)
    return None


def clear_version_cache() -> None:
    """Clear the cached CLI version map."""
    _VERSION_CACHE.clear()


def claude_cli_version(
    binary: str | None = None,
    *,
    timeout: float = 5.0,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int, int] | None:
    """Return the parsed `(major, minor, patch)` of `claude --version`, cached per binary."""
    resolved = binary or resolve_claude_binary(env)
    if not resolved:
        return None
    if resolved in _VERSION_CACHE:
        return _VERSION_CACHE[resolved]

    isolated_env = build_isolated_claude_env(merge_with_system_env(env))
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    import sys

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
        # Transient failures (e.g. TimeoutExpired on cold start) are not cached.
        return None

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}".strip()
    match = _VERSION_RE.search(output)
    if not match:
        return None
    parsed = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    _VERSION_CACHE[resolved] = parsed
    return parsed


def is_version_validated(version: tuple[int, int, int] | None) -> bool:
    """Return True when `version` falls within `VALIDATED_CLI_RANGE` (`>=2.1.309, <2.2`)."""
    if version is None:
        return False
    return (2, 1, 309) <= version < (2, 2, 0)


# Identity before any patching: a facade attribute still equal to it is not an override.
_CANONICAL_RESOLVE_BINARY = resolve_claude_binary


def _resolve_binary_compat(env: Mapping[str, str] | None = None) -> str | None:
    import sys

    facade = sys.modules.get("agentos.provider_catalog.claude_code")
    patched = getattr(facade, "_resolve_claude_binary", None) if facade is not None else None
    if callable(patched) and patched is not _CANONICAL_RESOLVE_BINARY:
        return patched(env)
    return resolve_claude_binary(env)


def check_claude_cli_status(
    *,
    binary: str | None = None,
    timeout: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Verify that `claude` CLI is installed and authenticated via a `claude.ai` OAuth subscription."""
    resolved_binary = binary or _resolve_binary_compat(env)
    if not resolved_binary:
        raise ProviderTransportError(
            "Claude Code CLI executable ('claude') was not found on this machine. "
            "Install @anthropic-ai/claude-code and run 'claude auth login'."
        )

    isolated_env = build_isolated_claude_env(merge_with_system_env(env))
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    try:
        completed = subprocess.run(
            [resolved_binary, "auth", "status"],
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
    except Exception as exc:
        raise ProviderTransportError(
            f"Failed to execute 'claude auth status': {exc}"
        ) from exc

    raw_output = (completed.stdout or "").strip()
    if not raw_output:
        stderr_hint = (completed.stderr or "").strip()
        raise ProviderTransportError(
            f"Claude Code CLI returned empty auth status. {stderr_hint}".strip()
        )

    try:
        payload = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ProviderTransportError(
            "Claude Code CLI returned invalid JSON for 'claude auth status'."
        ) from exc

    if not isinstance(payload, Mapping):
        raise ProviderTransportError(
            "Claude Code CLI returned an unexpected auth status payload."
        )

    logged_in = payload.get("loggedIn") is True
    auth_method = str(payload.get("authMethod") or "").strip().lower()
    if not logged_in:
        raise ProviderTransportError(
            "Claude Code CLI is not logged in. Run 'claude auth login' in your terminal."
        )
    if auth_method != "claude.ai":
        raise ProviderTransportError(
            f"Claude Code CLI is authenticated via '{auth_method}' instead of a 'claude.ai' OAuth subscription. "
            "Run 'claude auth login' to use your Pro/Max/Team subscription limits."
        )

    return dict(payload)


def write_owner_only_temp(
    content: str,
    *,
    prefix: str = "orin-claude-",
    suffix: str = ".tmp",
    directory: Path | None = None,
) -> Path:
    """Create a temporary file with owner-only (`0o600`) permissions."""
    fd, raw_path = tempfile.mkstemp(
        prefix=prefix,
        suffix=suffix,
        dir=str(directory) if directory is not None else None,
    )
    path = Path(raw_path)
    try:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return path


# Entries the CLI would read as project configuration from its cwd.
_RESERVED_CWD_ENTRIES = frozenset({".claude", "CLAUDE.md", "CLAUDE.local.md", ".mcp.json"})


def default_isolated_cwd() -> Path:
    """The per-user empty folder `claude -p` runs in. On a shared POSIX /tmp the name carries the uid."""
    tmp = Path(tempfile.gettempdir())
    getuid = getattr(os, "getuid", None)
    if os.name == "nt" or getuid is None:
        return tmp / "orin-claude-cli-cwd"
    return tmp / f"orin-claude-cli-cwd-{getuid()}"


_MEMORY_FILES = ("CLAUDE.md", "CLAUDE.local.md")


def _refuse_planted_parent_memory(root: Path) -> None:
    """Claude Code also reads CLAUDE.md / CLAUDE.local.md from every parent of its cwd. One in a shared temp
    area, or (POSIX) owned by another user, could have been planted: refuse rather than load it."""
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
    except OSError:
        temp_root = Path(tempfile.gettempdir())
    getuid = getattr(os, "getuid", None)
    # The CLI reads the parents of its real cwd: check them as well as the path as given.
    ancestors: list[Path] = []
    for view in (root.absolute, root.resolve):
        try:
            ancestors.extend(p for p in view().parents if p not in ancestors)
        except OSError:
            continue
    for ancestor in ancestors:
        try:
            resolved_ancestor = ancestor.resolve()
        except OSError:
            resolved_ancestor = ancestor
        in_temp = resolved_ancestor == temp_root or temp_root in resolved_ancestor.parents
        for name in _MEMORY_FILES:
            candidate = ancestor / name
            try:
                info = candidate.lstat()
            except OSError:
                continue
            foreign = os.name != "nt" and getuid is not None and info.st_uid != getuid()
            if in_temp or foreign:
                raise CliSecurityAbort(f"Refusing to run the Claude CLI under {candidate}: it would load that file")


def ensure_isolated_cwd(base: Path | None = None) -> Path:
    """Create or return an empty directory outside any project repository for `claude -p`.

    Fails closed (CliSecurityAbort) instead of running the CLI where someone else could have planted a
    configuration: a symlinked root, a root owned by another user (POSIX), or a reserved entry it cannot remove.
    Symlinks inside are unlinked, never followed.
    """
    root = base if base is not None else default_isolated_cwd()
    _refuse_planted_parent_memory(root)
    if root.is_symlink():
        raise CliSecurityAbort(f"Refusing a symlinked Claude CLI working directory: {root}")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise CliSecurityAbort(f"Cannot create the Claude CLI working directory {root}: {exc}") from exc
    info = root.lstat()
    getuid = getattr(os, "getuid", None)
    if os.name != "nt" and getuid is not None:
        if info.st_uid != getuid():
            raise CliSecurityAbort(f"The Claude CLI working directory {root} belongs to another user")
        if info.st_mode & 0o077:
            try:
                root.chmod(0o700)
            except OSError as exc:
                raise CliSecurityAbort(f"Cannot make {root} private: {exc}") from exc
    for name in _RESERVED_CWD_ENTRIES:
        child = root / name
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CliSecurityAbort(f"Cannot remove {child} from the Claude CLI working directory: {exc}") from exc
        if child.is_symlink() or child.exists():
            raise CliSecurityAbort(f"{child} is still in the Claude CLI working directory")
    return root


def _validate_path_arg(value: str | Path, label: str) -> str:
    text = str(value).strip()
    if (
        not text
        or text.startswith("-")
        or "\x00" in text
        or "\n" in text
        or "\r" in text
    ):
        raise ValueError(f"Invalid {label} path")
    return text


_SENSITIVE_CONFIG_TERMS = (
    "token",
    "key",
    "secret",
    "password",
    "bearer",
    "authorization",
    "model_instructions_file",
    "system_prompt",
)


def redact_argv(argv: Sequence[str]) -> list[str]:
    """Redact temporary file paths (`--mcp-config`, `--system-prompt-file`) and sensitive `-c`/`--config` overrides before logging."""
    redacted: list[str] = []
    skip_next = False
    check_c_next = False
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
        if text.startswith(("-c=", "--config=")) and any(
            term in text.lower() for term in _SENSITIVE_CONFIG_TERMS
        ):
            prefix = text.split("=", 1)[0]
            redacted.append(f"{prefix}=<redacted>")
            continue
        redacted.append(text)
    return redacted


def user_event_line(content: str | list[dict[str, object]]) -> str:
    """Format a user message for `--input-format stream-json` stdin."""
    return (
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": content,
                },
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def build_claude_command(
    *,
    binary: str,
    system_prompt_file: str | Path,
    model: str | None = None,
    effort: str | None = None,
    fallback_model: str | None = None,
    max_turns: int = 1,
    mcp_config_file: str | Path | None = None,
    mcp_server_name: str = "orin",
    autocompact: str | int | None = None,
    session_id: str | None = None,
    resume_session_id: str | None = None,
    resume: str | None = None,
    persist_session: bool = False,
) -> list[str]:
    """Construct the isolated `claude -p` argument list (never using `--bare`, `--safe-mode`, `--session-id`, or `--resume`)."""
    if not isinstance(binary, str):
        raise ValueError("Valid Claude CLI binary path is required")
    binary_str = _validate_path_arg(binary, "binary")
    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or max_turns < 1
    ):
        raise ValueError("max_turns must be >= 1")
    if (
        session_id is not None
        or resume_session_id is not None
        or resume is not None
        or persist_session
    ):
        raise ValueError(
            "CLI session flags (--session-id / --resume / persist_session) are forbidden; "
            "Orin never writes session state to ~/.claude"
        )

    prompt_str = _validate_path_arg(system_prompt_file, "system_prompt_file")
    cmd: list[str] = [
        binary_str,
        "-p",
        *ISOLATION_FLAGS,
        "--disable-slash-commands",
        "--no-chrome",
        "--tools",
        "",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--max-turns",
        str(max_turns),
        "--system-prompt-file",
        prompt_str,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
    ]

    if mcp_config_file is not None:
        srv_name = str(mcp_server_name or "").strip()
        if not srv_name or not _SAFE_MCP_SERVER_RE.match(srv_name):
            raise ValueError(f"Invalid mcp_server_name: {mcp_server_name!r}")
        cmd.extend(
            [
                "--mcp-config",
                _validate_path_arg(mcp_config_file, "mcp_config_file"),
                "--allowedTools",
                f"mcp__{srv_name}__*",
            ]
        )

    if autocompact is not None:
        if autocompact == "auto":
            cmd.extend(["--autocompact", "auto"])
        elif (
            isinstance(autocompact, int)
            and not isinstance(autocompact, bool)
            and 100_000 <= autocompact <= 1_000_000
        ):
            cmd.extend(["--autocompact", str(autocompact)])
        else:
            raise ValueError(f"Invalid autocompact value for Claude CLI: {autocompact!r}")

    cleaned_model = clean_model_id(model)
    if cleaned_model is not None:
        cmd.extend(["--model", cleaned_model])

    if effort is not None and str(effort).strip():
        cleaned_effort = str(effort).strip().lower()
        if cleaned_effort not in VALID_EFFORT_LEVELS:
            raise ValueError(f"Invalid effort level for Claude CLI: {effort!r}")
        cmd.extend(["--effort", cleaned_effort])

    cleaned_fallback = clean_model_id(fallback_model)
    if cleaned_fallback is not None:
        cmd.extend(["--fallback-model", cleaned_fallback])

    if any(flag in FORBIDDEN_FLAGS for flag in cmd):
        raise AssertionError("Forbidden flag leaked into command")
    return cmd
