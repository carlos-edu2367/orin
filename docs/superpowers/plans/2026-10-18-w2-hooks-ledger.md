# W2 — Hooks ledger

Status of the 33 Claude Code hook events (`CLAUDE_HOOK_EVENTS` in `agentos.agentic.lifecycle`).
"Emitted" means Orin's code calls the bus for the event. "Decision applied" means the
returned decision changes behaviour. Hooks declared for an event that is not emitted never run.

| # | Event | Emitted | Decision applied | Where |
|---|---|---|---|---|
| 1 | PreToolUse | yes | yes: deny, ask, allow, `updatedInput`, context, stop | `agent_tools.py` `AgentToolset.invoke` |
| 2 | PostToolUse | yes | yes: `updatedMCPToolOutput` (MCP tools), context, blocking feedback, stop | `agent_tools.py` `invoke` |
| 3 | PostToolUseFailure | yes | no: the bus drops its context (non-blocking, not a context event) | `agent_tools.py` `invoke` |
| 4 | PostToolBatch | yes | no | `runtime.py` after each tool batch |
| 5 | Notification | no | n/a | not emitted |
| 6 | UserPromptSubmit | yes | yes: block (`prompt_block_reason`), context, system messages | `session.py` `_emit_turn_start_events` |
| 7 | UserPromptExpansion | no | n/a | not emitted |
| 8 | SessionStart | yes (first message of a conversation) | yes: context into the system prompt; env file | `session.py` `_emit_turn_start_events`, `_session_start_context` |
| 9 | SessionEnd | no | n/a | not emitted |
| 10 | Stop | yes | yes on the plain completion path: block continues the turn (max 5 continuations); other paths non-blocking | `runtime.py` |
| 11 | StopFailure | yes | no (informational) | `runtime.py` |
| 12 | SubagentStart | yes | no: context is not injected into the subagent yet | `session.py` `_execute_subagent_run` |
| 13 | SubagentStop | yes | no: blocking deferred (deviation 2) | `session.py` `_execute_subagent_run` |
| 14 | PreCompact | yes | yes: block (ordinary compaction only), instructions for the summary | `runtime.py` `_run_compaction` |
| 15 | PostCompact | yes | no (informational) | `runtime.py` |
| 16 | PreModelSwitch | no | n/a | not emitted |
| 17 | PostModelSwitch | no | n/a | not emitted |
| 18 | PermissionRequest | yes (only when a decision would otherwise ask) | yes: allow (with `updatedInput`), deny, interrupt | `agent_tools.py` `_invoke_unobserved` |
| 19 | PermissionDenied | yes | no (informational) | `agent_tools.py` `invoke` |
| 20 | Setup | no | n/a | not emitted |
| 21 | TeammateIdle | no | n/a | not emitted |
| 22 | TaskCreated | yes | no: blocking deferred | `agent_tools.py` `_emit_tool_side_events` |
| 23 | TaskCompleted | yes | no: blocking deferred (deviation 2) | `agent_tools.py` `_emit_tool_side_events` |
| 24 | Elicitation | no | n/a | not emitted |
| 25 | ElicitationResult | no | n/a | not emitted |
| 26 | ConfigChange | yes (one event per changed settings file, per turn) | no: blocking deferred (deviation 2) | `session.py` `_config_change_events` |
| 27 | WorktreeCreate | yes | no (informational) | `agent_tools.py` `_emit_tool_side_events` |
| 28 | WorktreeRemove | yes | no (informational) | `agent_tools.py` `_emit_tool_side_events` |
| 29 | InstructionsLoaded | yes | no (informational) | `session.py` `_emit_turn_start_events` |
| 30 | CwdChanged | yes | no (informational) | `agent_tools.py` `_emit_tool_side_events` |
| 31 | FileChanged | no | n/a | not emitted |
| 32 | DirectoryAdded | no | n/a | not emitted |
| 33 | MessageDisplay | no | n/a | not emitted |

Totals: 21 emitted, 12 not emitted. Decisions applied: PreToolUse, PostToolUse, UserPromptSubmit,
SessionStart, Stop, PreCompact, PermissionRequest.

## Deviations from the plan (and why)

1. **Process tree on Windows (diagnosis corrected by review W2-R1).** The Job Object works: `cmd`,
   `powershell` and `ping` grandchildren are all captured and killed by `TerminateJobObject`. What escapes
   the Job is a process started by a **Microsoft Store (MSIX) Python** (a packaged app is activated outside
   its creator's job); the venv interpreter here is backed by that Store Python. `processes.py` therefore
   still walks the process table for descendants while the hook process is alive, but it now accepts a
   process only when its creation time is consistent with its parent and root (a dead parent PID is
   recycled by Windows, so the parent-PID link alone is not proof), keeps the validated handles open,
   terminates through those handles, never returns Orin's own PID or ancestors, and no longer runs
   `taskkill /T`. See "Review fixes" below.
2. **Matcher and toolset tests** were written in the same step as their implementation for two tasks
   (Task 3 and Task 7). Red was verified afterwards: Task 3 by moving the module aside; Task 7 by
   running the new tests against the HEAD version of `agent_tools.py`.
3. **Tool-output reminder.** The plan's `_append_reminder` truncated the whole result, which could cut
   the reminder off entirely. The reminder is now kept whole and the result text is trimmed in front of it.
4. **Stop hook effect bookkeeping.** The Stop emit moved above the final `_effect_finished`; a
   continuing Stop finishes its effect with outcome `stop_hook_blocked` before looping.
5. **`updatedInput` mapping.** Hook output is in the public (Claude Code) shape, so it is converted with
   `arguments_in` before use.
6. **Session wiring additions.** `_toolset` takes a `lifecycle_context` argument; SessionStart system
   messages join the prompt context as well; `_install_hooks` removes earlier subscriptions before adding
   new ones; `_hook_agent` refuses re-entry (added after review, see below).
7. **Existing test updated.** `test_config_change_between_turns` now asserts the per-file payload
   (`source`, `file_path`), as the plan allows (W0-R15).

## Found during the adversarial pass

- An agent-type hook runs a verifier subagent whose tool calls fire the same hooks: unbounded
  recursion. Fixed with a re-entry guard in `session.py` `_hook_agent` (commit `b7f1ff9`). The guard is
  per session, so a top-level agent hook that runs in parallel with a verifier is skipped and returns no
  verdict (fail-open unless `onFailure: "block"`).

## Deferred items

- Plugin hooks onto the new dispatcher, and unification with `.claude-plugin` hooks: W5.
- Blocking for SubagentStop, TaskCompleted and ConfigChange: W3/W5.
- Context injection for PostToolUseFailure and SubagentStart: needs the bus to keep context for those events.
- Hooks declared in skill and agent frontmatter: W5. SDK-registered callbacks: W6. `/hooks` UI: W7.
- Secret redaction for HTTP hooks and streamed output (W0-R11): still open.
- Non-interactive "ask" handling (W1-R9): still open.
- Hook activity records include a label of up to 120 characters of the command text. A secret typed
  inline in a hook command would therefore appear in activity. Hook stdout and stdin are never recorded.
