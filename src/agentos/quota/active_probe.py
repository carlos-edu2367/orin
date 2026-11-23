"""Active quota probes: ask each subscription CLI for its real usage without spending quota.

Each probe has the same shape as the passive readers (``read() -> list[QuotaSnapshot]``) plus
``available()`` (CLI installed and connected) and a safeguard: the answer must prove that the CLI
handled the request locally (no model turn, no cost). If a future CLI version forwards ``/usage`` to
the model, the probe turns itself off until the process restarts, so quota is never burned in a loop.

Sources (``QuotaSnapshot.source``):
- ``claude_usage_command``: ``claude -p "/usage"`` refreshes ``cachedUsageUtilization`` in ``~/.claude.json``,
  which ``ClaudeUsageCacheReader`` already understands; the command's own text is the fallback.
- ``codex_app_server``: JSON-RPC ``account/rateLimits/read`` over ``codex app-server`` (stdin/stdout).
- ``agy_usage_command``: ``agy -p "/usage" --output-format json`` (buckets per model group).

OAuth tokens are never read, passed on or logged here.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .claude_reader import ClaudeUsageCacheReader
from .models import QuotaSnapshot
from .normalize import (
    normalize_percent,
    normalize_resets_at,
    normalize_status,
    normalize_window_kind,
)

logger = logging.getLogger("agentos.quota.active_probe")

PROBE_TIMEOUT_SECONDS = 30.0
CLAUDE_PROBE_MODEL = "haiku"
AGY_PROBE_MODEL = "gemini-3.8-flash-low"

SOURCE_CLAUDE_USAGE = "claude_usage_command"
SOURCE_CODEX_APP_SERVER = "codex_app_server"
SOURCE_AGY_USAGE = "agy_usage_command"
ACTIVE_SOURCES: frozenset[str] = frozenset({SOURCE_CLAUDE_USAGE, SOURCE_CODEX_APP_SERVER, SOURCE_AGY_USAGE})

# `/usage` refetches only when its cache is older than a few minutes, so a cache stamped within this window
# before the probe started is exactly what the command just reported; older means the command did not refresh it.
_CACHE_MAX_AGE = timedelta(minutes=10)


class ProbeError(Exception):
    """A probe could not read the quota this time (transient: timeout, bad output, CLI error)."""


class ProbeUnsafeError(ProbeError):
    """The CLI did not handle the command locally; the probe must stay off until restart."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    error: str | None = None


# argv, env, cwd, timeout seconds
CommandRunner = Callable[[Sequence[str], Mapping[str, str], str, float], CommandResult]
# argv, env, cwd, timeout seconds -> the JSON-RPC ``result`` of ``account/rateLimits/read``
RateLimitsRpc = Callable[[Sequence[str], Mapping[str, str], str, float], Mapping[str, Any]]


class ActiveProbe(Protocol):
    provider: str
    source: str

    @property
    def disabled_reason(self) -> str | None: ...

    def available(self) -> bool: ...

    def read(self) -> list[QuotaSnapshot]: ...


# ---------------------------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------------------------


def run_command(argv: Sequence[str], env: Mapping[str, str], cwd: str, timeout: float) -> CommandResult:
    """Run a one-shot CLI command; the whole process tree dies on timeout and no window opens on Windows."""
    from agentos.hooks.processes import run_shell_command

    result = run_shell_command(
        list(argv),
        cwd=cwd,
        env=env,
        stdin_text="",
        timeout_seconds=timeout,
        shell=False,
    )
    return CommandResult(result.exit_code, result.stdout, result.stderr, result.timed_out, result.error)


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    pid = process.pid
    try:
        process.kill()
    except OSError:
        pass
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def codex_rate_limits_rpc(
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: str,
    timeout: float,
    *,
    client_version: str = "0",
) -> Mapping[str, Any]:
    """Speak JSON-RPC (one JSON message per line) to ``codex app-server`` and return ``account/rateLimits/read``'s result."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(  # noqa: S603 - argv built from a resolved binary, no shell
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            env=dict(env),
            creationflags=flags,
        )
    except (OSError, ValueError) as error:
        raise ProbeError(f"could not start codex app-server: {type(error).__name__}") from error

    lines: queue.Queue[bytes | None] = queue.Queue()

    def _pump() -> None:
        try:
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, b""):
                lines.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    threading.Thread(target=_pump, name="orin-codex-rpc-reader", daemon=True).start()
    deadline = time.monotonic() + timeout

    def _send(message: Mapping[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        process.stdin.flush()

    def _await(request_id: int) -> Mapping[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("codex app-server timed out")
            try:
                raw = lines.get(timeout=remaining)
            except queue.Empty as error:
                raise ProbeError("codex app-server timed out") from error
            if raw is None:
                raise ProbeError("codex app-server closed before answering")
            try:
                message = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(message, Mapping) or message.get("id") != request_id:
                continue
            if message.get("error") is not None:
                raise ProbeError("codex app-server returned an error")
            result = message.get("result")
            if not isinstance(result, Mapping):
                raise ProbeError("codex app-server returned no result")
            return result

    try:
        try:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "orin", "title": "Orin", "version": client_version}},
                }
            )
            _await(1)
            _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            _send({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read"})
            return _await(2)
        except (OSError, ValueError) as error:
            raise ProbeError(f"codex app-server pipe failed: {type(error).__name__}") from error
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        _terminate_tree(process)


def _probe_workdir(name: str) -> str:
    """A dedicated folder so probe runs never show up in the user's project/session lists."""
    try:
        from agentos.installation.paths import orin_paths

        path = orin_paths().data / "quota-probe" / name
    except Exception:  # noqa: BLE001
        path = Path(tempfile.gettempdir()) / "orin-quota-probe" / name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir())
    return str(path)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    candidates = [cleaned]
    candidates.extend(line.strip() for line in reversed(cleaned.splitlines()) if line.strip().startswith("{"))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ProbeError("output is not a JSON object")


def _is_zero(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) == 0.0


class _BaseProbe:
    provider = ""
    source = ""
    label = ""

    def __init__(self, *, clock: Callable[[], datetime] | None = None, timeout: float = PROBE_TIMEOUT_SECONDS) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout = float(timeout)
        self._disabled_reason: str | None = None

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    def _now(self) -> datetime:
        now = self._clock()
        return now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)

    def available(self) -> bool:  # pragma: no cover - overridden
        return False

    def _read(self) -> list[QuotaSnapshot]:  # pragma: no cover - overridden
        return []

    def _trip(self, reason: str) -> None:
        if self._disabled_reason is None:
            logger.warning(
                "sonda ativa de cota (%s) desligada até reiniciar: %s",
                self.label or self.provider,
                reason,
            )
        self._disabled_reason = reason

    def read(self) -> list[QuotaSnapshot]:
        if self._disabled_reason is not None:
            return []
        try:
            if not self.available():
                return []
            return self._read()
        except ProbeUnsafeError as error:
            self._trip(str(error))
            return []
        except ProbeError as error:
            logger.info("sonda ativa de cota (%s) sem leitura: %s", self.label or self.provider, error)
            return []
        except Exception:  # noqa: BLE001 - a probe never fails its caller
            logger.warning("sonda ativa de cota (%s) falhou", self.label or self.provider, exc_info=True)
            return []


# ---------------------------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------------------------

_MONTHS = {
    name: index
    for index, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
    )
}
_CLAUDE_LINE = re.compile(
    r"^\s*(?P<label>[^:\n]+?)\s*:\s*(?P<pct>\d+(?:\.\d+)?)\s*%\s*used(?:\s*[·\-|]\s*resets?\s+(?P<reset>[^\n(]+?))?\s*(?:\((?P<tz>[^)\n]+)\))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_RESET_TEXT = re.compile(
    r"^(?:(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2})(?:,|\s+at)?\s+)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)$",
    re.IGNORECASE,
)


def _parse_reset_text(text: str | None, tz_name: str | None, now: datetime) -> datetime | None:
    if not text:
        return None
    match = _RESET_TEXT.match(text.strip())
    if match is None:
        return None
    try:
        zone = ZoneInfo(tz_name.strip()) if tz_name else now.astimezone().tzinfo
    except Exception:  # noqa: BLE001 - unknown zone: fall back to the machine's zone
        zone = now.astimezone().tzinfo
    if zone is None:
        return None
    hour = int(match.group("hour")) % 12
    if match.group("ampm").lower() == "pm":
        hour += 12
    minute = int(match.group("minute") or 0)
    local_now = now.astimezone(zone)
    try:
        if match.group("mon") and match.group("day"):
            month = _MONTHS.get(match.group("mon").lower())
            if month is None:
                return None
            candidate = datetime(local_now.year, month, int(match.group("day")), hour, minute, tzinfo=zone)
            if candidate < local_now - timedelta(days=1):
                candidate = candidate.replace(year=candidate.year + 1)
        else:
            candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= local_now:
                candidate += timedelta(days=1)
    except ValueError:
        return None
    return candidate.astimezone(UTC)


def parse_claude_usage_text(text: str | None, *, now: datetime) -> list[QuotaSnapshot]:
    """Fallback parser for the human text ``/usage`` prints ("Current week (all models): 38% used · resets ...")."""
    snapshots: dict[tuple[str, str | None], QuotaSnapshot] = {}
    for match in _CLAUDE_LINE.finditer(text or ""):
        label = match.group("label").strip()
        lowered = label.lower()
        scope: str | None = None
        if "session" in lowered or "5" in lowered and "hour" in lowered:
            kind = "five_hour"
        elif "week" in lowered:
            inner = re.search(r"\(([^)]+)\)", label)
            inner_text = inner.group(1).strip() if inner else ""
            if not inner_text or inner_text.lower() in {"all models", "all", "todos os modelos"}:
                kind = "seven_day"
            else:
                kind = "weekly_scoped"
                scope = inner_text
        else:
            continue
        percent = normalize_percent(float(match.group("pct")), scale="percent")
        snapshots[(kind, scope)] = QuotaSnapshot(
            provider="anthropic-cli",
            window_kind=kind,
            used_percent=percent,
            status=normalize_status(None, used_percent=percent),
            resets_at=_parse_reset_text(match.group("reset"), match.group("tz"), now),
            source=SOURCE_CLAUDE_USAGE,
            observed_at=now,
            scope_model=scope,
            raw={"label": label, "origin": "usage_text"},
        )
    return list(snapshots.values())


class ClaudeUsageCommandProbe(_BaseProbe):
    """``claude -p "/usage"`` (local slash command; refreshes ``cachedUsageUtilization`` in ``~/.claude.json``)."""

    provider = "anthropic-cli"
    source = SOURCE_CLAUDE_USAGE
    label = "Claude"

    def __init__(
        self,
        *,
        reader: ClaudeUsageCacheReader | None = None,
        runner: CommandRunner | None = None,
        resolve_binary: Callable[[], str | None] | None = None,
        is_connected: Callable[[], bool] | None = None,
        env_factory: Callable[[], Mapping[str, str]] | None = None,
        workdir: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(clock=clock, timeout=timeout)
        self._reader = reader or ClaudeUsageCacheReader()
        self._runner = runner or run_command
        self._resolve_binary = resolve_binary or _default_claude_binary
        self._is_connected = is_connected or (lambda: self._reader.account_reader.path.is_file())
        self._env_factory = env_factory or _default_claude_env
        self._workdir = workdir or (lambda: _probe_workdir("claude"))

    def available(self) -> bool:
        return self._resolve_binary() is not None and bool(self._is_connected())

    def _read(self) -> list[QuotaSnapshot]:
        binary = self._resolve_binary()
        if not binary:
            raise ProbeError("claude binary not found")
        argv = [
            binary,
            "-p",
            "/usage",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--model",
            CLAUDE_PROBE_MODEL,
        ]
        started = self._now()
        result = self._runner(argv, self._env_factory(), self._workdir(), self._timeout)
        if result.timed_out:
            raise ProbeError("claude /usage timed out")
        if result.error:
            raise ProbeError(f"claude /usage could not run: {result.error}")
        payload = _parse_json_object(result.stdout)
        _assert_claude_local_command(payload)
        if payload.get("is_error") is True:
            raise ProbeError("claude /usage reported an error")

        fresh = [
            snap
            for snap in self._reader.read()
            if snap.observed_at >= started - _CACHE_MAX_AGE
        ]
        if fresh:
            return [replace(snap, source=SOURCE_CLAUDE_USAGE) for snap in fresh]
        text = payload.get("result")
        parsed = parse_claude_usage_text(text if isinstance(text, str) else "", now=self._now())
        if not parsed:
            raise ProbeError("claude /usage returned no recognizable windows")
        return parsed


def _assert_claude_local_command(payload: Mapping[str, Any]) -> None:
    turns = payload.get("num_turns")
    if not _is_zero(turns):
        raise ProbeUnsafeError(f"claude /usage did not run locally (num_turns={turns!r})")
    if payload.get("local_command") != "usage":
        raise ProbeUnsafeError("claude /usage did not report local_command=usage")
    if not _is_zero(payload.get("total_cost_usd")):
        raise ProbeUnsafeError(f"claude /usage reported a cost (total_cost_usd={payload.get('total_cost_usd')!r})")


def _default_claude_binary() -> str | None:
    from agentos.providers.claude_cli.command import resolve_claude_binary

    return resolve_claude_binary()


def _default_claude_env() -> Mapping[str, str]:
    from agentos.providers.claude_cli.command import build_isolated_claude_env

    return build_isolated_claude_env()


# ---------------------------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------------------------


def _codex_windows(
    limit: Mapping[str, Any],
    *,
    scope: str | None,
    rejected: bool,
    observed_at: datetime,
    limit_id: str | None,
) -> list[QuotaSnapshot]:
    out: list[QuotaSnapshot] = []
    for slot in ("primary", "secondary"):
        window = limit.get(slot)
        if not isinstance(window, Mapping):
            continue
        percent = normalize_percent(window.get("usedPercent"), scale="percent")
        minutes = window.get("windowDurationMins")
        kind = normalize_window_kind(None, window_minutes=minutes if isinstance(minutes, (int, float)) else None)
        if kind == "unknown":
            kind = "five_hour" if slot == "primary" else "seven_day"
        status = "rejected" if rejected else normalize_status(None, used_percent=percent)
        out.append(
            QuotaSnapshot(
                provider="openai-codex",
                window_kind=kind,
                used_percent=percent,
                status=status,
                resets_at=normalize_resets_at(window.get("resetsAt")),
                source=SOURCE_CODEX_APP_SERVER,
                observed_at=observed_at,
                scope_model=scope,
                raw={
                    "limit_id": limit_id,
                    "slot": slot,
                    "window_minutes": minutes,
                    "plan_type": limit.get("planType"),
                },
            )
        )
    return out


def parse_codex_rate_limits(result: Mapping[str, Any], *, observed_at: datetime) -> list[QuotaSnapshot]:
    """Map ``account/rateLimits/read`` onto snapshots (extra ``limitId`` entries become ``scope_model``)."""
    allowed = result.get("ordinaryUsageAllowed")
    blocked_globally = allowed is False
    by_id = result.get("rateLimitsByLimitId")
    entries: list[tuple[str | None, Mapping[str, Any]]] = []
    primary_limit = result.get("rateLimits")
    primary_id = primary_limit.get("limitId") if isinstance(primary_limit, Mapping) else None
    if isinstance(by_id, Mapping) and by_id:
        for key, value in by_id.items():
            if isinstance(value, Mapping):
                entries.append((str(key), value))
    elif isinstance(primary_limit, Mapping):
        entries.append((str(primary_id) if primary_id else "codex", primary_limit))

    snapshots: list[QuotaSnapshot] = []
    for limit_id, limit in entries:
        is_main = limit_id == "codex" or (primary_id is not None and limit_id == str(primary_id))
        scope: str | None = None
        if not is_main:
            name = limit.get("limitName")
            scope = str(name).strip() if isinstance(name, str) and name.strip() else limit_id
        rejected = bool(limit.get("rateLimitReachedType")) or (blocked_globally and is_main)
        snapshots.extend(
            _codex_windows(limit, scope=scope, rejected=rejected, observed_at=observed_at, limit_id=limit_id)
        )
    return snapshots


class CodexAppServerProbe(_BaseProbe):
    """``codex app-server`` JSON-RPC ``account/rateLimits/read``."""

    provider = "openai-codex"
    source = SOURCE_CODEX_APP_SERVER
    label = "Codex"

    def __init__(
        self,
        *,
        rpc: RateLimitsRpc | None = None,
        resolve_binary: Callable[[], str | None] | None = None,
        is_connected: Callable[[], bool] | None = None,
        env_factory: Callable[[], Mapping[str, str]] | None = None,
        workdir: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(clock=clock, timeout=timeout)
        self._rpc = rpc or codex_rate_limits_rpc
        self._resolve_binary = resolve_binary or _default_codex_binary
        self._is_connected = is_connected or _default_codex_connected
        self._env_factory = env_factory or _default_codex_env
        self._workdir = workdir or (lambda: _probe_workdir("codex"))

    def available(self) -> bool:
        return self._resolve_binary() is not None and bool(self._is_connected())

    def _read(self) -> list[QuotaSnapshot]:
        binary = self._resolve_binary()
        if not binary:
            raise ProbeError("codex binary not found")
        result = self._rpc([binary, "app-server"], self._env_factory(), self._workdir(), self._timeout)
        snapshots = parse_codex_rate_limits(result, observed_at=self._now())
        if not snapshots:
            raise ProbeError("codex returned no rate limit windows")
        return snapshots


def _default_codex_binary() -> str | None:
    from agentos.providers.codex_cli.command import resolve_codex_binary

    return resolve_codex_binary()


def _default_codex_connected() -> bool:
    from agentos.providers.codex_cli.catalog import resolve_codex_home

    try:
        return (resolve_codex_home() / "auth.json").is_file()
    except OSError:
        return False


def _default_codex_env() -> Mapping[str, str]:
    from agentos.providers.codex_cli.command import build_isolated_codex_env

    return build_isolated_codex_env()


# ---------------------------------------------------------------------------------------------
# Antigravity
# ---------------------------------------------------------------------------------------------


def clean_agy_group_name(name: str) -> str:
    """"Gemini Models" -> "Gemini"; "Claude and GPT models" -> "Claude e GPT"."""
    cleaned = re.sub(r"\s+models?\s*$", "", str(name or "").strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+and\s+", " e ", cleaned)
    return cleaned or str(name or "").strip()


def _agy_group_models(description: Any) -> list[str]:
    if not isinstance(description, str) or ":" not in description:
        return []
    tail = description.split(":", 1)[1]
    return [part.strip().rstrip(".") for part in tail.split(",") if part.strip()]


def parse_agy_usage(payload: Mapping[str, Any], *, observed_at: datetime) -> list[QuotaSnapshot]:
    command = payload.get("command")
    data = command.get("data") if isinstance(command, Mapping) else None
    groups = data.get("groups") if isinstance(data, Mapping) else None
    if not isinstance(groups, list):
        return []
    snapshots: list[QuotaSnapshot] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        group_name = str(group.get("name") or "").strip()
        if not group_name:
            continue
        models = _agy_group_models(group.get("description"))
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue
        for bucket in buckets:
            if not isinstance(bucket, Mapping):
                continue
            remaining = bucket.get("remaining_fraction")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
                continue
            percent = normalize_percent((1.0 - float(remaining)) * 100.0, scale="percent")
            window = str(bucket.get("window") or "").strip().lower()
            kind = "seven_day" if window in {"weekly", "week", "7d"} else normalize_window_kind(window)
            snapshots.append(
                QuotaSnapshot(
                    provider="google-antigravity",
                    window_kind=kind,
                    used_percent=percent,
                    status=normalize_status(None, used_percent=percent),
                    resets_at=normalize_resets_at(bucket.get("reset_time")),
                    source=SOURCE_AGY_USAGE,
                    observed_at=observed_at,
                    scope_model=clean_agy_group_name(group_name),
                    raw={
                        "group": group_name,
                        "models": models,
                        "bucket": bucket.get("id"),
                        "remaining_fraction": float(remaining),
                    },
                )
            )
    return snapshots


class AgyUsageCommandProbe(_BaseProbe):
    """``agy -p "/usage" --output-format json`` (local command, buckets per model group)."""

    provider = "google-antigravity"
    source = SOURCE_AGY_USAGE
    label = "Antigravity"

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        resolve_binary: Callable[[], str | None] | None = None,
        is_connected: Callable[[], bool] | None = None,
        env_factory: Callable[[], Mapping[str, str]] | None = None,
        workdir: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(clock=clock, timeout=timeout)
        self._runner = runner or run_command
        self._resolve_binary = resolve_binary or _default_agy_binary
        self._is_connected = is_connected or _default_agy_connected
        self._env_factory = env_factory or _default_agy_env
        self._workdir = workdir or (lambda: _probe_workdir("agy"))

    def available(self) -> bool:
        return self._resolve_binary() is not None and bool(self._is_connected())

    def _read(self) -> list[QuotaSnapshot]:
        binary = self._resolve_binary()
        if not binary:
            raise ProbeError("agy binary not found")
        argv = [binary, "-p", "/usage", "--output-format", "json", "--model", AGY_PROBE_MODEL]
        result = self._runner(argv, self._env_factory(), self._workdir(), self._timeout)
        if result.timed_out:
            raise ProbeError("agy /usage timed out")
        if result.error:
            raise ProbeError(f"agy /usage could not run: {result.error}")
        payload = _parse_json_object(result.stdout)
        _assert_agy_local_command(payload)
        snapshots = parse_agy_usage(payload, observed_at=self._now())
        if not snapshots:
            raise ProbeError("agy /usage returned no buckets")
        return snapshots


def _assert_agy_local_command(payload: Mapping[str, Any]) -> None:
    turns = payload.get("num_turns")
    if not _is_zero(turns):
        raise ProbeUnsafeError(f"agy /usage did not run locally (num_turns={turns!r})")
    command = payload.get("command")
    if not isinstance(command, Mapping) or str(command.get("name") or "") != "usage":
        raise ProbeUnsafeError("agy /usage did not report command=usage")
    cost = payload.get("total_cost_usd")
    if cost is not None and not _is_zero(cost):
        raise ProbeUnsafeError(f"agy /usage reported a cost (total_cost_usd={cost!r})")


def _default_agy_binary() -> str | None:
    from agentos.providers.antigravity_cli.command import resolve_antigravity_binary

    return resolve_antigravity_binary()


def _default_agy_connected() -> bool:
    from agentos.providers.antigravity_cli.command import resolve_antigravity_home

    try:
        return (resolve_antigravity_home() / "jetski_state.pbtxt").is_file()
    except OSError:
        return False


def _default_agy_env() -> Mapping[str, str]:
    from agentos.providers.antigravity_cli.command import build_isolated_antigravity_env

    env = build_isolated_antigravity_env()
    if os.name == "nt":
        for key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
            if key not in env and key in os.environ:
                env[key] = os.environ[key]
    return env


def default_active_probes() -> list[ActiveProbe]:
    """The production probes, one per subscription CLI."""
    return [ClaudeUsageCommandProbe(), CodexAppServerProbe(), AgyUsageCommandProbe()]


__all__ = [
    "ACTIVE_SOURCES",
    "ActiveProbe",
    "AgyUsageCommandProbe",
    "ClaudeUsageCommandProbe",
    "CodexAppServerProbe",
    "CommandResult",
    "ProbeError",
    "ProbeUnsafeError",
    "SOURCE_AGY_USAGE",
    "SOURCE_CLAUDE_USAGE",
    "SOURCE_CODEX_APP_SERVER",
    "clean_agy_group_name",
    "codex_rate_limits_rpc",
    "default_active_probes",
    "parse_agy_usage",
    "parse_claude_usage_text",
    "parse_codex_rate_limits",
    "run_command",
]
