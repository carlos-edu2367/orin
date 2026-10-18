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
| Stop | yes | `AgenticTurnRuntime.run` | — |
| StopFailure | yes | `AgenticTurnRuntime._fail` | — |
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
