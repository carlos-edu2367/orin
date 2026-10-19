"""Worktree tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any
from agentos.agentic.tools._shared import (
    AgentToolError,
    ToolOutcome,
)


class WorktreeTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def enter_worktree(
        self,
        name: str | None = None,
        base_branch: str | None = None,
    ) -> ToolOutcome:
        from agentos.code_mode.worktree import WorktreeError, WorktreeManager

        manager = WorktreeManager(self._original_workspace_root)
        try:
            info = manager.create_worktree(name=name, base_branch=base_branch)
        except WorktreeError as error:
            raise AgentToolError(str(error)) from error
        self.workspace.root = info.path
        self._current_cwd = info.path
        self._active_worktree_name = info.name
        summary = f"Worktree isolado ativado: {info.name} ({info.branch})"
        content = json.dumps(
            {
                "status": "entered",
                "worktree": info.to_dict(),
                "workspace_root": str(info.path),
            },
            ensure_ascii=False,
            indent=2,
        )
        return ToolOutcome(
            "succeeded",
            summary[:240],
            content,
            {"tool_kind": "terminal", "worktree": info.to_dict()},
        )

    def exit_worktree(
        self,
        action: str = "keep",
        force: bool = False,
    ) -> ToolOutcome:
        from agentos.code_mode.worktree import WorktreeError, WorktreeManager

        clean_action = str(action or "keep").strip().lower()
        if clean_action not in {"keep", "remove"}:
            raise AgentToolError("action must be 'keep' or 'remove'.")
        manager = WorktreeManager(self._original_workspace_root)
        wt_name = self._active_worktree_name
        if not wt_name:
            active = manager.active_worktree()
            if active is not None:
                wt_name = active.name
        removed_info: dict[str, Any] | None = None
        if clean_action == "remove" and wt_name:
            # Switch root out of worktree before removing directory on Windows
            previous_root = self.workspace.root
            self.workspace.root = self._original_workspace_root
            self._current_cwd = self._original_workspace_root
            try:
                removed_info = manager.remove_worktree(wt_name, force=bool(force))
            except WorktreeError as error:
                self.workspace.root = previous_root
                self._current_cwd = previous_root
                raise AgentToolError(str(error)) from error
        else:
            self.workspace.root = self._original_workspace_root
            self._current_cwd = self._original_workspace_root
        self._active_worktree_name = None
        summary = f"Saiu do worktree ({clean_action})"
        content = json.dumps(
            {
                "status": "exited",
                "action": clean_action,
                "worktree_name": wt_name,
                "removed": removed_info,
                "workspace_root": str(self.workspace.root),
            },
            ensure_ascii=False,
            indent=2,
        )
        return ToolOutcome(
            "succeeded",
            summary[:240],
            content,
            {"tool_kind": "terminal", "action": clean_action, "worktree_name": wt_name},
        )
