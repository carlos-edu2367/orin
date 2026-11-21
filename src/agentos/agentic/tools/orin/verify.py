"""Verify tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping

from agentos.agentic.diagnostics import STEP_ORDER, detect_recipe
from agentos.agentic.verification_findings import (
    VALID_CATEGORIES,
    VALID_EVIDENCE_KINDS,
    VALID_SEVERITIES,
    VALID_VERDICTS,
    validate_report,
)
from agentos.agentic.tools._shared import (
    AgentToolError,
    CANCEL_CHECK_SECONDS,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MAX_VERIFY_STEP_CHARS,
    SCAFFOLD_RECIPES,
    VERIFY_STEP_TIMEOUT_SECONDS,
    _bounded_output,
    _terminate_process_tree,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec


def _run_verification_step(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    cancelled: Callable[[], bool],
) -> tuple[bool, str]:
    if cancelled():
        raise AgentToolError(
            "Interrupted by user: the verification was stopped.",
            error_code="INTERRUPTED_BY_USER",
        )
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "CI": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"},
        **popen_kwargs,
    )
    started_at = time.monotonic()
    deadline = started_at + timeout
    try:
        while True:
            if cancelled():
                _terminate_process_tree(process)
                try:
                    process.communicate(timeout=1)
                except Exception:
                    pass
                raise AgentToolError(
                    "Interrupted by user: the verification was stopped.",
                    error_code="INTERRUPTED_BY_USER",
                )
            now = time.monotonic()
            if now >= deadline:
                _terminate_process_tree(process)
                try:
                    process.communicate(timeout=1)
                except Exception:
                    pass
                return False, f"[timed out after {timeout}s]"
            wait_time = min(CANCEL_CHECK_SECONDS, max(0.01, deadline - now))
            try:
                stdout, _ = process.communicate(timeout=wait_time)
                output = (stdout or b"").decode("utf-8", errors="replace")
                passed = (process.returncode == 0)
                return passed, output
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            _terminate_process_tree(process)
            try:
                process.communicate(timeout=1)
            except Exception:
                pass


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
                # Each scaffold step needs the previous one finished: never moved to the background.
                result = self.run_command(command, timeout_seconds=MAX_COMMAND_TIMEOUT_SECONDS, auto_background=False)
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
            passed, raw_output = _run_verification_step(
                command,
                cwd=self.workspace.root,
                timeout=VERIFY_STEP_TIMEOUT_SECONDS,
                cancelled=self._cancel_requested,
            )
            output, _ = _bounded_output(raw_output.strip() or "[no output]", MAX_VERIFY_STEP_CHARS)
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

    def report_verification(
        self,
        passed: bool | None = None,
        findings: str | list[dict[str, Any]] = "",
        verdict: str | None = None,
        checked: list[dict[str, Any]] | None = None,
        unverified: list[str] | None = None,
        summary: str | None = None,
    ) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "findings": findings,
            "checked": checked,
            "unverified": unverified,
            "summary": summary,
        }
        if verdict is not None:
            raw["verdict"] = verdict
        if passed is not None:
            raw["passed"] = passed
        try:
            report = validate_report(raw)
        except ValueError as error:
            raise AgentToolError(str(error), error_code="INVALID_VERIFICATION_REPORT") from error

        if report.verdict == "pass":
            status_summary = "Verificação: critérios atendidos"
            default_content = "Todos os critérios de aceite foram conferidos com ferramentas e se sustentam."
        elif report.verdict == "inconclusive":
            status_summary = "Verificação: resultado inconclusivo"
            default_content = "Não foi possível concluir a verificação de todos os critérios."
        else:
            status_summary = "Verificação: critérios não atendidos"
            default_content = "Um ou mais critérios de aceite não se sustentam."

        return {
            "summary": status_summary,
            "content": report.summary_text or default_content,
            "payload": {
                "passed": report.passed,
                "verdict": report.verdict,
                "findings": report.summary_text,
                "structured_findings": [item.to_dict() for item in report.findings],
                "blocking_findings": [item.to_dict() for item in report.blocking_findings],
                "checked": [item.to_dict() for item in report.checked],
                "unverified": list(report.unverified),
                "report": report.to_dict(),
            },
        }


_CHECKED_ITEM_SCHEMA = _schema(
    {
        "command": {**_TEXT, "description": "Command executed during verification."},
        "exit_code": {"type": "integer", "description": "Process exit code."},
        "summary": {**_TEXT, "description": "Short summary of the command output."},
    },
    ("command", "exit_code"),
)

_EVIDENCE_SCHEMA = _schema(
    {
        "kind": {
            "type": "string",
            "enum": sorted(VALID_EVIDENCE_KINDS),
            "description": "failing_test/command require command+exit_code; code_ref requires file+line+reason.",
        },
        "command": {**_TEXT, "description": "Command run (required for failing_test/command)."},
        "exit_code": {"type": "integer", "description": "Exit code (required for failing_test/command)."},
        "output_excerpt": {**_TEXT, "description": "Short excerpt of the failing output."},
        "file": {**_TEXT, "description": "Relative file path (required for code_ref)."},
        "line": {"type": "integer", "minimum": 1, "description": "1-based line number (required for code_ref)."},
        "reason": {**_TEXT, "description": "Why this line is wrong (required for code_ref)."},
    },
    ("kind",),
)

_FINDING_ITEM_SCHEMA = _schema(
    {
        "id": {**_TEXT, "description": "Short finding id such as F1."},
        "severity": {
            "type": "string",
            "enum": sorted(VALID_SEVERITIES),
            "description": "blocker, major, minor, nit, or unproven (findings without valid evidence downgrade to unproven).",
        },
        "category": {
            "type": "string",
            "enum": sorted(VALID_CATEGORIES),
            "description": "Problem category.",
        },
        "claim": {**_TEXT, "description": "Concrete statement of the issue."},
        "evidence": _EVIDENCE_SCHEMA,
        "suggested_fix": {**_TEXT, "description": "Optional short suggestion to fix the issue."},
    },
    ("claim",),
)


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="report_verification",
        description="Conclude verification with a verdict ('pass', 'fail', or 'inconclusive'), executed commands in "
        "'checked', concrete 'findings' backed by evidence (failing_test/command: command+exit_code; code_ref: "
        "file+line+reason — unproven findings downgrade to 'unproven'), and 'unverified' items. Verdict 'pass' "
        "is rejected when any proven blocker finding exists.",
        legacy_schema=_schema({
            "verdict": {
                "type": "string",
                "enum": sorted(VALID_VERDICTS),
                "description": "Overall verdict: pass, fail, or inconclusive.",
            },
            "passed": {
                "type": "boolean",
                "description": "Legacy boolean verdict (optional when verdict is provided).",
            },
            "checked": {
                "type": "array",
                "items": _CHECKED_ITEM_SCHEMA,
                "description": "Commands actually run to verify the change.",
            },
            "findings": {
                "type": "array",
                "items": _FINDING_ITEM_SCHEMA,
                "description": "Structured findings with mandatory evidence.",
            },
            "unverified": {
                "type": "array",
                "items": _TEXT,
                "description": "Checks that could not be executed and why.",
            },
            "summary": {
                **_TEXT,
                "description": "Optional short summary of what was verified.",
            },
        }),
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
