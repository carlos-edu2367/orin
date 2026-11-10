"""What a turn keeps for later (the working state a compaction summary carries, full tool outputs on disk).

Methods of AgentToolset that live in their own module.
"""
from __future__ import annotations

import re
from typing import Mapping
from uuid import uuid4

from agentos.agentic.tools._shared import ToolOutcome
from agentos.agentic.tools.public_names import public_name as _public_tool_name

RECENT_ACTIONS_KEPT = 5


class WorkingStateTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _persist_truncated_output(self, tool_name: str, full_content: str) -> str | None:
        """Persist full untruncated tool output inside .orin/tool-outputs/ for paged inspection."""
        try:
            rel_dir = ".orin/tool-outputs"
            out_dir = self.workspace.resolve(rel_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", tool_name)[:32] or "tool"
            rel_path = f"{rel_dir}/{safe_name}-{uuid4().hex[:12]}.txt"
            target = self.workspace.resolve(rel_path)
            target.write_text(full_content, encoding="utf-8", errors="replace")
            return rel_path
        except Exception:
            return None

    def _note_recent_action(self, internal: str, outcome: ToolOutcome) -> None:
        summary = " ".join(str(outcome.summary or "").split())[:160]
        line = f"{_public_tool_name(internal)} ({outcome.status}){': ' + summary if summary else ''}"
        actions = self.__dict__.setdefault("_recent_actions", [])
        actions.append(line)
        del actions[:-RECENT_ACTIONS_KEPT]

    def _working_state_section(self) -> str:
        """What the runtime knows for certain about the work, so a summary cannot lose it.

        Written by the runtime, not the model: live background processes (pid, command, log, last line),
        the files this turn wrote and the last actions. Losing these after a compaction is what made a model
        start a running job again or re-read files it had already produced.
        """
        lines: list[str] = []
        try:
            manifest = self._load_process_manifest()
        except Exception:
            manifest = {}
        processes: list[str] = []
        for pid_text, info in list(manifest.items())[-10:]:
            try:
                pid = int(pid_text)
            except (TypeError, ValueError):
                continue
            if not isinstance(info, Mapping) or not self._background_status(pid)[0]:
                continue
            log_rel = str(info.get("log_path") or "")
            last_line = ""
            if log_rel:
                try:
                    tail, _ = self.workspace.read_text(log_rel)
                    last_line = next((item.strip() for item in reversed(tail[-4000:].splitlines()) if item.strip()), "")
                except Exception:
                    last_line = ""
            command = " ".join(str(info.get("command") or "").split())[:160]
            row = f"- pid {pid}: `{command}`" + (f" (log: {log_rel})" if log_rel else "")
            if last_line:
                row += f"; last line: {last_line[:200]}"
            processes.append(row)
        if processes:
            lines.append("Background processes still running (do not start them again; read their output instead):")
            lines.extend(processes)
        written = sorted(str(path) for path in getattr(self, "_changed_paths", ()) if str(path).strip())
        if written:
            lines.append("Files written in this turn: " + ", ".join(f"`{path}`" for path in written[:40]))
        actions = list(getattr(self, "_recent_actions", ()))
        if actions:
            lines.append("Last actions:")
            lines.extend(f"- {item}" for item in actions)
        return "### Working State (from the runtime)\n" + "\n".join(lines) if lines else ""
