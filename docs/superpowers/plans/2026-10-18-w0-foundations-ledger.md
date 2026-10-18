# W0 Foundations: lifecycle emission ledger and deviations

Companion to `2026-10-18-w0-foundations.md`. Lists which of the 35 `HookEvent` values W0 emits, from where, and which later wave owns the rest.

| Event | Emitted in W0 | Where | Deferred to |
|---|---|---|---|
| PreToolUse | yes | `AgentToolset.invoke` | — |
| PostToolUse | yes | `AgentToolset.invoke` | — |
| PostToolUseFailure | yes | `AgentToolset.invoke` | — |
| PostToolBatch | yes | `AgenticTurnRuntime._run_toolset` | — |
| Notification | no | — | W7 |
| UserPromptSubmit | yes | `TurnSession._emit_turn_start_events` | — |
| UserPromptExpansion | no | — | W7 |
| SessionStart | yes | `TurnSession._emit_turn_start_events` | — |
| SessionEnd | no | — | W3 |
| Stop | yes | `AgenticTurnRuntime._run`: completed, completed_with_caveats (`stop_reason`), waiting_user (`stop_reason`) | — |
| StopFailure | yes | `AgenticTurnRuntime._fail`, the two `reconciliation_required` returns, and `AgenticTurnRuntime.run` when an exception escapes (`error_code=RUNTIME_EXCEPTION`) | — |
| SubagentStart | yes | `TurnSession._execute_subagent_run` | — |
| SubagentStop | yes | `TurnSession._execute_subagent_run` | — |
| PreCompact | yes | `AgenticTurnRuntime._run_compaction` | — |
| PostCompact | yes | `AgenticTurnRuntime._run_compaction` | — |
| PreModelSwitch | no | — | W3 |
| PostModelSwitch | no | — | W3 |
| PermissionRequest | yes | `AgentToolset._invoke_unobserved` | — |
| PermissionDenied | yes | `AgentToolset.invoke` | — |
| Setup | no | — | W6 |
| TeammateIdle | no | — | W5 |
| TaskCreated | yes | `AgentToolset._emit_tool_side_events` | — |
| TaskCompleted | yes | `AgentToolset._emit_tool_side_events` | — |
| Elicitation | no | — | W5 |
| ElicitationResult | no | — | W5 |
| ConfigChange | yes | `TurnSession._emit_turn_start_events` | — |
| WorktreeCreate | yes | `AgentToolset._emit_tool_side_events` | — |
| WorktreeRemove | yes | `AgentToolset._emit_tool_side_events` | — |
| InstructionsLoaded | yes | `TurnSession._emit_turn_start_events` | — |
| CwdChanged | yes | `AgentToolset._emit_tool_side_events` | — |
| FileChanged | no | — | W3 |
| DirectoryAdded | no | — | W1 |
| MessageDisplay | no | — | W7 |
| StatusLine | no | — | W7 |
| FileSuggestion | no | — | W7 |

Emitted in W0: 20 of the 35. Deferred: 15.

## Intentional deviations from the master spec

1. The god class `AgentToolset` is not split in W0. The physical split happens in W4, when each tool is rewritten to Claude Code parity. W0 delivers the registry metadata (`public_name`, `internal_name`, `permission_category`).
2. User settings live in `~/.orin/` (override with env `ORIN_USER_DIR`), not in `ORIN_CONFIG_DIR`. In a source checkout `ORIN_CONFIG_DIR` is the repository root, which would make a repo-root `settings.json` look like user settings.
3. No HTTP endpoint for effective settings yet. The UI that needs it is W7.
4. `EffectiveSettings` is a frozen dataclass, not a Pydantic model (spec: Pydantic). Owner: W1, when the settings model gains permission and trust fields. Proposed owner, not set by the review.
5. There is no one-time import of `data/agent-runtime.json` into the settings layers. Owner: W1 (proposed, not set by the review).
6. The settings writer has no mtime check before replacing a file (spec §3.2). Owner: W1, the first real writer use (review W0-R16 defers to W1).
7. The lifecycle bus does not feed SSE and does not replace `runtime._hooks` or `runtime._life` (spec §3.3). Owner: W2 for the hook path, W7 for SSE (proposed split).
8. There are no `--settings` or `--setting-sources` CLI flags, only the `cli=` and `sources=` parameters of `load_effective_settings`. Owner: W7 (proposed, not set by the review).
9. "Registry metadata" (`public_name`, `internal_name`, `permission_category`) is a set of free functions, not `ToolDefinition` fields. Owner: W4, when the tools are rewritten and the god class is split.

## Deferred review findings

Source: `docs/superpowers/reviews/2026-10-18-w0-review.md`. W0-R1 to W0-R9, W0-R12, W0-R13, W0-R16 and W0-R17 are fixed in W0. The findings below are deferred, with the wave the review assigns to each.

| ID | Sev | Deferred to | Summary |
|---|---|---|---|
| W0-R10 | medium | W2 | Subagent tool calls emit PreToolUse/PostToolUse with no `agent_id` or `agent_type`. |
| W0-R11 | medium | W2 (decision needed) | Event payloads carry raw `tool_input`, tool output, assistant text and prompts; redaction is needed before any sink (SSE, activity, HTTP hooks) persists or streams them. |
| W0-R14 | low | W3 | SessionStart is guessed with `user_messages <= 1`; take an explicit `source` from the conversation store. |
| W0-R15 | low | W2 | ConfigChange and InstructionsLoaded use `source="settings"` and `file_paths`, not Claude Code's per-file shape; snapshots are keyed by root only and a failed load records `{}`. |
| W0-R18 | low | W1 for permissions, W7 for `/status` | Lists are unioned at every path, and provenance names only the last layer for unioned lists. |
| W0-R19 | nit | Now (docs), owners in deviations 4 to 9 above | Spec items not delivered and missing from Deviations. |
| W0-R20 | nit | W3 (system prompt) and W4 (tool results) | With the flag on, prompts and tool results still name snake_case tools and keys. |
| W0-R21 | nit | W4 | With the flag on, public schemas hide internal capabilities (`edits`, `cwd`, `write_file.mode`, `force_reload`, `max_results`); `ask_user` translation is lossy. |

Also outside the W0 diff, reported in the review and due first in W1: PRE-1 (`src/agentos/code_mode/permissions.py`): the permission broker honours `permissions.allow` from a committed repository file without a trust check.
