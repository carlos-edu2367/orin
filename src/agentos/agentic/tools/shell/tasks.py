"""Tasks tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Literal

from pydantic import Field

from agentos.agentic.workspace import WorkspaceError
from agentos.agentic.tools._shared import (
    AgentToolError,
    MAX_PROCESS_LOG_TAIL_CHARS,
    _process_is_running,
    _terminate_pid,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class ShellTaskTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def read_process_output(self, pid: int | None = None, tail_chars: int = MAX_PROCESS_LOG_TAIL_CHARS) -> dict[str, Any]:
        """List every tracked background process, or read one's output.

        Folded into a single tool (rather than a separate ``list_processes``)
        so the phase's published tool set stays small enough for a weak model
        to navigate reliably.
        """
        manifest = self._load_process_manifest()
        preview_manifest: dict[str, Any] = {}
        try:
            from agentos.code_mode.preview_server import PreviewServerManager

            preview_manifest = PreviewServerManager(self.workspace.root, browser=self._browser)._load_manifest()
        except Exception:
            preview_manifest = {}

        if pid is None:
            rows: list[dict[str, Any]] = []
            seen_pids: set[int] = set()
            for pid_text, info in manifest.items():
                try:
                    row_pid = int(pid_text)
                except ValueError:
                    continue
                if not isinstance(info, Mapping):
                    continue
                seen_pids.add(row_pid)
                rows.append({
                    "pid": row_pid,
                    "command": str(info.get("command") or ""),
                    "running": _process_is_running(row_pid),
                    "log_path": str(info.get("log_path") or ""),
                    "started_at": str(info.get("started_at") or ""),
                })
            for srv_name, srv_info in preview_manifest.items():
                if not isinstance(srv_info, Mapping):
                    continue
                try:
                    srv_pid = int(srv_info.get("pid") or -1)
                except (TypeError, ValueError):
                    continue
                if srv_pid <= 0 or srv_pid in seen_pids:
                    continue
                seen_pids.add(srv_pid)
                rows.append({
                    "pid": srv_pid,
                    "command": str(srv_info.get("command") or f"preview:{srv_name}"),
                    "running": _process_is_running(srv_pid),
                    "log_path": str(srv_info.get("log_path") or f".orin/logs/preview-{srv_name}.log"),
                    "started_at": str(srv_info.get("started_at") or ""),
                })
            rows.sort(key=lambda item: item["pid"])
            if not rows:
                body = "[no background process was started in this workspace]"
            else:
                body = "\n".join(
                    f"{item['pid']} {'running' if item['running'] else 'exited'}  {item['command']}  (log: {item['log_path']})"
                    for item in rows
                )
            return {
                "summary": f"Listou {len(rows)} processo(s) em segundo plano",
                "content": body,
                "payload": {"count": len(rows), "processes": rows},
            }
        pid = int(pid)
        info = manifest.get(str(pid))
        if not isinstance(info, Mapping):
            for shell_entry in self._background_shells.values():
                if isinstance(shell_entry, Mapping) and shell_entry.get("pid") == pid:
                    info = shell_entry
                    break
        if not isinstance(info, Mapping):
            for srv_name, srv_info in preview_manifest.items():
                if isinstance(srv_info, Mapping):
                    try:
                        srv_pid = int(srv_info.get("pid") or -1)
                    except (TypeError, ValueError):
                        continue
                    if srv_pid == pid:
                        info = {
                            "command": str(srv_info.get("command") or f"preview:{srv_name}"),
                            "log_path": str(srv_info.get("log_path") or f".orin/logs/preview-{srv_name}.log"),
                            "started_at": str(srv_info.get("started_at") or ""),
                        }
                        break
        if not isinstance(info, Mapping):
            raise AgentToolError(f"No background process with pid {pid} was started in this workspace.")
        log_path = str(info.get("log_path") or "")
        if log_path:
            try:
                p = Path(log_path)
                if p.is_absolute():
                    log_path = p.resolve().relative_to(self.workspace.root.resolve()).as_posix()
            except Exception:
                pass
        # Checked before reading the file, not after: a process that finishes
        # between the two would otherwise read as "exited" with whatever
        # partial (possibly empty) output had been flushed at the moment of
        # the read, which looks like a completed, silent failure. Checking
        # first biases the race the other way — worst case, a process that
        # finishes a moment later is reported "still running" with output
        # that is already complete, which a follow-up call corrects.
        running = _process_is_running(pid)
        try:
            content, _ = self.workspace.read_text(log_path)
        except WorkspaceError:
            content = ""
        window = max(1, min(int(tail_chars), MAX_PROCESS_LOG_TAIL_CHARS))
        tail = content[-window:]
        body = tail if tail.strip() else "[no output yet]"
        return {
            "summary": f"Leu saída do processo {pid} ({'ainda rodando' if running else 'encerrado'})",
            "content": f"pid={pid} status={'running' if running else 'exited'}\n{body}",
            "payload": {"pid": pid, "running": running, "log_path": log_path},
        }

    def stop_process(self, pid: int) -> dict[str, Any]:
        pid = int(pid)
        manifest = self._load_process_manifest()
        info = manifest.get(str(pid))
        found_in_shells = False
        for shell_entry in self._background_shells.values():
            if isinstance(shell_entry, dict) and shell_entry.get("pid") == pid:
                found_in_shells = True
                shell_entry["status"] = "stopped"
        preview_stopped_name: str | None = None
        try:
            from agentos.code_mode.preview_server import PreviewServerManager

            preview_mgr = PreviewServerManager(self.workspace.root, browser=self._browser)
            preview_manifest = preview_mgr._load_manifest()
            for srv_name, srv_info in list(preview_manifest.items()):
                if isinstance(srv_info, Mapping):
                    try:
                        srv_pid = int(srv_info.get("pid") or -1)
                    except (TypeError, ValueError):
                        continue
                    if srv_pid == pid:
                        preview_mgr.stop_server(name=str(srv_name))
                        preview_stopped_name = str(srv_name)
                        break
        except Exception:
            pass
        if not isinstance(info, Mapping) and not found_in_shells and preview_stopped_name is None:
            raise AgentToolError(f"No background process with pid {pid} was started in this workspace.")
        if _process_is_running(pid):
            _terminate_pid(pid)
        self._notified_exited_pids.add(pid)
        if isinstance(info, Mapping):
            updated = dict(info)
            updated["notified_exit"] = True
            manifest[str(pid)] = updated
            try:
                self._save_process_manifest(manifest)
            except Exception:
                pass
        payload: dict[str, Any] = {"pid": pid, "ok": True, "status": "stopped"}
        if preview_stopped_name:
            payload["preview_server"] = preview_stopped_name
        return {
            "ok": True,
            "pid": pid,
            "status": "stopped",
            **({"preview_server": preview_stopped_name} if preview_stopped_name else {}),
            "summary": f"Encerrou o processo {pid}",
            "content": f"pid={pid} stopped",
            "payload": payload,
        }


class TaskOutputInput(ToolInput):
    task_id: str = Field(description="Id returned when the command started.")


class TaskStopInput(ToolInput):
    task_id: str | None = Field(None, description="Id of the background command.")
    shell_id: str | None = Field(None, description="Older name of task_id.")


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="read_process_output",
        description="List or read background processes started with run_command(background=true) in this workspace. Omit pid to list "
        "every tracked process (pid, command, running or exited). Pass pid to read that process's captured output, up to "
        "tail_chars, and whether it is still running.",
        legacy_schema=_schema({
            "pid": {"type": "integer", "description": "Process id from run_command(background=true). Omit to list every tracked process instead."},
            "tail_chars": {
                "type": "integer", "minimum": 1, "maximum": MAX_PROCESS_LOG_TAIL_CHARS,
                "description": "How many trailing characters of output to return when pid is set. Defaults to the maximum.",
            },
        }),
        handler="read_process_output",
        kind="terminal",
        input_model=TaskOutputInput,
        public_description='Read the output of a background command started with run_in_background.',
        read_only=True,
        available=lambda self: (self._enable_terminal),
        order=250,
    ),
    ToolSpec(
        key="stop_process",
        description="Stop a background process started with run_command(background=true), e.g. a dev server you no longer need running.",
        legacy_schema=_schema({"pid": {"type": "integer", "description": "Process id to stop, from run_command or read_process_output."}}, ("pid",)),
        handler="stop_process",
        kind="terminal",
        input_model=TaskStopInput,
        public_description='Stop a background command.',
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=260,
    ),
)
