"""Verify tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import os
import subprocess
from typing import Any, Mapping

from agentos.agentic.diagnostics import STEP_ORDER, detect_recipe
from agentos.agentic.tools._shared import (
    AgentToolError,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MAX_VERIFY_STEP_CHARS,
    SCAFFOLD_RECIPES,
    VERIFY_STEP_TIMEOUT_SECONDS,
    _bounded_output,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec


class VerifyTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def verify_project(self, steps: list[str] | None = None, scaffold: str | None = None) -> dict[str, Any]:
        if scaffold is not None:
            recipe_name = str(scaffold)
            commands = SCAFFOLD_RECIPES.get(recipe_name)
            if commands is None:
                raise AgentToolError(f"unknown scaffold recipe '{recipe_name}'. valid recipes: {', '.join(sorted(SCAFFOLD_RECIPES))}.")
            if steps:
                raise AgentToolError("scaffold cannot be combined with verification steps; scaffold first, then call verify_project again.")
            results: list[dict[str, Any]] = []
            artifacts: list[dict[str, Any]] = []
            for command in commands:
                result = self.run_command(command, timeout_seconds=MAX_COMMAND_TIMEOUT_SECONDS)
                payload = result.get("payload") if isinstance(result.get("payload"), Mapping) else {}
                command_artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
                artifacts.extend(item for item in command_artifacts if isinstance(item, Mapping))
                passed = not bool(payload.get("failed"))
                results.append({"step": "scaffold", "command": command, "ran": True, "passed": passed, "output": str(result.get("content") or "")})
                if not passed:
                    break
            all_passed = bool(results) and all(item["passed"] for item in results)
            lines = [f"Scaffold: {recipe_name}"]
            for item in results:
                lines.extend((f"\n== {item['command']} ==", f"resultado: {'ok' if item['passed'] else 'falhou'}", item["output"]))
            return {
                "summary": f"Scaffold {recipe_name}: {'ok' if all_passed else 'falhou'}",
                "content": "\n".join(lines),
                "payload": {"scaffold": recipe_name, "steps": results, "all_passed": all_passed, "artifacts": artifacts},
            }
        recipe = detect_recipe(self.workspace.root)
        if recipe is None:
            return {
                "summary": "Nenhum manifesto de projeto reconhecido",
                "content": (
                    "[no package.json, pyproject.toml, requirements.txt, go.mod or Cargo.toml was found at the "
                    "workspace root; there is nothing this tool knows how to verify]"
                ),
                "payload": {"recipe": None, "steps": [], "all_passed": None},
            }
        if steps is None:
            requested = list(recipe.available_steps())
        else:
            invalid = sorted(set(steps) - set(STEP_ORDER))
            if invalid:
                raise AgentToolError(f"unknown step(s): {', '.join(invalid)}. valid steps: {', '.join(STEP_ORDER)}.")
            requested = [step for step in STEP_ORDER if step in steps]
        step_results: list[dict[str, Any]] = []
        stopped_early = False
        for step in requested:
            command = recipe.command_for(step)
            if command is None:
                step_results.append({"step": step, "command": None, "ran": False, "passed": None})
                continue
            try:
                completed = subprocess.run(
                    command, shell=True, cwd=str(self.workspace.root), capture_output=True, text=True,
                    timeout=VERIFY_STEP_TIMEOUT_SECONDS, encoding="utf-8", errors="replace",
                    env={**os.environ, "CI": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                )
                passed = completed.returncode == 0
                output, _ = _bounded_output(((completed.stdout or "") + (completed.stderr or "")).strip() or "[no output]", MAX_VERIFY_STEP_CHARS)
            except subprocess.TimeoutExpired:
                passed = False
                output = f"[timed out after {VERIFY_STEP_TIMEOUT_SECONDS}s]"
            step_results.append({"step": step, "command": command, "ran": True, "passed": passed, "output": output})
            if step == "install" and not passed:
                stopped_early = True
                break
        ran = [item for item in step_results if item["ran"]]
        passed_steps = [item["step"] for item in ran if item["passed"]]
        failed_steps = [item["step"] for item in ran if not item["passed"]]
        all_passed = bool(ran) and not failed_steps
        lines = [f"Projeto detectado: {recipe.kind} ({recipe.manifest})"]
        for item in step_results:
            if not item["ran"]:
                lines.append(f"\n== {item['step']}: sem comando detectado ==")
                continue
            lines.append(f"\n== {item['step']}: {item['command']} ==")
            lines.append(f"resultado: {'ok' if item['passed'] else 'falhou'}")
            lines.append(item["output"])
        if stopped_early:
            lines.append("\n[a instalação falhou; as etapas seguintes não foram executadas]")
        lines.append(f"\nResumo — passou: {', '.join(passed_steps) or 'nenhuma'}; falhou: {', '.join(failed_steps) or 'nenhuma'}.")
        return {
            "summary": (
                f"Verificou o projeto ({recipe.kind}): {len(passed_steps)} ok, {len(failed_steps)} falhou"
                if ran else f"Projeto {recipe.kind} não tem etapas executáveis detectadas"
            ),
            "content": "\n".join(lines),
            "payload": {"recipe": recipe.kind, "steps": step_results, "all_passed": all_passed},
        }

    def report_verification(self, passed: bool, findings: str = "") -> dict[str, Any]:
        passed = bool(passed)
        findings = str(findings or "").strip()[:2000]
        return {
            "summary": "Verificação: critérios atendidos" if passed else "Verificação: critérios não atendidos",
            "content": findings or (
                "Todos os critérios de aceite foram conferidos com ferramentas e se sustentam."
                if passed else "Um ou mais critérios de aceite não se sustentam."
            ),
            "payload": {"passed": passed, "findings": findings},
        }


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="report_verification",
        description="Conclude verification: state whether every acceptance criterion actually holds, based on what you just "
        "checked with tools — not a guess. If something does not hold, describe concretely what is broken (the "
        "actual error, not a hypothesis); you will be sent back to fix it, up to a few rounds. Calling this tool, "
        "not a plain text reply, is how you leave the verification stage.",
        legacy_schema=_schema({
            "passed": {"type": "boolean", "description": "True only if every acceptance criterion was checked with a tool and holds."},
            "findings": {**_TEXT, "description": "What you checked and, when passed=false, exactly what is broken and where."},
        }, ("passed",)),
        handler="report_verification",
        kind="planning",
        order=110,
    ),
    ToolSpec(
        key="verify_project",
        description="Detect the project's stack from its manifest (package.json, pyproject.toml, requirements.txt, go.mod, "
        "Cargo.toml) and run its install, typecheck, lint, build and test steps in that order, reporting exactly "
        "what passed and what failed with each step's real output. Use this instead of guessing which command to "
        "run for a stack you did not write yourself. Omit steps to run everything detected; pass steps to skip a "
        "slow one you already know is satisfied (e.g. skip 'install' once dependencies are installed).",
        legacy_schema=_schema({
            "steps": {
                "type": "array", "items": {"type": "string", "enum": list(STEP_ORDER)},
                "description": "Subset of steps to run (always executed in the canonical order regardless of the order given here). Omit to run every step the project has.",
            },
            "scaffold": {
                "type": "string", "enum": sorted(SCAFFOLD_RECIPES),
                "description": "Create a new project from the official stack generator. Use only in a new workspace; this runs the curated generator and dependency install sequence.",
            },
        }),
        handler="verify_project",
        kind="terminal",
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=270,
    ),
)
