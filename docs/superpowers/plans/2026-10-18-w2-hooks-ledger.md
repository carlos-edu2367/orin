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

## Review fixes (2026-10-18-w2-review.md)

Each finding was reproduced as a failing test first, then fixed. Commits: R1 `3ace026`, R2 `10bd80e` (+ `fef698d`), R3 `0e1bb4c`, R4+R5 `9e4bcd5`, R6 `a4a4522`, R7 `37fcda7`, R8 `1703201`, R9 `746370c`.

| Finding | Status | What changed | Residual risk |
|---|---|---|---|
| W2-R1 (critical) | fixed | A pure `_descendants_from(rows, root_pid, root_created, exclude)` accepts a child only if created no earlier than its parent and the root. Candidates are opened once; their handles stay open and are terminated through those handles. Orin's PID and ancestors are excluded. `taskkill /T` is gone. Deviation 1 corrected above. | Only exercised on Windows with Store-backed Python. No non-Store interpreter exists on this machine, so the non-Store variant was not run. Processes started after the snapshot and outside the Job can still survive. The pure rule is tested; the walk is tested only on our own processes. |
| W2-R2 (high) | fixed | Repository settings are filtered by where the file was found (lexical), not where it resolves. An untrusted link that leaves the workspace is dropped and reported as `settings link outside the workspace`. | A trusted workspace still loads through a link (intended: the trust decision covers the workspace). Link tests use a junction on Windows and a symlink elsewhere; the symlink branch was not run here. |
| W2-R3 (high) | fixed | `disableAllHooks` is decided per layer. Managed `true` turns every hook off. User or CLI `true` turns off every non-managed layer. A trusted repository's `true` does the same. An untrusted repository's value is reported in `untrusted_ignored`. | Design choice to confirm: a trusted repository's switch also silences user hooks (the review's reading of Claude Code). |
| W2-R4 (high) | fixed | A PermissionRequest `allow` with `updatedInput` is coerced, checked by plan mode and the permission engine, and only then runs. A deny always stands. | none beyond the engine's own rules |
| W2-R5 (med-high) | fixed | A PreToolUse `allow` skips only ordinary asks. `requires_human` asks (protected paths, ask rules) still go to a person or fail closed without one. The same rule covers PermissionRequest `allow`. | Behaviour change: a hook can no longer auto-approve a protected-path write, even in acceptEdits. This matches spec 4.5. |
| W2-R6 (high) | fixed (see notes) | A blocked prompt is erased: its text, command expansion and the title derived from it are replaced, and its user message is marked `blocked_by_hook` (existing status column, no migration). `history_for_turn` skips the marked message and its turn's assistant message. The runtime calls the erasure before `finish`, so kernel-managed turns are covered. A blocked turn skips @-mention reads, memory ranking, the plan scaffold and the skill catalog. | (a) The minimal `last_error` approach was rejected: kernel-managed turns never write that code. (b) The user sees a placeholder where their blocked text was. (c) Attachments of a blocked message stay on disk. (d) `SessionStart` counts messages in the filtered history, so it may fire again on the first accepted prompt after a blocked first prompt. Not fixed. |
| W2-R7 (med) | fixed | `parse_verdict` returns the last `ok: false` candidate if there is one, otherwise the last `ok: true`. The old test that encoded the disagreement-is-None behaviour was updated (`test_verdicts`). | A forged `ok: true` with no refusal still approves; the verifier model's own output is the limit. |
| W2-R8 (med) | fixed (see notes) | Timed-out prompt and agent hooks are cancelled: the verifier child observes the event, the prompt stream is closed, queued calls never start. One bounded pool per runner (2 workers). The verifier guard is a non-blocking lock. The dispatcher closes when superseded and when the turn's runtime closes. | A verifier that ignores cancel keeps running and holds the lock, so later agent hooks in that turn return no verdict (fail-open unless `onFailure: block`). Cancel is checked between child iterations, not inside a long blocking tool call. Up to 2 threads per runner can stay stuck. The concurrent-verifier test passed before the fix too: the old race was not reliably reproducible. |
| W2-R9 (med) | fixed | Each shell pipe is drained on its own thread and keeps at most 1 MiB; the stdin payload is written on its own thread. HTTP hook bodies are streamed and read only to the 64 KiB cap. | Pipe readers are joined for at most 2 s (5 s after a timeout). A grandchild holding a pipe leaves a daemon reader thread behind. RSS was not measured; the cap was asserted through the buffer and the bytes read. |

### Verification

- `uv run pytest -q tests/unit`: 2857 passed, 53 skipped, 0 failed (baseline before fixes: 2830 passed).
- `npm --prefix frontend test`: 100 files, 596 tests passed.
- `npm --prefix frontend run lint`: clean.
- Throwaway re-check of the review reproductions (R1 pure rule, R2 to R9), kept outside the repo: 14 of 14 checks pass. R6 is covered by its tests only, not by the re-check script.
- Each fix was shown red first, on the unfixed code. R2's session-level test was also shown to fail on the pre-R2 loader.

### Not done

- Linux and macOS were not run. The symlink branches and the POSIX process paths are unverified here.
- Visual checks are not applicable: no UI changed in this pass.
