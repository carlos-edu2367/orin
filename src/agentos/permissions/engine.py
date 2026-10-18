"""deny -> plan -> ask -> protected -> allow -> built-ins -> mode fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentos.code_mode.models import CodePermissionMode, normalize_permission_mode
from agentos.settings import EffectiveSettings

from .paths import is_protected_path, workspace_path
from .rules import EDIT_FAMILY, MatchContext, PermissionRule, command_allowed, parse_rule, rule_matches
from .shell import analyze_command, default_shell

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read_file", "view_file", "transcribe_pdf", "list_files", "search_files", "search_code", "glob_files",
    "project_map", "todo_write", "task_create", "task_update", "task_get", "task_list", "ask_user",
    "enter_plan_mode", "exit_plan_mode", "write_contract", "report_verification", "use_skill",
    "search_skills", "list_skills", "read_skill_resource", "recall", "check_subagent", "read_process_output",
    "preview_logs", "search_mcp_tools", "inspect_mcp_server", "list_mcp_servers", "list_mcp_catalog",
    "list_plugins", "search_plugin", "list_projects", "read_project_conversation",
})
DELEGATION_TOOLS: frozenset[str] = frozenset({"run_subagent", "create_agent", "ask_agent", "ask_agents"})
SAFE_FILE_COMMANDS: frozenset[str] = frozenset({"mkdir", "touch", "cp", "mv"})
_PLAN_SUBAGENTS = frozenset({"explore", "plan"})
# cp/mv flags that cannot redirect a write elsewhere. Any other flag makes the call ask.
_SAFE_TRANSFER_FLAGS = frozenset({
    "-r", "-R", "-f", "-i", "-p", "-v", "-a", "-n", "-u", "--recursive", "--force", "--verbose",
    "--preserve", "--no-clobber", "--interactive", "--archive", "--update",
})


def _canonical_variants(word: str) -> list[str]:
    """Readings of one shell word: quotes removed, backslash as an escape or as a Windows separator."""
    base = str(word).replace('"', "").replace("'", "")
    return list(dict.fromkeys([base.replace("\\", ""), base.replace("\\", "/")]))


def _has_glob(word: str) -> bool:
    return any(char in word for char in "*?[")

Classifier = Callable[[str, Mapping[str, Any]], "PermissionDecision"]


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    behavior: str
    reason: str
    rule: str | None = None
    source: str | None = None
    # True when only a person may approve (an explicit ask rule or a protected
    # path): without an interactive approver such a call is denied, never run.
    requires_human: bool = False


def _rules(settings: EffectiveSettings, kind: str) -> list[PermissionRule]:
    parsed: list[PermissionRule] = []
    for raw, source in settings.permission_rules(kind):
        rule = parse_rule(raw, kind=kind, source=source)
        if rule is not None:
            parsed.append(rule)
    return parsed


class PermissionEngine:
    def __init__(
        self,
        workspace_root: Path,
        settings: EffectiveSettings,
        *,
        shell: str | None = None,
        home: Path | None = None,
        classifier: Classifier | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.settings = settings
        self.ctx = MatchContext(self.workspace_root, Path(home) if home is not None else Path.home(), shell or default_shell())
        self.classifier = classifier
        self._deny = _rules(settings, "deny")
        self._ask = _rules(settings, "ask")
        self._allow = _rules(settings, "allow")

    def has_rules(self) -> bool:
        return bool(self._deny or self._ask or self._allow)

    # -- helpers -----------------------------------------------------------

    def _first(self, rules: list[PermissionRule], internal: str, args: Mapping[str, Any], analysis) -> PermissionRule | None:
        for rule in rules:
            try:
                if rule_matches(rule, internal, args, self.ctx, analysis=analysis):
                    return rule
            except Exception:  # noqa: BLE001 - a malformed rule never grants or breaks
                logger.debug("permission rule %s failed to evaluate", rule.raw, exc_info=True)
        return None

    def _target(self, args: Mapping[str, Any]) -> Path | None:
        raw = args.get("path") or args.get("file_path") or args.get("notebook_path")
        if not raw:
            return None
        return workspace_path(str(raw), self.workspace_root)

    def _inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.workspace_root.resolve())
            return True
        except (OSError, ValueError):
            return False

    def _is_plan_file(self, args: Mapping[str, Any]) -> bool:
        target = self._target(args)
        if target is None or not self._inside(target):
            return False
        relative = target.resolve().relative_to(self.workspace_root.resolve()).as_posix()
        return relative.lower().startswith(".orin/plans/") and len(relative.split("/")) >= 3

    def _command_tokens(self, subcommand: str) -> list[str]:
        """Every reading of every argument, options included (``--target-directory=DIR``, ``-tDIR``)."""
        words: list[str] = []
        for token in subcommand.split(" ")[1:]:
            if not token:
                continue
            if token.startswith("-"):
                body = token.lstrip("-")
                words.extend([body, token[2:]])
                if "=" in body:
                    words.append(body.split("=", 1)[1])
            else:
                words.append(token)
        return [variant for word in words for variant in _canonical_variants(word)]

    def _transfer_operands(self, subcommand: str) -> tuple[list[str], list[str]] | None:
        """``cp``/``mv`` operands and explicit target directories, or None when a flag is not understood."""
        operands: list[str] = []
        targets: list[str] = []
        tokens = [token for token in subcommand.split(" ")[1:] if token]
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"-t", "--target-directory"} and index + 1 < len(tokens):
                targets.append(tokens[index + 1])
                index += 2
                continue
            if token.startswith("--target-directory="):
                targets.append(token.split("=", 1)[1])
            elif token.startswith("-t") and not token.startswith("--"):
                targets.append(token[2:])
            elif token in {"-T", "--no-target-directory"}:
                pass
            elif token.startswith("-"):
                if token not in _SAFE_TRANSFER_FLAGS:
                    return None
            else:
                operands.append(token)
            index += 1
        return operands, targets

    def _token_paths(self, token: str) -> list[Path]:
        """Every meaning a shell token can have as a path.

        A root-relative token ("/tmp/x", "\.env") is checked both as the real
        filesystem path the shell opens and as the workspace path the tools open.
        """
        if token.startswith("~"):
            return [Path(token).expanduser()]
        if token.startswith(("/", "\\")):
            return [Path(token), workspace_path(token, self.workspace_root)]
        return [workspace_path(token, self.workspace_root)]

    def _mentions_protected(self, analysis) -> bool:
        for sub in analysis.subcommands:
            for token in self._command_tokens(sub):
                if any(is_protected_path(path, workspace_root=self.workspace_root) for path in self._token_paths(token)):
                    return True
        for target in analysis.redirect_targets:
            for variant in _canonical_variants(target):
                if any(is_protected_path(path, workspace_root=self.workspace_root) for path in self._token_paths(variant)):
                    return True
        return False

    def _inside_unprotected(self, word: str) -> bool:
        for variant in _canonical_variants(word):
            if _has_glob(variant):
                return False
            for target in self._token_paths(variant):
                if not self._inside(target) or is_protected_path(target, workspace_root=self.workspace_root):
                    return False
        return True

    def _safe_file_command(self, analysis) -> bool:
        if analysis.complex or not analysis.subcommands:
            return False
        for sub in analysis.subcommands:
            name = sub.split(" ", 1)[0]
            if name not in SAFE_FILE_COMMANDS:
                return False
            if name in {"cp", "mv"}:
                parsed = self._transfer_operands(sub)
                if parsed is None:
                    return False
                operands, targets = parsed
                if not operands and not targets:
                    return False
                if not targets and len(operands) < 2:
                    return False
                if not all(self._inside_unprotected(word) for word in operands + targets):
                    return False
                continue
            for token in [token for token in sub.split(" ")[1:] if token and not token.startswith("-")]:
                if not self._inside_unprotected(token):
                    return False
        return True

    def _bash_allowed(self, analysis, args: Mapping[str, Any]) -> PermissionRule | None:
        bash_rules = [rule for rule in self._allow if rule.tool in {"run_command", "*"}]
        unconditional = next((rule for rule in bash_rules if rule.specifier is None), None)
        if unconditional is not None:
            return unconditional
        if analysis.complex or not analysis.subcommands:
            return None
        matched: PermissionRule | None = None
        for sub in analysis.subcommands:
            rule = next((r for r in bash_rules if command_allowed(r.specifier, sub, shell=self.ctx.shell)), None)
            if rule is None:
                return None
            matched = matched or rule
        return matched

    # -- pipeline ----------------------------------------------------------

    def decide(
        self,
        internal: str,
        arguments: Mapping[str, Any] | None,
        mode: CodePermissionMode | str,
        *,
        plan_mode_active: bool = False,
    ) -> PermissionDecision:
        args = dict(arguments or {})
        resolved = normalize_permission_mode(mode)
        analysis = analyze_command(str(args.get("command") or ""), shell=self.ctx.shell) if internal == "run_command" else None

        denied = self._first(self._deny, internal, args, analysis)
        if denied is not None:
            return PermissionDecision("deny", f"denied by rule {denied.raw}", denied.raw, denied.source)

        if plan_mode_active or resolved is CodePermissionMode.PLAN_MODE:
            if internal in READ_ONLY_TOOLS:
                return PermissionDecision("allow", "read-only tool in plan mode")
            if internal in {"run_subagent", "ask_agent"}:
                kind = str(args.get("subagent_type") or args.get("name") or "").strip().lower()
                if kind in _PLAN_SUBAGENTS:
                    return PermissionDecision("allow", "read-only subagent in plan mode")
            if internal in EDIT_FAMILY and self._is_plan_file(args):
                return PermissionDecision("allow", "plan file")
            return PermissionDecision("deny", "plan mode is read-only")

        asked = self._first(self._ask, internal, args, analysis)
        if asked is not None:
            if resolved is CodePermissionMode.DONT_ASK:
                return PermissionDecision("deny", f"rule {asked.raw} requires a prompt and dontAsk denies prompts", asked.raw, asked.source)
            return PermissionDecision("ask", f"rule {asked.raw} requires confirmation", asked.raw, asked.source, requires_human=True)

        protected = False
        if internal in EDIT_FAMILY:
            target = self._target(args)
            protected = target is not None and is_protected_path(target, workspace_root=self.workspace_root)
        elif analysis is not None:
            protected = self._mentions_protected(analysis)
        if protected:
            if resolved is CodePermissionMode.FULL_AUTONOMY:
                return PermissionDecision("allow", "protected path, bypass mode")
            if resolved is CodePermissionMode.DONT_ASK:
                return PermissionDecision("deny", "protected path and dontAsk denies prompts")
            return PermissionDecision("ask", "protected configuration path", requires_human=True)

        if internal == "run_command" and analysis is not None:
            allowed = self._bash_allowed(analysis, args)
        else:
            allowed = self._first(self._allow, internal, args, analysis)
        if allowed is not None:
            return PermissionDecision("allow", f"allowed by rule {allowed.raw}", allowed.raw, allowed.source)

        if internal in READ_ONLY_TOOLS or internal in DELEGATION_TOOLS:
            return PermissionDecision("allow", "read-only or delegation tool")
        if internal in EDIT_FAMILY and self._is_plan_file(args):
            return PermissionDecision("allow", "plan file")

        if resolved is CodePermissionMode.FULL_AUTONOMY:
            return PermissionDecision("allow", "bypass mode")
        if resolved is CodePermissionMode.ACCEPT_EDITS:
            if internal in EDIT_FAMILY:
                target = self._target(args)
                if target is not None and self._inside(target):
                    return PermissionDecision("allow", "acceptEdits: edit inside the workspace")
            if internal == "run_command" and analysis is not None and self._safe_file_command(analysis):
                return PermissionDecision("allow", "acceptEdits: file command inside the workspace")
            if internal == "git_commit":
                return PermissionDecision("allow", "acceptEdits: local commit")
            return PermissionDecision("ask", "acceptEdits asks for this tool")
        if resolved is CodePermissionMode.AUTO:
            if self.classifier is None:
                return PermissionDecision("ask", "auto mode classifier unavailable")
            try:
                verdict = self.classifier(internal, args)
            except Exception:  # noqa: BLE001 - classifier failure means a person decides
                logger.warning("auto mode classifier failed", exc_info=True)
                return PermissionDecision("ask", "auto mode classifier failed")
            if isinstance(verdict, PermissionDecision) and verdict.behavior in {"allow", "ask", "deny"}:
                return verdict
            return PermissionDecision("ask", "auto mode classifier returned no decision")
        if resolved is CodePermissionMode.DONT_ASK:
            return PermissionDecision("deny", "dontAsk denies anything that would prompt")
        return PermissionDecision("ask", "default mode asks before side effects")
