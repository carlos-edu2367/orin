"""Plan mode tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from typing import Any, Mapping, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import (
    ContractError,
    ToolOutcome,
    parse_contract,
    TOOLKITS,
    VERIFICATION_MODES,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class PlanModeTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def write_contract(self, **values: Any) -> ToolOutcome:
        """Record the task contract, or explain precisely what is missing.

        A rejection is a result the model reads and corrects, never an
        exception: a weak model that cannot fill the schema on the first try
        has to be able to try again with the field name in front of it. The
        parsed contract rides back on the payload; the runtime is what pins
        it into the request, so this tool stays free of runtime state.
        """
        try:
            contract = parse_contract(values)
        except ContractError as error:
            return ToolOutcome("failed", "Contrato incompleto", str(error), {"tool_kind": "planning"}, "INVALID_CONTRACT")
        rendered = contract.render()
        payload: dict[str, Any] = {"tool_kind": "planning", "contract": contract.as_payload()}
        if self._code_mode_requires_approval:
            payload.update({
                "wait_for_user": True,
                "code_approval": True,
                "questions": [{
                    "id": "code_plan", "question": "Aprovar este plano do Modo Code para iniciar a implementação?",
                    "mode": "single_choice",
                    "options": [
                        {"id": "approve", "label": "Aprovar plano"},
                        {"id": "adjust", "label": "Pedir ajustes"},
                        {"id": "cancel", "label": "Cancelar"},
                    ],
                }],
            })
        return ToolOutcome(
            "succeeded", f"Contrato definido: {contract.objective[:180]}",
            f"Contrato registrado. Ele permanece visível durante toda a tarefa.\n\n{rendered}",
            payload,
        )

    def _sync_workspace_restriction(self) -> None:
        from agentos.code_mode.models import CodePermissionMode

        # A shared instance (no host folders) keeps full_autonomy inside the workspace: fail closed (Lote 0).
        is_full = self._permission_mode is CodePermissionMode.FULL_AUTONOMY and bool(getattr(self, "_host_folders", False))
        if getattr(self, "_read_only_mode", False) and not is_full:
            return
        self.workspace.unrestricted = is_full
        if not is_full and hasattr(self, "_current_cwd"):
            roots = (self.workspace.root.resolve(), *self.workspace.additional_roots)
            if not any(self._current_cwd.resolve().is_relative_to(r) for r in roots):
                self._current_cwd = self.workspace.root

    def enter_plan_mode(self, reason: str = "") -> ToolOutcome:
        """Switch the current turn into read-only Plan Mode."""
        from agentos.code_mode.models import CodePermissionMode

        if self._permission_mode is not CodePermissionMode.PLAN_MODE:
            self._previous_permission_mode = self._permission_mode
        self._plan_mode_active = True
        self._permission_mode = CodePermissionMode.PLAN_MODE
        self._sync_workspace_restriction()
        clean_reason = str(reason or "").strip()
        summary = f"Modo Plano ativado{f': {clean_reason[:120]}' if clean_reason else ''}"
        return ToolOutcome(
            "succeeded",
            summary,
            (
                "Plan Mode is now active. Mutating tools are blocked except writing or editing "
                "plan files inside `.orin/plans/`. Inspect the codebase, write your plan to "
                "`.orin/plans/<name>.md`, and call `exit_plan_mode(plan_path=..., summary=...)` when ready."
            ),
            {
                "ok": True,
                "tool_kind": "planning",
                "plan_mode": True,
                "permission_mode": "plan_mode",
                "reason": clean_reason,
            },
        )

    def exit_plan_mode(
        self,
        plan_path: str = "",
        summary: str = "",
        allowed_prompts: list[dict[str, Any]] | None = None,
    ) -> ToolOutcome:
        """Exit Plan Mode and present the plan for review or implementation."""
        from agentos.code_mode.models import CodePermissionMode

        clean_path = str(plan_path or "").strip().replace("\\", "/")
        clean_summary = str(summary or "").strip()
        plan_content = ""
        # W7 (W4): the output's filePath is only ever a path the workspace resolved, never a raw join.
        resolved_path = None
        if clean_path:
            try:
                target = self.workspace.resolve(clean_path)
                resolved_path = target
                if target.exists() and target.is_file():
                    plan_content = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                plan_content = ""
        elif (self.workspace.root / ".orin" / "plans").is_dir():
            plans = sorted((self.workspace.root / ".orin" / "plans").glob("*.md"), key=lambda item: item.stat().st_mtime)
            if plans:
                latest = plans[-1]
                resolved_path = latest
                clean_path = latest.relative_to(self.workspace.root).as_posix()
                try:
                    plan_content = latest.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    plan_content = ""

        restored_mode = getattr(self, "_previous_permission_mode", CodePermissionMode.ACCEPT_EDITS)
        if restored_mode is CodePermissionMode.PLAN_MODE:
            restored_mode = CodePermissionMode.ACCEPT_EDITS
        self._plan_mode_active = False
        self._permission_mode = restored_mode
        if hasattr(self, "_sync_workspace_restriction"):
            self._sync_workspace_restriction()

        payload: dict[str, Any] = {
            "ok": True,
            "tool_kind": "planning",
            "plan_mode": False,
            "plan_ready": True,
            "plan_approval": True,
            "permission_mode": restored_mode.value,
            "plan_path": clean_path,
            "plan_summary": clean_summary,
            "plan_content": plan_content[:2000],
        }
        if isinstance(allowed_prompts, list):
            payload["allowed_prompts"] = [dict(item) for item in allowed_prompts[:12] if isinstance(item, Mapping)]
        if self._code_mode_requires_approval:
            payload.update({
                "wait_for_user": True,
                "code_approval": True,
                "questions": [{
                    "id": "code_plan",
                    "question": "Aprovar este plano do Modo Code para iniciar a implementação?",
                    "mode": "single_choice",
                    "options": [
                        {"id": "approve", "label": "Aprovar plano"},
                        {"id": "adjust", "label": "Pedir ajustes"},
                        {"id": "cancel", "label": "Cancelar"},
                    ],
                }],
            })
        body_parts = ["Exited Plan Mode."]
        if clean_path:
            body_parts.append(f"Plan file: {clean_path}")
        if clean_summary:
            body_parts.append(f"Summary: {clean_summary}")
        if plan_content:
            body_parts.append(f"\n{plan_content[:4000]}")
        return ToolOutcome(
            "succeeded",
            f"Plano pronto{f': {clean_summary[:140]}' if clean_summary else ''}",
            "\n".join(body_parts),
            payload,
            data={"tool_output": {
                "plan": (plan_content or clean_summary or None), "isAgent": False,
                **({"filePath": str(resolved_path)} if resolved_path is not None else {}),
            }},
        )


class ExitPlanModeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str | None
    isAgent: bool
    filePath: str | None = None


class EnterPlanModeInput(ToolInput):
    pass


class ExitPlanModeInput(ToolInput):
    plan: str | None = Field(None, description="The plan, in Markdown.")
    allowedPrompts: list[dict[str, Any]] | None = None


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="write_contract",
        description="Declare what this task is and how it will be judged done, before doing it. "
        "State the objective, the files you will produce, the constraints you must respect, "
        "the acceptance criteria that must all hold before you may claim completion, and the "
        "tool families you need. Call it again to revise the contract when the task turns out "
        "to be different from what you assumed.",
        legacy_schema=_schema({
            "objective": {**_TEXT, "description": "One sentence: what the person actually wants."},
            "deliverables": {
                "type": "array", "maxItems": 12,
                "description": "Files this task must produce or change.",
                "items": _schema({"path": _TEXT, "description": _TEXT}, ("path", "description")),
            },
            "constraints": {
                "type": "array", "maxItems": 12, "items": _TEXT,
                "description": "What you must not do, or must preserve.",
            },
            "acceptance": {
                "type": "array", "minItems": 1, "maxItems": 12,
                "description": "Checks that must all pass before you report success.",
                "items": _schema({
                    "id": {**_TEXT, "description": "Short identifier, e.g. total_correto."},
                    "check": {**_TEXT, "description": "The condition, stated so it can be confirmed."},
                    "how": {"type": "string", "enum": sorted(VERIFICATION_MODES), "description": "'tool' to run something, 'inspection' to look at something."},
                }, ("id", "check", "how")),
            },
            "toolkits": {
                "type": "array", "minItems": 1, "maxItems": 7,
                "items": {"type": "string", "enum": sorted(TOOLKITS)},
                "description": "Only the families you actually need; unlisted tools stay unavailable.",
            },
            "steps": {
                "type": "array", "maxItems": 12, "items": _TEXT,
                "description": "Your intended plan. Guidance, not a commitment.",
            },
        }, ("objective", "acceptance", "toolkits")),
        handler="write_contract",
        kind="planning",
        order=100,
    ),
    ToolSpec(
        key="enter_plan_mode",
        description="Enter read-only Plan Mode for the current turn. In Plan Mode, mutating tools are blocked except writing or editing plan files inside .orin/plans/. Use this to explore the codebase and draft a plan before implementation.",
        legacy_schema=_schema({
            "reason": {**_TEXT, "description": "Why planning is needed before modifying project files."},
        }),
        handler="enter_plan_mode",
        kind="planning",
        input_model=EnterPlanModeInput,
        public_description='Switch to plan mode: explore read-only and prepare a plan before changing code.',
        read_only=True,
        order=120,
    ),
    ToolSpec(
        key="exit_plan_mode",
        description="Exit Plan Mode after writing the plan in .orin/plans/ and present it for review or transition back to implementation.",
        legacy_schema=_schema({
            "plan_path": {**_TEXT, "description": "Workspace-relative path to the plan file inside .orin/plans/."},
            "summary": {**_TEXT, "description": "Concise summary of the proposed plan."},
            "allowed_prompts": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
                "description": "Optional prompt-based permissions needed to implement the plan.",
            },
            "allowedPrompts": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
        }),
        handler="exit_plan_mode",
        kind="planning",
        input_model=ExitPlanModeInput,
        output_model=ExitPlanModeOutput,
        public_description='Present the finished plan to the user for approval and leave plan mode.',
        read_only=True,
        order=130,
    ),
)
