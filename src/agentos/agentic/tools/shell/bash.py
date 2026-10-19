"""Bash tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Literal
from uuid import uuid4

from pydantic import Field

from agentos.agentic.workspace import WorkspaceError
from agentos.agentic.reminders import system_reminder
from agentos.agentic.tools._shared import (
    AgentToolError,
    CANCEL_CHECK_SECONDS,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_CHARS,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MAX_TRACKED_PROCESSES,
    _BLOCKED_COMMAND,
    _DEV_SERVER_CMD_RE,
    _PROCESS_LOG_DIR,
    _PROCESS_MANIFEST_PATH,
    _SERVER_READY_RE,
    _URL_EXTRACT_RE,
    _bounded_output,
    _process_is_running,
    _terminate_process_tree,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class BashTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _resolve_cwd_after_command(self, command: str, starting_cwd: Path, *, precheck: bool) -> Path:
        root_resolved = self.workspace.root.resolve()
        current = starting_cwd.resolve()
        for segment in re.split(r"\s*(?:&&|;)\s*", command.strip()):
            match = re.match(r"(?i)^cd(?:\s+/d)?\s+(.+)$", segment.strip())
            if not match:
                continue
            raw_target = match.group(1).strip()
            if len(raw_target) >= 2 and raw_target[0] == raw_target[-1] and raw_target[0] in {"'", '"'}:
                raw_target = raw_target[1:-1].strip()
            if not raw_target:
                continue
            candidate = (current / raw_target).resolve()
            try:
                candidate.relative_to(root_resolved)
            except ValueError as error:
                raise AgentToolError("Directory outside workspace is not allowed.") from error
            if precheck and re.match(r"(?i)^cd(?:\s+/d)?\s+(.+)$", command.strip()):
                if not candidate.is_dir():
                    raise AgentToolError(f"'{raw_target}' is not a directory in this workspace")
            current = candidate
        return current

    def run_command(
        self,
        command: str,
        background: bool = False,
        timeout_seconds: int | None = None,
        cwd: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        if not isinstance(command, str) or not command.strip():
            raise AgentToolError("command must be a non-blank string")
        if len(command) > 4096:
            raise AgentToolError("command is too long")
        if _BLOCKED_COMMAND.search(command):
            raise AgentToolError("This command is blocked because it would affect the host system.")
        clean_description = str(description or "").strip()[:240]
        timeout = DEFAULT_COMMAND_TIMEOUT_SECONDS if timeout_seconds is None else int(timeout_seconds)
        if not 1 <= timeout <= MAX_COMMAND_TIMEOUT_SECONDS:
            raise AgentToolError(f"timeout_seconds must be between 1 and {MAX_COMMAND_TIMEOUT_SECONDS}.")
        working_directory = self._current_cwd if self._current_cwd.is_dir() else self.workspace.root
        if isinstance(cwd, str) and cwd.strip():
            working_directory = self.workspace.resolve(cwd)
            if not working_directory.is_dir():
                raise AgentToolError(f"'{cwd}' is not a directory in this workspace")
            self._current_cwd = working_directory
        self._resolve_cwd_after_command(command, working_directory, precheck=True)
        change_baseline = self.workspace.begin_change_tracking()
        environment = {
            **os.environ,
            **self._extra_env,
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
            # A model cannot answer an interactive prompt, so an installer or
            # scaffolding tool that would ask one must instead fail fast (with
            # stdin closed below) or take its non-interactive default.
            "CI": "1",
            "npm_config_yes": "true",
            "npm_config_fund": "false",
            "npm_config_audit": "false",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "DEBIAN_FRONTEND": "noninteractive",
        }
        process_options: dict[str, Any] = {
            "shell": True,
            "cwd": str(working_directory),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL if background else subprocess.PIPE,
            "stderr": subprocess.DEVNULL if background else subprocess.PIPE,
            "env": environment,
        }
        if os.name == "nt":
            process_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            process_options["start_new_session"] = True
        log_relative = ""
        log_handle = None
        if background:
            # DEVNULL would otherwise swallow a dev server's output entirely;
            # a later read_process_output needs it on disk to read.
            log_relative = f"{_PROCESS_LOG_DIR}/{uuid4().hex[:12]}.log"
            log_handle = self.workspace.prepare_write_target(log_relative).open("wb")
            process_options["stdout"] = log_handle
            process_options["stderr"] = subprocess.STDOUT
        stashed_orin_dir: Path | None = None
        orin_dir = self.workspace.root / ".orin"
        if not background and orin_dir.is_dir():
            try:
                root_entries = [entry.name for entry in self.workspace.root.iterdir()]
                if all(name in {".orin", ".git"} for name in root_entries):
                    stash_parent = Path(tempfile.mkdtemp(prefix="orin-stash-"))
                    stashed_orin_dir = stash_parent / ".orin"
                    shutil.move(str(orin_dir), str(stashed_orin_dir))
            except Exception:
                stashed_orin_dir = None

        def _restore_stashed_orin() -> None:
            if stashed_orin_dir is None or not stashed_orin_dir.exists():
                return
            try:
                if not orin_dir.exists():
                    shutil.move(str(stashed_orin_dir), str(orin_dir))
                else:
                    for child in stashed_orin_dir.iterdir():
                        dest = orin_dir / child.name
                        if not dest.exists():
                            shutil.move(str(child), str(dest))
                shutil.rmtree(str(stashed_orin_dir.parent), ignore_errors=True)
            except Exception:
                pass

        try:
            try:
                process = subprocess.Popen(command, **process_options)  # noqa: S602 - a local agent shell is the feature
            finally:
                if log_handle is not None:
                    log_handle.close()
            if background:
                shell_id = f"bash_{process.pid}"
                manifest = self._load_process_manifest()
                manifest[str(process.pid)] = {
                    "command": command[:400],
                    "description": clean_description,
                    "log_path": log_relative,
                    "started_at": datetime.now(UTC).isoformat(),
                    "notified_exit": False,
                }
                self._save_process_manifest(manifest)
                self._background_shells[shell_id] = {
                    "shell_id": shell_id,
                    "pid": process.pid,
                    "command": command[:400],
                    "description": clean_description,
                    "process": process,
                    "log_path": log_relative,
                    "status": "running",
                }
                artifacts = self.workspace.end_change_tracking(change_baseline)
                self._queue_reindex(artifacts)
                return {
                    "ok": True,
                    "description": clean_description,
                    "background": True,
                    "auto_backgrounded": False,
                    "shell_id": shell_id,
                    "pid": process.pid,
                    "log_path": log_relative,
                    "summary": f"Iniciou em segundo plano: {(clean_description or command)[:80]}",
                    "content": (
                        f"Started background process {process.pid}. Its output is being written to {log_relative}; "
                        f"use read_process_output(pid={process.pid}) to read it, read_process_output() with no pid to "
                        "see every tracked process, or stop_process(pid=...) to end it."
                    ),
                    "payload": {
                        "ok": True,
                        "command": command[:400],
                        "description": clean_description,
                        "label": (clean_description or command)[:120],
                        "background": True,
                        "auto_backgrounded": False,
                        "shell_id": shell_id,
                        "pid": process.pid,
                        "log_path": log_relative,
                        "artifacts": artifacts,
                    },
                }
            if getattr(process, "stdout", None) is not None and getattr(process, "stderr", None) is not None and hasattr(process, "poll"):
                stdout_chunks: list[bytes] = []
                stderr_chunks: list[bytes] = []
                live_parts: list[str] = []
                stream_lock = threading.Lock()
                bg_log_file_ref: list[Any] = [None]

                def _reader(stream: Any, bucket: list[bytes]) -> None:
                    try:
                        for chunk in iter(stream.readline, b""):
                            if not chunk:
                                break
                            decoded = chunk.decode("utf-8", "replace")
                            with stream_lock:
                                bucket.append(chunk)
                                live_parts.append(decoded)
                                log_f = bg_log_file_ref[0]
                                if log_f is not None:
                                    try:
                                        log_f.write(chunk)
                                        log_f.flush()
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                    finally:
                        try:
                            stream.close()
                        except Exception:
                            pass

                t_out = threading.Thread(target=_reader, args=(process.stdout, stdout_chunks), daemon=True)
                t_err = threading.Thread(target=_reader, args=(process.stderr, stderr_chunks), daemon=True)
                t_out.start()
                t_err.start()
                started_at = time.monotonic()
                next_cancel_check = 0.0
                last_emit_at = 0.0
                last_emitted_count = 0
                is_dev_server_cmd = bool(_DEV_SERVER_CMD_RE.search(command))
                ready_match_at: float | None = None
                detected_url: str | None = None

                while process.poll() is None or t_out.is_alive() or t_err.is_alive():
                    now = time.monotonic()
                    elapsed = now - started_at
                    if now >= next_cancel_check:
                        next_cancel_check = now + CANCEL_CHECK_SECONDS
                        if self._cancel_requested():
                            _terminate_process_tree(process)
                            t_out.join(timeout=0.5)
                            t_err.join(timeout=0.5)
                            try:
                                process.wait(timeout=0.5)  # the readers own the pipes now; communicate() would read them again
                            except Exception:
                                pass
                            raise AgentToolError("Interrupted by user: the command was stopped.", error_code="INTERRUPTED_BY_USER")
                    if elapsed >= timeout:
                        _terminate_process_tree(process)
                        t_out.join(timeout=0.5)
                        t_err.join(timeout=0.5)
                        if hasattr(process, "communicate"):
                            try:
                                process.communicate(timeout=0.5)
                            except Exception:
                                pass
                        raise AgentToolError(f"Command timed out after {timeout}s.")
                    with stream_lock:
                        current_count = len(live_parts)
                        combined_live = "".join(live_parts) if current_count > 0 else ""
                        tail_snapshot = combined_live[-2000:].strip() if current_count > last_emitted_count else ""
                    if ready_match_at is None and combined_live and _SERVER_READY_RE.search(combined_live):
                        ready_match_at = now
                        url_m = _URL_EXTRACT_RE.search(combined_live)
                        detected_url = url_m.group(0) if url_m else None

                    should_auto_bg = process.poll() is None and (
                        (ready_match_at is not None and (now - ready_match_at) >= 0.35)
                        or (is_dev_server_cmd and elapsed >= 2.0)
                    )
                    if should_auto_bg:
                        _restore_stashed_orin()
                        auto_log_rel = f"{_PROCESS_LOG_DIR}/{uuid4().hex[:12]}.log"
                        try:
                            auto_log_target = self.workspace.prepare_write_target(auto_log_rel)
                            auto_log_f = auto_log_target.open("ab")
                        except Exception:
                            auto_log_f = None
                        with stream_lock:
                            stdout_now = b"".join(stdout_chunks).decode("utf-8", "replace").strip()
                            stderr_now = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
                            if auto_log_f is not None:
                                try:
                                    for c in stdout_chunks:
                                        auto_log_f.write(c)
                                    for c in stderr_chunks:
                                        auto_log_f.write(c)
                                    auto_log_f.flush()
                                except Exception:
                                    pass
                                bg_log_file_ref[0] = auto_log_f
                        if detected_url is None and combined_live:
                            url_m = _URL_EXTRACT_RE.search(combined_live)
                            detected_url = url_m.group(0) if url_m else None
                        shell_id = f"bash_{process.pid}"
                        manifest = self._load_process_manifest()
                        manifest[str(process.pid)] = {
                            "command": command[:400],
                            "description": clean_description,
                            "log_path": auto_log_rel,
                            "started_at": datetime.now(UTC).isoformat(),
                            "notified_exit": False,
                            "auto_backgrounded": True,
                            "detected_url": detected_url,
                        }
                        self._save_process_manifest(manifest)
                        self._background_shells[shell_id] = {
                            "shell_id": shell_id,
                            "pid": process.pid,
                            "command": command[:400],
                            "description": clean_description,
                            "process": process,
                            "log_path": auto_log_rel,
                            "status": "running",
                            "auto_backgrounded": True,
                            "detected_url": detected_url,
                        }
                        artifacts = self.workspace.end_change_tracking(change_baseline)
                        self._queue_reindex(artifacts)
                        msg = "Dev server detected and automatically moved to background."
                        content_parts = [f"{msg} (pid={process.pid}, shell_id={shell_id}, log={auto_log_rel})"]
                        if detected_url:
                            content_parts.append(f"Detected URL: {detected_url}")
                        if stdout_now:
                            content_parts.append(stdout_now)
                        if stderr_now:
                            content_parts.append(f"[stderr]\n{stderr_now}")
                        auto_tail = "\n".join(p for p in (stdout_now, stderr_now) if p)[-2000:]
                        payload = {
                            "ok": True,
                            "command": command[:400],
                            "description": clean_description,
                            "label": (clean_description or command)[:120],
                            "background": True,
                            "auto_backgrounded": True,
                            "backgrounded_by_readiness": bool(ready_match_at is not None),
                            "shell_id": shell_id,
                            "pid": process.pid,
                            "process_id": process.pid,
                            "status": "running",
                            "detected_url": detected_url,
                            "log_path": auto_log_rel,
                            "exit_code": None,
                            "failed": False,
                            "stdout": stdout_now[:400],
                            "stderr": stderr_now[:400],
                            "output_tail": auto_tail,
                            "message": msg,
                            "artifacts": artifacts,
                        }
                        return {
                            "ok": True,
                            "command": command,
                            "description": clean_description,
                            "background": True,
                            "auto_backgrounded": True,
                            "backgrounded_by_readiness": bool(ready_match_at is not None),
                            "shell_id": shell_id,
                            "pid": process.pid,
                            "process_id": process.pid,
                            "status": "running",
                            "detected_url": detected_url,
                            "log_path": auto_log_rel,
                            "exit_code": None,
                            "stdout": stdout_now,
                            "stderr": stderr_now,
                            "output_tail": auto_tail,
                            "message": msg,
                            "summary": f"Auto-backgrounded dev server: {(clean_description or command)[:60]}"[:240],
                            "content": "\n".join(content_parts),
                            "payload": payload,
                        }

                    if callable(self._on_tool_progress):
                        if current_count > last_emitted_count and tail_snapshot and (last_emitted_count == 0 or now - last_emit_at >= 0.25):
                            last_emitted_count = current_count
                            last_emit_at = now
                            progress_payload = {
                                "tool_name": "run_command",
                                "tool_kind": "terminal",
                                "invocation_id": getattr(self, "_active_invocation_id", None) or "",
                                "command": command[:240],
                                "description": clean_description,
                                "output_tail": tail_snapshot,
                                "elapsed_ms": int(elapsed * 1000),
                            }
                            try:
                                try:
                                    self._on_tool_progress("run_command", f"Executando: {(clean_description or command)[:60]}", progress_payload)
                                except TypeError:
                                    self._on_tool_progress("run_command", progress_payload)  # type: ignore[call-arg]
                            except Exception:
                                pass
                    time.sleep(0.02)

                t_out.join(timeout=0.5)
                t_err.join(timeout=0.5)
                if hasattr(process, "wait"):
                    try:
                        process.wait(timeout=0.5)
                    except Exception:
                        pass
                if callable(self._on_tool_progress):
                    with stream_lock:
                        current_count = len(live_parts)
                        tail_snapshot = "".join(live_parts)[-2000:].strip()
                    if current_count > last_emitted_count and tail_snapshot:
                        elapsed = time.monotonic() - started_at
                        progress_payload = {
                            "tool_name": "run_command",
                            "tool_kind": "terminal",
                            "invocation_id": getattr(self, "_active_invocation_id", None) or "",
                            "command": command[:240],
                            "description": clean_description,
                            "output_tail": tail_snapshot,
                            "elapsed_ms": int(elapsed * 1000),
                        }
                        try:
                            try:
                                self._on_tool_progress("run_command", f"Executando: {(clean_description or command)[:60]}", progress_payload)
                            except TypeError:
                                self._on_tool_progress("run_command", progress_payload)  # type: ignore[call-arg]
                        except Exception:
                            pass
                stdout_bytes = b"".join(stdout_chunks)
                stderr_bytes = b"".join(stderr_chunks)
            else:
                try:
                    stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired as error:
                    _terminate_process_tree(process)
                    process.communicate()
                    raise AgentToolError(f"Command timed out after {timeout}s.") from error
        finally:
            _restore_stashed_orin()
        stdout = stdout_bytes.decode("utf-8", "replace").strip()
        stderr = stderr_bytes.decode("utf-8", "replace").strip()
        body = "\n".join(part for part in (stdout, f"[stderr]\n{stderr}" if stderr else "") if part) or "[no output]"
        body, output_truncated = _bounded_output(body, MAX_COMMAND_OUTPUT_CHARS)
        fg_tail = "\n".join(p for p in (stdout, stderr) if p)[-2000:]
        succeeded = process.returncode == 0
        if succeeded:
            next_cwd = self._resolve_cwd_after_command(command, working_directory, precheck=False)
            if next_cwd.is_dir():
                self._current_cwd = next_cwd
        artifacts = self.workspace.end_change_tracking(change_baseline)
        for artifact in artifacts:
            artifact_path = str(artifact.get("path") or "") if isinstance(artifact, Mapping) else ""
            if artifact_path and (change_baseline is None or artifact_path not in change_baseline or artifact_path not in self._read_file_state):
                self._record_file_read(artifact_path, explicit_read=False)
        self._queue_reindex(artifacts)
        rel_cwd = self._current_cwd.resolve().relative_to(self.workspace.root.resolve()).as_posix() or "."
        cwd_prefix = f"[cwd: {rel_cwd}]\n" if rel_cwd != "." else ""
        if re.search(r"\boperation\s+cancel+ed\b", body, re.IGNORECASE) and not artifacts:
            raise AgentToolError(
                f"{cwd_prefix}exit={process.returncode}\n{body}\n\n"
                "[Command cancelled interactively ('Operation cancelled') because stdin is closed or the target "
                "directory is not empty. Do not repeat the same command; pass non-interactive overwrite flags or "
                "create the files directly with write_file.]",
                error_code="COMMAND_CANCELLED_INTERACTIVE",
            )
        return {
            "ok": succeeded,
            "command": command,
            "description": clean_description,
            "background": False,
            "auto_backgrounded": False,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_tail": fg_tail,
            "summary": f"$ {(clean_description or command)[:80]}",
            "content": f"{cwd_prefix}exit={process.returncode}\n{body}",
            "payload": {
                "ok": succeeded,
                "command": command[:400],
                "description": clean_description,
                "background": False,
                "auto_backgrounded": False,
                "exit_code": process.returncode,
                "stdout": stdout[:400],
                "stderr": stderr[:400],
                "output_tail": fg_tail,
                "label": (clean_description or command)[:120],
                "failed": not succeeded,
                "artifacts": artifacts,
                "output_truncated": output_truncated,
                "cwd": rel_cwd,
            },
        }

    def _check_exited_background_processes(self) -> str:
        manifest = self._load_process_manifest()
        if not manifest:
            return ""
        notices: list[str] = []
        dirty = False
        for pid_text, info in list(manifest.items()):
            if not isinstance(info, Mapping):
                continue
            if info.get("notified_exit"):
                continue
            try:
                row_pid = int(pid_text)
            except ValueError:
                continue
            if row_pid in self._notified_exited_pids:
                continue
            if not _process_is_running(row_pid):
                self._notified_exited_pids.add(row_pid)
                updated = dict(info)
                updated["notified_exit"] = True
                manifest[pid_text] = updated
                dirty = True
                cmd = str(info.get("command") or "").strip()
                log_path = str(info.get("log_path") or "").strip()
                notices.append(
                    f"Background process {row_pid} ({cmd}) has exited. "
                    f"Use read_process_output(pid={row_pid}) to inspect {log_path or 'its output'}."
                )
        if dirty:
            try:
                self._save_process_manifest(manifest)
            except Exception:
                pass
        if not notices:
            return ""
        return "\n\n" + system_reminder("\n".join(notices))

    def _load_process_manifest(self) -> dict[str, dict[str, Any]]:
        try:
            content, _ = self.workspace.read_text(_PROCESS_MANIFEST_PATH)
        except WorkspaceError:
            return {}
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return {}
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            normalized: dict[str, dict[str, Any]] = {}
            for item in data:
                if isinstance(item, Mapping) and item.get("pid") is not None:
                    normalized[str(item["pid"])] = dict(item)
            return normalized
        return {}

    def _save_process_manifest(self, manifest: Mapping[str, Mapping[str, Any]]) -> None:
        # Bounded so a long-running conversation cannot grow this file forever;
        # the oldest tracked processes (by insertion order) fall off first.
        trimmed = dict(list(manifest.items())[-MAX_TRACKED_PROCESSES:])
        self.workspace.write_text(_PROCESS_MANIFEST_PATH, json.dumps(trimmed))


class BashInput(ToolInput):
    command: str = Field(description="The command to run.")
    description: str | None = Field(None, description="What the command does, in 5-10 words.")
    timeout: int | None = Field(None, ge=1000, le=600_000, description="Timeout in milliseconds.")
    run_in_background: bool | None = Field(None, description="Start it in the background and return immediately.")
    cwd: str | None = Field(
        None, description="Orin: workspace-relative directory to run in. Defaults to the session's current directory.",
    )


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="run_command",
        description="Run one shell command inside the conversation workspace and return its output. Set background=true only for a "
        "long-lived process (e.g. a dev server); it returns immediately and you read its output later with read_process_output. "
        "Raise timeout_seconds for a slow install or build instead of splitting it into smaller commands.",
        legacy_schema=_schema({
            "command": {**_TEXT, "description": "A single command line, executed non-interactively (stdin is closed)."},
            "description": {**_TEXT, "description": "Short 5-10 word description of what this command does."},
            "background": {"type": "boolean", "description": "Start without waiting; use only for persistent servers."},
            "timeout_seconds": {
                "type": "integer", "minimum": 1, "maximum": MAX_COMMAND_TIMEOUT_SECONDS,
                "description": f"How long to wait for the command to finish. Defaults to {DEFAULT_COMMAND_TIMEOUT_SECONDS}s. Ignored when background=true.",
            },
            "cwd": {**_TEXT, "description": "Workspace-relative directory to run the command from. Defaults to the workspace root."},
        }, ("command",)),
        handler="run_command",
        kind="terminal",
        input_model=BashInput,
        public_description='Run a shell command in the workspace. Prefer the file tools over shell commands for reading, searching and editing files. Give a short description. Use run_in_background for long-lived processes such as dev servers.',
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=240,
    ),
)
