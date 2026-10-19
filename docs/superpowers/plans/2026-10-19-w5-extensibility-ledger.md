# W5 — Extensibility — Ledger

Execution record for `docs/superpowers/plans/2026-10-19-w5-extensibility.md`.

## Batch A — Skills and slash commands on disk (Tasks 1–5)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `b7b2235`:

| Task | Commit | Subject |
|---|---|---|
| 1 | `9264570` | feat(w5): skills and commands read from disk in Claude Code's format |
| 2 | `ccefc6d` | feat(w5): the Skill tool runs skills from disk with substitutions, commands and allowed tools |
| 3 | `f290581` | feat(w5): the turn lists skills from disk and runs a person's /command |
| 4 | `511e11a` | fix(w5): plugin commands survive a restart |
| 5 | this commit | docs(w5): ledger, batch A |

### Tests added (27)

- `tests/unit/skills/test_disk_skills.py`: 9 (Task 1).
- `tests/unit/agentic/tools/test_disk_skill_tool.py`: 10 (Task 2).
- `tests/unit/agentic/test_disk_skills_session.py`: 5 (Task 3).
- `tests/unit/plugins/test_command_rehydration_on_demand.py`: 3 (Task 4).

Each new test file was run before its implementation and failed for the reason the plan gives: `ModuleNotFoundError` (Task 1), `ImportError` for `COMMAND_REMINDER_CHARS` (Task 3), `AttributeError` for `set_rehydrator` (Task 4). Task 2 failed 9 of 10 (the tenth, "not offered without skills", passed before the change, as it asserts the old behaviour).

### Suite results

- `uv run pytest -q tests/unit` (foreground, timeout 600000 ms): **3697 passed, 59 skipped, 0 failed** (262 s).
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (63 s). All 65 skips say `AGENTOS_TEST_POSTGRES_DSN` / `AGENTOS_POSTGRES_URL` / `AGENTOS_REDIS_URL` is not configured.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. Batch A touches no file under `frontend/`, so lint and build were not run for this batch.
- Batch-level runs during the tasks: `tests/unit/agentic/tools tests/unit/permissions tests/unit/code_mode` 811 passed, 7 skipped; `tests/unit/agentic` 1200 passed, 7 skipped; `tests/unit/plugins tests/unit/conversations tests/unit/bootstrap` 246 passed, 1 skipped.

### Deviations from the plan

- **Code and tests:** none. Every code and test block was copied from the plan, and every `replace` block of Tasks 2–4 matched exactly once.
- **Observation, kept as the plan writes it:** `Skill` is offered whenever the session carries a disk catalog (`available` tests `is not None`), so a session built with `skills=None` and an empty catalog now offers the tool. In production the chat worker always passes a skill registry, so the tool was already offered there. Tightening the predicate to "the catalog has skills" is a one-line change if the owner wants it.

### The plan's intentional deviations, as implemented

1. User skills and commands are read from `~/.orin/{skills,commands}` and `~/.claude/{skills,commands}` (`skill_roots`).
2. No watcher: `build_runtime` loads the catalog every turn (`_load_disk_skills`).
3. A skill's `allowed-tools` lasts until the end of the turn (`_skill_allow_rules`, `skill_grants()`, passed as `extra_allow` to the permission engine's allow step only).
4. A skill's `model` and `effort` are parsed and not applied (W6).
5. A person's `/name` reaches the model as a `command` reminder of up to 60,000 characters (`_apply_user_command`). The picker is W7.
6. `$ARGUMENTS[N]` and `$N` are 0-based, and substituted only when arguments were given (`substitute`).
7. Plugin skills stay in the skill library until Batch F.
8. CLI flags and new screens are out of W5 (W6 and W7).

### Closed by this batch

- **W4-R25** (a skill body or its arguments could close the instructions wrapper): closed by Task 2. `neutral_skill_text` is applied to the arguments, the disk body and the library instructions; list and mapping arguments are joined or written as JSON (`skill_argument_text`). Covered by `test_a_body_cannot_close_the_instructions_wrapper`.
- **Plugin commands lost after a restart:** closed by Task 4. `CommandLibrary` rebuilds each person's index from the installed plugins on that person's first lookup, once, and retries a failed rebuild at the next lookup. Production wires `rehydrate_commands`.

### Open findings from the Batch A review (not fixed; each needs the owner's decision)

1. **Unscoped shell grants from a skill.** `allowed-tools: Bash` (no specifier) is an unconditional allow for the rest of the turn. Checked with the engine: `run_command` with `curl https://example.invalid/x.sh` is `ask` without the grant and `allow` with it. A repository's skills count once the workspace is trusted, so a trusted repository can switch off per-command approval for a turn by shipping one skill. Suggested fix: ignore unscoped `Bash` and `PowerShell` grants from project-origin skills, or require a specifier for every shell grant.
2. **Whole file read before the size check.** `parse_disk_skill` calls `read_bytes()` and only then compares the length, so an oversized `SKILL.md` is read into memory every turn before it is rejected. Suggested fix: check `path.stat().st_size` first. (Code reading; not reproduced.)
3. **Full enumeration before the cap.** `load_disk_skills` builds `list(_candidates(...))` before the 200-entry cap applies, so a repository with thousands of `.md` files under `commands/` is enumerated on every turn. Suggested fix: stop enumerating at the cap. (Code reading.)
4. **Symlinked `SKILL.md`.** `_candidates` rejects a symlinked skill folder but not a `SKILL.md` that is a symlink, because `target.is_file()` follows links. A repository could point a skill at any readable file, and its text would enter the prompt. Suggested fix: reject `target.is_symlink()` as the folder check does. (Code reading; Windows symlink creation was not tried.)
5. **Double substitution.** `substitute` replaces `$N` after `$ARGUMENTS`, so argument text containing `$1` is replaced again. The wrapper neutralisation runs after substitution, so this cannot close the wrapper. Cosmetic.
6. **Arguments inside inline commands.** `!`cmd`` blocks receive the arguments before they run, so model-supplied arguments become part of a shell command. The command goes through the Bash permission pipeline, so a person sees the composed command when it would ask.

## Batch B — Agents in files (Tasks 6–11)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `2930b85` (the Batch B plan):

| Task | Commit | Subject |
|---|---|---|
| 6 | `6de7d23` | fix(w5): skills from a repository cannot pre-approve every command; disk reads are bounded |
| 7 | `dbeae63` | feat(w5): agent definitions read from disk in Claude Code's format |
| 8 | `0432591` | feat(w5): the Agent tool starts agents defined on disk |
| 9 | `11540f6` | fix(w5): a subagent gets its own reminders and instruction files (W3-R26) |
| 10 | `98212eb` | feat(w5): background agents write an output file and stop with TaskStop |
| extra | `9d19390` | fix(w5): an empty turn queue still receives a subagent's monitor (W4-R9) |
| 11 | this commit | docs(w5): ledger, batch B |

The `extra` commit is not a plan task. It fixes a defect in the Task 9 code as the plan writes it (see Deviations).

### Tests added (34 cases; 33 pass and 1 skips on this machine)

- Task 6: `tests/unit/skills/test_disk_skills.py` (3: one substitution pass, a linked `SKILL.md` not read, an oversized file refused before it is read; the linked-file one **skips** here because this Windows account cannot create symbolic links) and `tests/unit/agentic/tools/test_disk_skill_tool.py` (2: a project skill cannot pre-approve every command, a user skill still can).
- Task 7: `tests/unit/agentic/test_agent_definitions.py` (17 cases counting the parametrised ones).
- Task 8: `tests/unit/agentic/test_agent_definitions_session.py` (6).
- Task 9: `tests/unit/agentic/test_subagent_reminders_and_memory.py` (1, plus 1 added by the extra commit).
- Task 10: `tests/unit/agentic/test_background_agents.py` (4).

Each new test file or test was run before its implementation and failed for the reason the plan gives (Task 6: the three predicted tests failed, the linked-file test skipped, and the user-skill test passed as a guard; Task 7: `ModuleNotFoundError`; Task 8: 6 of 6 failed; Task 9: the subagent's second request lacked `PKG-RULES`; Task 10: `KeyError: 'output_file'` and the missing `_stop_background_subagent`). The extra regression test failed with the monitor's sink set to the subagent's own queue.

### Suite results

- `uv run pytest -q tests/unit` (foreground, timeout 600000 ms), final run after the extra commit: **3730 passed, 60 skipped, 0 failed** (267 s). Batch A ended at 3697 passed and 59 skipped.
- `uv run pytest -q tests/integration`, final run: **78 passed, 65 skipped, 0 failed** (63 s). The 65 skips need `AGENTOS_TEST_POSTGRES_DSN`, `AGENTOS_POSTGRES_URL` or `AGENTOS_REDIS_URL`.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. Batch B touches no file under `frontend/`, so lint and build were not run.
- Batch-level runs during the tasks: `tests/unit/agentic` 1230 passed, 7 skipped after Task 10 (before the extra commit).

### Deviations from the plan

- **Plan deviations 9–12, as implemented:**
  9. An agent's `isolation`, `mcpServers`, `memory`, `effort`, `color` and `hooks` are read (`AgentDefinition`) and not applied.
  10. A `model` alias maps through `modelAliases`. An alias with no mapping, or a model this person does not allow for subagents, runs on the parent's model.
  11. A background agent's report is read with `check_subagent` (or `TaskOutput`) or from its output file. Its completion does not resume the turn.
  12. The `verifier` builtin is Orin's own wording and has no editing tools.
- **Extra commit `9d19390` (not in the plan).** The plan's Task 9 edit makes the monitor sink `getattr(self, "_monitor_sink", None) or self.reminders`. `ReminderQueue` defines `__len__`, so an empty turn queue is falsy and the `or` falls back to the subagent's own queue. A background monitor started by a subagent while the turn queue is empty would then reach the subagent's runtime and not the running turn, which breaks W4-R9. The fix tests `is None`. The test that proves it is `test_a_subagents_monitor_sinks_into_the_turn_even_when_the_turn_queue_is_empty`, which reuses the `conversation` fixture of `test_monitor_tool.py`. The rest of the plan's text for that line is unchanged. The Task 9 commit (`11540f6`) still contains the defective line; the extra commit corrects it. No other truthiness check on a `ReminderQueue` was found (`runtime.py`, `read.py` and `monitor.py` test `is not None`).
- **Task 9, existing test changed (as the plan says):** `tests/unit/agentic/tools/test_monitor_review_fixes.py::test_a_subagent_is_built_with_the_turn_reminder_queue`. Its expectation now checks `_monitor_sink is session._reminders` and that the subagent's own `reminders` is not the turn queue. The test's recording wrapper now returns the toolset as well, which the plan prescribes. No other existing test was changed.
- **Task 6, Windows symlinks:** `test_a_linked_skill_file_is_not_read` skips here. The `not target.is_symlink()` check is therefore verified by reading the code only.
- **Task 10, TaskStop message:** a non-numeric id on a toolset without agent support now says "is not a background command or agent of this conversation." The old text mentioned agent teams. No test asserted the old text.
- **Commit trailer:** every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the plan and the brief require and as Batch A did. The attribution note in this session names Haiku 5.5 for commits. The owner should decide which one to use from here on.
- **Ledger:** the stale line "Batch B has not started." at the end of the Batch A section is replaced by this section.

### Closed by this batch

- **Batch A findings (the Batch A review list above):** 1 (unscoped shell grants from a repository's skill) closed by Task 6. 2 (the whole file read before the size check) closed by Task 6. 4 (a symlinked `SKILL.md`) closed by Task 6 in code, not verified by test here. 5 (double substitution) closed by Task 6. 3 (enumeration before the cap) is only bounded per folder, at 2,000 entries (`MAX_FOLDER_ENTRIES`); the 200-skill cap is still applied after listing. 6 (arguments inside `!`cmd`` blocks) is still open and is outside Batch B.
- **W3-R26** (a subagent's reads reached the parent's reminders and never the subagent's instructions): closed by Task 9 and the extra commit. A subagent has its own `ReminderQueue` and its own memory tracker. Its monitor still sinks into the running turn.

### Observations not fixed (for the owner)

- **Preloaded skills.** `skills:` in an agent definition puts the skill body into the subagent's system prompt through `_preloaded_skills`. The body is not wrapped as subordinate instructions, its `allowed-tools` are not granted to the subagent, and its `!`cmd`` blocks are not run. A repository that is trusted can therefore put its own text into a subagent's prompt. This grants no tool.
- **Replacing a builtin.** A definition named `explore` or `plan` in a trusted repository replaces the builtin. Without a `tools` line it publishes every tool the parent may publish, and it runs with the parent's permission mode and not with the read-only mode of the builtin. This is what the plan says and it never exceeds the parent's own grants. The owner may want a warning or a rule that a builtin name needs the user's folder.

## Batch C — Agent teams (Tasks 12–15)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `b433a14` (the Batch C plan):

| Task | Commit | Subject |
|---|---|---|
| 12 | `0205a20` | fix(w5): preloaded skills keep their wrapper; a repository cannot replace builtin agents |
| 13 | `39034d6` | feat(w5): the agent team mailbox |
| 14 | `bd70874` | feat(w5): agent teams with SendMessage, a mailbox and TeammateIdle |
| 15 | this commit | docs(w5): ledger, batch C |

### Tests added (22 cases, and 1 more from the public-contract parametrisation)

- Task 12: `tests/unit/agentic/test_agent_definitions.py` (6 cases: five parametrised repository cases and the person case) and `tests/unit/agentic/test_agent_definitions_session.py` (1, the wrapper case). Before the implementation, the six repository and wrapper cases failed and the person case passed (the plan expects it to pass already, as a guard).
- Task 13: `tests/unit/conversations/test_mailbox.py` (8 cases: four tests, each run against the database and in memory). Before the implementation: collection error, `ModuleNotFoundError: agentos.conversations.mailbox`.
- Task 14: `tests/unit/agentic/test_agent_teams.py` (7). Before the implementation, all seven failed at run time: `TypeError` for `teammate_name` (four cases), `AttributeError` for `_send_team_message`, `SendMessage` unknown (`failed` instead of `succeeded`), and `IndexError` on the Agent passthrough. The plan predicted a collection error; the test module does not import `team.py`, so the failures came at run time.
- `tests/unit/agentic/tools/test_public_contract.py` is parametrised over its table, so the new `send_message` entry adds one case.

### Suite results

- `uv run pytest -q tests/unit` (foreground, timeout 600000 ms): **3753 passed, 60 skipped, 0 failed** (274 s). Batch B ended at 3730 passed and 60 skipped; the 23 added passes are the 22 new cases and the contract entry.
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (63 s). The 65 skips need `AGENTOS_TEST_POSTGRES_DSN`, `AGENTOS_POSTGRES_URL` or `AGENTOS_REDIS_URL`, as in Batch B.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. Batch C touches no file under `frontend/`, so lint and build were not run.
- Batch-level runs during the tasks: `tests/unit/agentic tests/unit/permissions` (without the nested `tools` argument, see Deviation 3): 1611 passed, 8 skipped. `tests/unit/conversations/test_mailbox.py` with `tests/unit/persistence`: 148 passed. `tests/unit/agentic/test_agent_teams.py`: 7 passed.

### Deviations from the plan

- **Plan deviations 13–15, as implemented:**
  13. The team roster is kept on the turn's session and is gone at the next turn. The mailbox is stored in the database through the chat store's engine (`PostgresChatStore._engine`); a session whose chat store has no engine (the tests' recording store) uses `InMemoryMailbox`.
  14. `TeammateIdle` is emitted on the lifecycle bus and its hook answers are not read. The lead receives the teammate's last report (up to 2,000 characters) as a `teammate` reminder.
  15. A message to a busy teammate waits until its current run ends. A teammate reads up to `MAX_TEAM_DELIVERIES = 10` batches in one run, then goes idle. `ask_agents` is unchanged.
- **Other differences from the plan:**
  a. Task 12, step 3, edit 2: the old text `_PERMISSIVENESS = {` is a prefix of a one-line dict in `agent_definitions.py`. The new constant is inserted before that line and the dict is untouched.
  b. Task 14, step 2: the expected collection error did not happen (see Tests above); the red state is still the missing feature.
  c. Task 14, step 4: the command as written, `uv run pytest -q tests/unit/agentic tests/unit/agentic/tools tests/unit/permissions`, collected 745 tests where the three directories hold 1619. pytest drops tests when a directory and one of its subdirectories are both arguments (checked with `--co`). The run used `tests/unit/agentic tests/unit/permissions` instead: 1611 passed, 8 skipped, 1619 collected.
  d. Commit trailer: every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the plan and the brief require. The session's attribution note names Haiku 5.5 for commits; the owner should decide, as the Batch B ledger already says.
  e. `agent_tools.py` is 1698 lines after Task 14; the guard in `test_split.py` requires fewer than 1700. Two lines of room remain.
  f. Git prints "LF will be replaced by CRLF" for the edited and new files. Their content is LF in the working copy and in HEAD, so the warning comes from the environment's `core.autocrlf` setting. No content changed.
  g. A scratch copy of the contract test (used to count its cases) was written to `C:\Users\carlos\AppData\Local\Temp\old_contract.py`, outside the repository. It was not deleted, because of the no-deletion rule.

### Expectation changes (the only existing tests changed)

- Task 13: `tests/unit/persistence/test_postgres_schema.py` lists `agent_mailbox`; `tests/unit/persistence/test_w3_schema.py` expects the head revision `0050_w5_extensibility` (both assertions).
- Task 14: `tests/unit/agentic/tools/test_tool_ledger.py` counts 32 included, 5 deferred, 12 excluded. `tests/unit/agentic/tools/test_public_contract.py`: the Agent tool lists `name` and `team_name`, and a `send_message` entry (fields `to`, `message`, `summary`; required `to` and `message`).

No other existing test was changed, weakened or skipped.

### Closed by this batch

- Batch B observation **Preloaded skills**: the wrapper part is closed by Task 12. The body sits inside the subordinate wrapper, and a closing `</agentos-skill-instructions>` inside it is neutralised. The rest of that observation is still open: the skill's `allowed-tools` are not granted to the subagent, and its `!`cmd`` blocks are not run.
- Batch B observation **Replacing a builtin**: a repository can no longer replace `explore`, `plan`, `general-purpose`, `general`, `fork` or `verifier`; such a definition is reported and skipped. A person's own or a managed definition may still replace them, as the plan says.

### Observations not fixed (for the owner)

- **Messages lost when a start is refused.** `_send_team_message` takes the pending messages (`_take_team_mail`) before `_ask_agent` runs. If that start is refused (the turn's subagent budget is used up, the model is not allowed, the teammate is still running), the messages are already marked delivered and nothing reads them. The lead does receive the refusal. Possible fix: take the messages only after a successful start, or post them back on failure.
- **Idle with mail still pending.** After `MAX_TEAM_DELIVERIES` batches a teammate goes idle. Messages posted after that point stay pending; the lead is not told, and they are read only when the lead next sends to that teammate.
- **Cancelled teammates.** `_run_background_agent` emits `TeammateIdle` and sends the last report whatever the outcome, and it keeps taking mail after a cancel. I did not test what the re-runs do after a cancel.
- **A teammate name is turn-wide.** Naming an existing teammate with a different `team_name` silently moves it to the new team, because the roster entry is overwritten.

## Batch D — MCP servers in configuration files (Tasks 16–19)

### Commits

- `80edda2` fix(w5): team messages survive a refused start, an idle race and a stop; a teammate keeps its team (Task 16)
- `3073c81` feat(w5): read MCP servers from the managed, user, local and project files (Task 17)
- `93fa1f0` feat(w5): MCP servers from configuration files join the turn (Task 18)
- the commit that adds this section: docs(w5): ledger, batch D (Task 19)

### Tests added (25 cases)

- Task 16: `tests/unit/conversations/test_mailbox.py` +2 (one test, run against the database and in memory) and `tests/unit/agentic/test_agent_teams.py` +5 (a refused start keeps the message; an idle race starts the teammate again; the delivery cap reports unread messages; a teammate keeps its team; a stopped teammate leaves its mail unread). Before the implementation, all seven failed at run time (`KeyError: 'message_id'` for the mailbox; the five team tests also failed at run time; their messages were not inspected one by one).
- Task 17: `tests/unit/mcp/test_file_config.py` +10 (new file). Before the implementation: collection error, `ModuleNotFoundError: agentos.mcp.file_config`.
- Task 18: `tests/unit/mcp/test_file_mcp_servers.py` +8 (new file). Before the implementation: collection error, `ModuleNotFoundError: agentos.mcp.file_servers`.

### Suite results

- Task 16: `tests/unit/agentic/test_agent_teams.py` with `tests/unit/conversations/test_mailbox.py`: 22 passed. `tests/unit/agentic`: 1251 passed, 7 skipped.
- Task 17: `tests/unit/mcp/test_file_config.py`: 10 passed. `tests/unit/settings`: 53 passed, 3 skipped.
- Task 18: `tests/unit/mcp/test_file_mcp_servers.py`: 8 passed. `tests/unit/mcp tests/unit/agentic`: 1398 passed, 7 skipped.
- Task 19, full backend, foreground: `uv run pytest -q tests/unit` (timeout 600000 ms): **3778 passed, 60 skipped, 0 failed** (309 s). Batch C ended at 3753 passed and 60 skipped; the difference is the 25 new cases.
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (70 s). The skip count is the same as in Batches B and C.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. Batch D touches no file under `frontend/`, so lint and build were not run.

### Deviations from the plan

- **Plan deviations 16–22, as implemented:**
  16. Not changed in this batch: a preloaded skill's `allowed-tools` and `` !`cmd` `` blocks stay out of a subagent. Its text stays inside the subordinate wrapper from Batch C's Task 12.
  17. `load_file_servers` reads `~/.orin/mcp.json`, `~/.orin/workspaces/<key>/mcp.json` (with its own enabled and disabled lists) and `.orin/mcp.json`. Orin's entry replaces Claude Code's entry of the same name within a scope.
  18. `_load_file_mcp_servers` skips a file server whose slug an Orin server already uses, and tells the model. `policy_refusal` is called for Orin's own servers too, with the slug as an alias.
  19. `personal` is `mcp_service.allow_stdio`, which production sets from the `mcp_stdio` capability. A session without a service, and a shared Orin, read only the managed file.
  20. `expand` raises for `ORIN_*` and `AGENTOS_*` names, and that stops only the server that uses them.
  21. No approval prompt. Pending project servers are listed in the `mcp` reminder with the settings keys that approve them.
  22. `file_servers.py` lists tools in parallel within `MCP_TIMEOUT` and caches each configuration's answer for 600 s (60 s for a failure). File servers cannot sign in with OAuth, and an `sse` or `ws` file server is reported as not supported yet.
- **Other differences from the plan:**
  a. The commit trailer. The brief asked for `Co-Authored-By: Claude Opus 5.5`. The session's attribution note asks for `Claude Haiku 5.5`, and Batch D's commits use the Haiku line. Batch C used Opus. The owner should decide which one stays. The three commits are local and have not been pushed.
  b. Task 16, step 3, edit 13: the plan's old text ends in the partial line `return ToolOutcome`. The edit matched it as a prefix, so the rest of that line is unchanged. The result is the plan's intended code.
  c. Task 17's `tests/conftest.py` edit adds two lines (the plan's comment and the `delenv` call), not one.
  d. The appended test blocks keep the plan's single blank line before the new code. No Python linter was run on Batch D's code.

### Expectation changes

None. The diff of `tests/` since `0f59a6f` removes no line. It only adds tests and two lines in `tests/conftest.py`.

### Closed by this batch

- Batch C observation **Messages lost when a start is refused**: closed by Task 16, item 1 (`release`).
- **Idle with mail still pending**: closed by Task 16, item 3 (the lead's idle reminder states the unread count).
- **Cancelled teammates**: closed by Task 16, item 5 (a stopped teammate takes no more mail). A test covers it.
- **A teammate name is turn-wide**: closed by Task 16, item 4 (a teammate keeps its team, and naming another team is refused).

### Observations not fixed (for the owner)

Found by reading the code; no test reproduces them.

- **A teammate can stay busy for the rest of the turn.** `_send_team_message` claims the teammate before `_take_team_mail` and `_ask_agent`, and `_run_subagent` claims it before `_start_subagent`. If one of those raises (for example a database error), nothing releases the claim, so later messages to that teammate return `SUBAGENT_STILL_RUNNING` until the turn ends. The background run releases on an exception; the sender side does not. Suggested fix: wrap the section from the claim to the start in `try` / `except BaseException:` with `self._release_teammate(name)`, then re-raise.
- **Failure texts mask only values of 4 or more characters, and never the URL path.** `_redacted` replaces environment and header values of at least four characters, and the query string. A shorter header value, or a token in the path, could reach the model in a failure reason. Suggested fix: mask every non-empty value and the path too.
- **Approval is by settings keys only** (deviation 21). Approved file servers may run any command line (`allow_any_command` for file servers, as the plan specifies), so the approval step is the only guard for project servers. The approval prompt arrives with W7.

## Batch E — The MCP protocol (Tasks 20–26)

### Commits

- `31fd601` feat(w5): the MCP client answers the server's requests and notes its notifications (Task 20)
- `210f865` feat(w5): legacy SSE and WebSocket transports for MCP servers in configuration files (Task 21)
- `63a6f8b` feat(w5): stdio MCP servers stop gracefully; MAX_MCP_OUTPUT_TOKENS sets the output limit (Task 22)
- `0fbc3e5` feat(w5): MCP resources and RefreshMcpTools (Task 23)
- `7d2b345` feat(w5): MCP prompts run as /mcp__server__prompt commands (Task 24)
- `84ad5a6` feat(w5): MCP elicitation is declined unless an Elicitation hook answers it (Task 25)
- the commit that adds this section: docs(w5): ledger, batch E (Task 26)

### Tests added (33 cases: 29 test functions, and 4 parametrized cases from the public contract)

- Task 20: `tests/unit/mcp/test_server_messages.py` (new), 6 tests. Before the implementation: collection error, `ImportError: cannot import name 'METHOD_NOT_FOUND'`.
- Task 21: `tests/unit/mcp/test_sse_ws_transports.py` (new), 6 tests. Before the implementation: collection error, `ModuleNotFoundError: agentos.mcp.transport_sse`.
- Task 22: `tests/unit/mcp/test_transport_stdio.py` +2 and `tests/unit/mcp/test_file_mcp_servers.py` +1. Before the implementation: those three failed at run time, as the plan predicted.
- Task 23: `tests/unit/mcp/test_mcp_resources.py` (new), 4 tests. Before the implementation: 3 failed and `test_the_resource_tools_need_a_connected_server` passed, as the plan predicted. `tests/unit/agentic/tools/test_public_contract.py` gains four entries, which run as four parametrized cases.
- Task 24: `tests/unit/mcp/test_mcp_prompts.py` (new), 4 tests. Before the implementation: all four failed (`AttributeError: 'McpToolProvider' object has no attribute 'get_prompt'`, and no command reminder).
- Task 25: `tests/unit/mcp/test_mcp_elicitation.py` (new), 6 tests. Before the implementation: collection error, `ModuleNotFoundError: agentos.mcp.elicitation`.

### Suite results

- Task 20: `tests/unit/mcp/test_server_messages.py`: 6 passed. `tests/unit/mcp`: 153 passed.
- Task 21: `tests/unit/mcp/test_sse_ws_transports.py`: 6 passed. `tests/unit/mcp`: 159 passed.
- Task 22: `tests/unit/mcp/test_transport_stdio.py` and `tests/unit/mcp/test_file_mcp_servers.py`: 21 passed. `tests/unit/mcp`: 162 passed.
- Task 23: `tests/unit/mcp/test_mcp_resources.py`: 4 passed. `tests/unit/agentic/tools tests/unit/permissions`: 743 passed, 6 skipped. `tests/unit/mcp`: 166 passed.
- Task 24: `tests/unit/mcp/test_mcp_prompts.py`: 4 passed. `tests/unit/agentic/test_disk_skills_session.py`: 5 passed. `tests/unit/mcp`: 170 passed.
- Task 25: `tests/unit/mcp/test_mcp_elicitation.py`: 6 passed. `tests/unit/hooks tests/unit/mcp`: 267 passed.
- Task 26, full backend, foreground: `uv run pytest -q tests/unit` (timeout 600000 ms): **3811 passed, 60 skipped, 0 failed** (317 s). Batch D ended at 3778 passed; the difference is the 33 new cases.
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (68 s). All 65 skips need `AGENTOS_TEST_POSTGRES_DSN`, `AGENTOS_POSTGRES_URL` or `AGENTOS_REDIS_URL`, which are not configured on this machine. The same count appeared in Batch D.
- In the areas this batch touched (`tests/unit/mcp`, `tests/unit/agentic/tools`, `tests/unit/permissions`, `tests/unit/hooks`), the skips are environmental: no ripgrep on this machine, and POSIX-only names. The other unit-suite skips were not audited one by one.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. Batch E touches no file under `frontend/`, so lint and build were not run.
- `tests/eval` was not run: it holds the manual model-evaluation matrix (`software_quality_cases.json`), not a pytest suite.

### Deviations from the plan

- **Plan deviations 23–28, as implemented:**
  23. Elicitation is declined unless an `Elicitation` hook answers it, and the model hears of the request through an `mcp` reminder. Sampling requests are refused. `roots/list` returns an empty list, because `build_client` passes no roots.
  24. `ReadMcpResourceDir` lists the resources whose address starts with the given uri. Images come back as images; other binary content is described by its size and type, not saved.
  25. `RefreshMcpTools` replaces a configuration-file server's tools. Orin's own servers keep the approved tools, and the report says `applied: false` with the note. A `list_changed` notice during a call is followed after that call (`_follow_changes`), not during `initialize` or a list call.
  26. No warning at 10,000 tokens. The cut at `MAX_MCP_OUTPUT_TOKENS` applies: default 25,000 tokens, 4 characters each, clamped to 1,000–250,000 tokens.
  27. MCP prompts do not appear in a `/` menu. `/mcp__server__prompt args` is handled in `_apply_user_command` through `_apply_mcp_prompt`, and the arguments are split with `shlex` (see observation 2 below).
  28. SSE and WebSocket serve configuration-file servers only; `McpServerService.propose` refuses them (tested). A stdio server gets `GRACEFUL_EXIT_SECONDS` = 0.3 s to exit after its input closes before its process tree is killed. No Windows Job Object.
- **Other differences from the plan:**
  a. Task 20, edit 14: the plan's old block for `_first_sse_payload` also covered the blank lines after it. The function was replaced and the file's existing blank lines before `__all__` were kept. No code effect.
  b. The commit trailer is `Co-Authored-By: Claude Opus 5.5`, as the owner asked for this branch. The session's attribution note asked for Haiku 5.5, and Batch D's commits use the Haiku line. The owner should decide which one stays, as in Batch D's note (a).
  c. Git printed "LF will be replaced by CRLF" warnings on some commits. The working copies are CRLF and git normalises them; no content differs from the plan.
  d. Nothing was deleted and nothing was pushed.

### Expectation changes

- `tests/unit/agentic/tools/test_tool_ledger.py`: `{"included": 32, "deferred": 5, "excluded": 12}` became `{"included": 36, "deferred": 1, "excluded": 12}`.
- `tests/unit/agentic/tools/test_public_contract.py`: four entries added (`list_mcp_resources`, `read_mcp_resource`, `read_mcp_resource_dir`, `refresh_mcp_tools`).
- Nothing else in `tests/` was removed or changed: `git diff a9cccce HEAD -- tests` removes only the ledger count line.

### Closed by this batch

- None. The Batch D observations are not about the MCP protocol, so they still stand.

### Observations not fixed (for the owner)

Found by reading the code while implementing the batch. Where a check was run, it is noted.

- **A NaN or infinite number in an elicitation answer is accepted.** `checked_answer` takes any `float`. Checked: `checked_answer({"action": "accept", "content": {"n": float("nan")}})` returns the NaN unchanged, and `json.dumps` writes it as `NaN`, which is not valid JSON, so the server may reject the reply. Suggested fix: decline when `math.isfinite(value)` is false.
- **Backslashes in `/mcp__…` arguments are lost.** `_apply_mcp_prompt` splits with `shlex.split(arguments, posix=True)`, which treats `\` as an escape. Checked: `C:\work\pr 7` becomes `['C:workpr', '7']`, so a Windows path argument is corrupted on this platform. Suggested fix: split with a regex that honours only quotes, for example `re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', arguments)`, taking the first non-empty group of each match.
- **A `/mcp__…` message is silently ordinary text when no MCP provider exists.** With a provider but an unknown server, the message is reported ("could not run"); with no provider at all, `_apply_mcp_prompt` returns without a reminder. Suggested fix: add the "could not run: no MCP server is connected" reminder in that branch.
- **SSE and WebSocket check the address only when the transport is built.** The HTTP transport checks again before every request. `SseTransport` checks the endpoint of each POST but not the stream it opens in `open()`, and `WebSocketTransport.open()` does not check at all. The window is short (build, then initialize). Not tested. Suggested fix: run `checked_url` again at the start of each `open()`.
- **A graceful stdio close does not kill what the server started.** When the server exits within 0.3 s, `close()` returns without `_terminate_process_tree`, so a child process that outlives its launcher is not killed. This follows the plan (a server that exits on its own is never killed). Not tested.

## Batch F — Plugins (Tasks 27–32)

### Commits

- `46c65a2` fix(w5): a teammate is released when its start raises; file-server failures mask short values and URL paths (Task 27)
- `8332c04` feat(w5): plugin packages as Claude Code and Orin lay them out (Task 28)
- `b14ebfe` feat(w5): plugin agents and output styles join the turn; enabledPlugins can turn a plugin off (Task 29)
- `5f8976d` feat(w5): plugin options (userConfig), encrypted per person (Task 30)
- `66a0f74` feat(w5): plugin marketplaces are fetched and read (Task 31)
- the commit that adds this section: docs(w5): ledger, batch F (Task 32)

### Tests added (25 test functions, all passing)

- Task 27: `tests/unit/agentic/test_agent_teams.py` +1 and `tests/unit/mcp/test_file_mcp_servers.py` +2. Red before the fix: the teammate stayed busy, the masking test showed `abc` and the path segment, and the malformed-URL test (added by the executor, see deviations) raised `ValueError: Invalid IPv6 URL`.
- Task 28: `tests/unit/plugins/test_plugin_packages.py` (new), 7 tests (6 from the plan, 1 added). Red: collection error (`manifest_file` missing). The added alias-bomb test was red with the plan's code: the alias expanded into text and the model rejected it with `ValueError` after about 1.6 s.
- Task 29: `tests/unit/plugins/test_plugin_runtime.py` (new), 4 tests. Red: collection error (`agentos.plugins.runtime` missing).
- Task 30: `tests/unit/plugins/test_plugin_user_config.py` (new), 6 tests (5 from the plan, 1 added). Red: collection error (`plugin_settings` import). The added "too large" test was written together with its guard and was not seen failing. The guard's reason was checked directly: the cipher refuses a plaintext over 4096 characters with `ValueError`, which the guard turns into `PluginConfigError`.
- Task 31: `tests/unit/plugins/test_marketplaces.py` (new), 5 tests (4 from the plan, 1 added). Red: collection error (`marketplace_file` missing). The added unusable-name test was red with `ValueError` from `plugin_id_from_name`.

### Suite results

- `uv run pytest -q tests/unit` (foreground, timeout 600000 ms): **3836 passed, 60 skipped, 0 failed** (284 s). Batch E ended at 3811 passed; the difference is the 25 new test functions above.
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (63 s). Same counts as Batch E. The 65 skips need `AGENTOS_TEST_POSTGRES_DSN`, `AGENTOS_POSTGRES_URL` or `AGENTOS_REDIS_URL`, which are not configured on this machine.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. `npm --prefix frontend run lint` and `npm --prefix frontend run build`: exit 0. Batch F adds no file under `frontend/`, so these are a check, not a test of new UI.

### Deviations from the plan

- **Plan deviations 29–34, as implemented:**
  29. A plugin skill is read leniently (YAML frontmatter, only the fields Orin's library stores). Its `allowed-tools`, `argument-hint` and other Claude Code keys are dropped and not applied.
  30. A plugin's agents are named `plugin:agent` and lose `hooks`, `mcpServers` and `permissionMode`. Its output styles are named `plugin:style`. Its `settings.json` and `.lsp.json` are not read.
  31. `enabledPlugins: false` turns an installed plugin's agents and styles off for the turn. `true` for a plugin that is not installed is reported in a `plugins` reminder and not fetched.
  32. Option values are encrypted together with the provider-secret cipher. A sensitive value is never returned, only `is_set`. A missing required option is refused when a plugin is turned on, and a sensitive option is refused in a command line or URL. Changed values reach the MCP servers when the plugin is turned off and on again. Options are set through `GET`/`PUT /v1/plugins/{id}/config`. Hooks do not receive options until Batch G.
  33. A marketplace is a GitHub repository or a local folder. `extraKnownMarketplaces` is not read. A shared Orin (remote-only) refuses a local folder, so a relative plugin of a marketplace is refused there.
  34. Only the first `commands` path of a manifest is read, and an inline `hooks` object is ignored.
- **Deviations found by the executor (not in the plan):**
  a. Task 27: the plan's `_redacted` calls `urlsplit`, which raises `ValueError` on a malformed URL (for example `http://[bad/x`). Called inside the failure handler, that made `connect_file_servers` raise for the whole turn. Fixed: a malformed URL is masked as a whole. Test: `test_a_malformed_url_is_reported_and_masked_whole`.
  b. Task 28: the lenient frontmatter reader turns only plain scalars (text, numbers, booleans, dates) into fields, so a list or mapping is dropped. Reason: a YAML alias can share a very large structure, and `str()` expands it. A 10^6-leaf alias bomb took seconds to expand before the model rejected it. A `name` or `description` given as a list now counts as missing (fail closed). Test: `test_a_frontmatter_alias_bomb_is_refused_not_expanded_into_text`.
  c. Task 28: the registry compares `skill.source == SkillSource.PLUGIN` instead of `is`. The enum is a `StrEnum`, so `==` also matches a plain `"plugin"` string.
  d. Task 30: `PluginConfigStore.update` refuses a plaintext over 4096 characters (`MAX_STORED_CHARS`) with `PluginConfigError`. Without it, 32 options of up to 4000 characters would have reached the cipher and raised a bare `ValueError` (a 500). Test: `test_options_too_large_to_encrypt_are_refused_with_a_clear_error`.
  e. Task 31: `add_marketplace` turns a marketplace name with no letters or digits (for example `!!!`) into `PluginServiceError` (`marketplace_invalid`). Before, `plugin_id_from_name` raised a bare `ValueError`. Test: `test_a_marketplace_whose_name_gives_no_identifier_is_refused`.
  f. The commit trailer is `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the owner requires for this branch. The session's attribution reminder asked for Haiku 5.5; the owner's instruction was followed.
  g. Git printed "LF will be replaced by CRLF" warnings on the commits. Content is unchanged.
  h. Nothing was deleted and nothing was pushed.

### Expectation changes

- `tests/unit/persistence/test_postgres_schema.py`: `"plugin_settings"` added to the expected table set (+1 line).
- `tests/unit/persistence/test_w3_schema.py`: both `.scalar() == "0050_w5_extensibility"` assertions became `"0051_w5_plugin_settings"` (two lines removed, two added).
- `tests/integration/api/test_profile_isolation.py`: `GET` and `PUT /v1/plugins/{plugin_id}/config` added to the owned routes (+1 line).
- `git diff 1b67f75 HEAD -- tests` removes only the two `0050` lines. Nothing else in `tests/` was removed or changed.

### Not verified, or left for later (for the owner)

- **Postgres:** migration `0051_w5_plugin_settings` and the `plugin_settings` table were verified on SQLite only. The integration suite skips every Postgres test here. Run the integration suite with `AGENTOS_TEST_POSTGRES_DSN` set before relying on the migration.
- **Config routes:** `test_the_config_routes` uses a fake plugin service. The service behind them was tested on SQLite with the real cipher. The auth path (`principal_for`, rate limit, authorize) was not exercised with a real security object.
- **Windows hook paths:** a hook command substitutes `${CLAUDE_PLUGIN_ROOT}` unquoted, then splits it with `shlex`. A plugin installed under a folder with a space in its path splits into two arguments. This existed before the batch; Task 28 only changed the separators. Not fixed.
- **`.` in a manifest path:** `"agents": "."` maps to the default `agents` folder, not the package root. Not fixed.
- **Marketplace references:** `search` returns the server-side absolute path of a relative plugin as its `reference`, so a person sees the server's path. A bare relative source without `./` (`plugins/fmt`) is read as GitHub `owner/repo`. Not fixed; the plan specifies the `./` form.
- **Frontmatter size:** the lenient reader does not cap the size of the frontmatter before YAML parsing. The disk loader from Batch A has the same property; not changed here.
- **Batch G:** plugin hooks, and options for hooks, are not in this batch.

## Batch G — One hook system (Tasks 33–36)

### Commits

- `8446e98` fix(w5): MCP hardening — finite elicitation numbers, Windows paths in prompt arguments, address re-checks (Task 33)
- `722fe60` feat(w5): plugin hooks run in the settings hooks' dispatcher (Task 34)
- `79c4fd7` feat(w5): a skill's or agent's own hooks run for the agent that uses it (Task 35)
- `6aaba17` fix(w5): managed disableAllHooks also switches off a managed skill's or agent's hooks (executor deviation 39, separate commit)
- the commit that adds this section: docs(w5): ledger, batch G (Task 36)

### Tests added (15 test functions, all passing)

- Task 33: 4 functions, one in each of the three planned files (elicitation: 1, prompts: 2, transports: 1). Red before the fix: all four failed, as the plan predicted. Green after: `tests/unit/mcp` 182 passed.
- Task 34: `tests/unit/plugins/test_plugin_hooks_unified.py` (new), 6 tests. Red: collection error. The import that fails first is `non_managed_hooks_allowed` (the test file imports it before `hook_specs`), not the `ModuleNotFoundError` for `hook_specs` that the plan quoted; both are collection errors. Green after.
- Task 35: `tests/unit/hooks/test_scoped_hooks.py` (new), 4 tests from the plan, and 1 added by the executor (deviation 39). Red: collection error (`parse_hook_mapping`) for the four plan tests. The added test was red with the decision `deny` still returned for a managed skill's hook. Green after the fix.

### Suite results

- `uv run pytest -q tests/unit` (foreground, timeout 600000 ms): **3851 passed, 60 skipped, 0 failed** (283 s). Batch F ended at 3836; the difference is the 15 new test functions above.
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (63 s). The 65 skips are the Postgres-optional tests: `AGENTOS_TEST_POSTGRES_DSN`, `AGENTOS_POSTGRES_URL` and `AGENTOS_REDIS_URL` are not set on this machine.
- `npm --prefix frontend test`: **103 files, 609 tests passed**. No file under `frontend/` changed in this batch, so lint and build were not run.
- The 60 unit skips, from the 29 files that carry skip markers: 43 "the fake runtimes are POSIX shell scripts", 5 "no ripgrep on this machine", and the rest are symlink and POSIX file-mode checks. Shell-script hook tests therefore did not run on this Windows machine; the Batch G tests use the running Python interpreter instead.

### Deviations from the plan

- **Plan deviations 35–38, as implemented:**
  35. A plugin hook runs only if its id is in the person's consent (`ActivePlugin.enabled_hook_ids`, read from the contribution rows). It runs `command` handlers only, through `resolve_argv` with no shell, in the project folder, with the allowlisted environment, `plugin_variables` and the option values. A hook naming a sensitive option in its command is skipped with an error.
  36. The worker no longer calls `rehydrate_hooks`, and the turn-local `HookEngine` stays empty. `plugins/hook_engine.py` and `plugins/rehydrate.py` remain in the code and their tests still pass; nothing was deleted.
  37. A skill's hooks are registered at its first use, once per (skill, agent), and removed with the settings hooks at turn start or on a refused configuration change. An agent's hooks are removed in the `finally` of `_execute_subagent_run`, after its `SubagentStop`, and an agent's `Stop` becomes `SubagentStop` for it. The worker calls `build_runtime` once per turn (`workers/chat.py`), so the removal does not run mid-turn.
  38. Recorded in the plan. The stdio exit behaviour is Batch E code and was not changed here.
- **Deviations found by the executor (not in the plan):**
  a. Task 34, plan items 4 and 22: the stub line and the import line are removed together with their line endings, not replaced by an empty line. No behaviour change.
  b. Task 34, Step 2: the first missing name reported is `non_managed_hooks_allowed` (see the test note above).
  c. Task 35, deviation 39: `_scoped_hooks` skipped the managed-settings check for managed skills and agents (origin `managed`), so a managed `disableAllHooks` did not switch their hooks off, contrary to deviation 37. Fixed: a `managed_hooks_allowed` helper in `hooks/config.py` is used for managed origins; user and CLI switches still never reach managed hooks, as for the settings hooks. Test: `test_managed_disable_all_hooks_switches_off_a_managed_skills_hooks`. Committed separately as `6aaba17`.
  d. Task 33: while appending the prompt test, the backslashes were typed single where the plan has double. Caught by a `SyntaxWarning` and by a check of the file against the plan; corrected to the plan's text before the tests ran.
  e. Every "replace … with" block of Tasks 33–35 was checked against the plan text with a script. It caught one mistyped line (the `MIN_TIMEOUT, DEFAULT_TIMEOUT, MAX_TIMEOUT` line in `hooks_manifest.py`), which was corrected before the tests ran.
  f. The commit trailer is `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the owner requires for this branch.
  g. Git printed "LF will be replaced by CRLF" warnings. The files edited with the Edit tool keep the line endings they had: the CRLF files stayed entirely CRLF and the LF files stayed LF.
  h. Nothing was deleted, nothing was pushed, and the branch was not switched.

### Expectation changes (the only existing tests changed)

- `tests/unit/plugins/test_hooks_manifest.py`, three cases: (1) `test_the_reference_hooks_file_yields_three_hooks` now asserts `hooks[0].run_async is True and warnings == ()` instead of the "async" warning; (2) `test_an_unsupported_event_is_warned_not_dropped_silently` is renamed `test_an_unknown_event_is_warned_not_dropped_silently` and uses `NotAnEvent`, because `PreToolUse` is now a supported event; (3) `test_timeout_is_clamped` expects 600 instead of 30.
- `tests/unit/workers/test_multi_session_concurrency.py`: one stub line removed (`monkeypatch.setattr(chat_module, "rehydrate_hooks", ...)`), because `workers/chat.py` no longer imports `rehydrate_hooks` and `monkeypatch.setattr` would fail on the missing attribute.

### Not verified, or left for later (for the owner)

- **Plugin hook consent is tied to the hook id, not to its content (decision needed).** Hook ids are `{plugin}:{Event}:{index}`, and `plugin_hook_specs` reads each command from the installed package at every turn. The recorded `package_digest` is stored but never compared with the files (`inspector.py`), so nothing notices a change on disk. Two consequences:
  (a) If a package changes the command of an id the person already consented to, or inserts a hook earlier in the same event (which shifts the indexes), the new command runs under the old consent.
  (b) `set_enabled(enabled=True)` in `plugins/service.py` (around lines 279–287) gives every hook row of the re-activated package the previous "all hooks were enabled" flag. A hook a later version adds is therefore enabled without a new consent, which contradicts deviation 35. This is Batch F code.
  Not fixed here: the full fix needs a schema change. Suggested fix: store a digest of event, matcher and command with each hook consent row (a new column and a migration), run a hook only when its current digest matches the consented one, return any other hook as "waits for consent", and carry consent into a re-activation only per (id, digest).
- **Windows hook paths and symlinks:** not tested. Whether a symlink inside a plugin folder can point outside it was not checked.
- **Postgres:** Batch G adds no migration, but the 65 integration skips were not run here.
- **POSIX:** the 43 shell-script hook tests were skipped on this machine, so the POSIX runner path is covered only by the test code, not by a run.
- **Frontend:** only `npm --prefix frontend test` was run; no frontend file changed.

## Batch H — Diagnostics after edits (Tasks 37–38)

### Commits

Branch `feat/orin-code-parity-w0-w3`, on top of `909b430` (the Batch H plan):

| Task | Commit | Subject |
|---|---|---|
| 37 | `aa0b7f3` | feat(w5): a plugin's language server reports diagnostics after Write and Edit (includes the reader fix, deviation 42) |
| 38 | this commit | docs(w5): ledger, batch H and W5 summary |

### Tests added (6 test functions, all in `tests/unit/lsp/test_lsp.py`)

- The five functions of the plan: `test_the_client_sends_a_change_and_returns_its_diagnostics`, `test_diagnostics_become_reminder_text`, `test_a_server_that_cannot_start_is_turned_off`, `test_a_plugins_lsp_json_is_read_once_its_code_may_run`, `test_writing_a_file_brings_its_diagnostics_to_the_model`.
- One added by the executor, for deviation 42: `test_a_malformed_message_does_not_stop_the_reader`.

Red states: Task 37 Step 2 gave the predicted collection error, `ModuleNotFoundError: No module named 'agentos.lsp'`. The added test failed before its fix: the reader thread ended with `TypeError: 'int' object is not iterable`, and `diagnostics` returned `None` where the good message's diagnostics were expected.

### Suite results

- Task 37, `uv run pytest -q tests/unit/lsp`: **6 passed**.
- Task 37 Step 4: `uv run pytest -q tests/unit/agentic`: 1256 passed, 7 skipped. `uv run pytest -q tests/unit/agentic/tools`: 378 passed, 5 skipped. Run separately, as the plan says.
- Task 38 Step 1, `uv run pytest -q tests/unit` (foreground, timeout 600000 ms): **3857 passed, 60 skipped, 0 failed** (288 s). Batch G ended at 3851; the difference is the six new test functions.
- `uv run pytest -q tests/integration`: **78 passed, 65 skipped, 0 failed** (63 s). The 65 skips are the same Postgres and Redis tests as in every batch (`AGENTOS_TEST_POSTGRES_DSN`, `AGENTOS_POSTGRES_URL`, `AGENTOS_REDIS_URL` not set here).
- Task 38 Step 2: `npm --prefix frontend test`: **103 files, 609 tests passed**. `npm --prefix frontend run lint` (`eslint . --max-warnings=0`): exit 0. `npm --prefix frontend run build`: exit 0, with the bundler's advisory that some chunks exceed 500 kB. The advisory is a warning, not an error. Batch H changes no file under `frontend/`.
- Python linting: `pyproject.toml` configures no linter and ruff is not in the virtual environment, so no Python linter was run on Batch H's code (the same as Batch D).

### Deviations from the plan

- **Plan deviations 40–41, as implemented:**
  40. A plugin's language server runs only once the person allowed the plugin to run code (`ActivePlugin.enabled_hook_ids`) and only on an Orin that runs plugin hooks (`_lsp_manager` returns None otherwise). Its environment is the worker's without `ORIN_*` and `AGENTOS_*` variables or names containing `API_KEY`, `SECRET`, `TOKEN` or `PASSWORD`, plus its `env` (`_environment` in `lsp/client.py`).
  41. Diagnostics are gathered in the background after Write and Edit and join the next request as a `diagnostics` reminder (errors and warnings, at most 30 per file). A server that fails is turned off for the turn and the model is told once. Orin has no `LSP` code-navigation tool. `restartOnCrash`, `maxRestarts`, `transport: socket` and `workspaceFolder` are not read.
  This batch also reads the plugin's `.lsp.json` (or the manifest's `lspServers`), so Batch F's deviation 30 is updated: `.lsp.json` is now read. `settings.json` still is not.
- **Deviation 42 (found by the executor; not in the plan).** The plan's `LspClient._read_loop` called `_dispatch` without a guard. One malformed server message (for example a `publishDiagnostics` whose `diagnostics` is a number, or an `id` or `method` that is not hashable) raised `TypeError` inside the reader thread, which then ended. Every later reply was never read, so each later call waited its full timeout and returned nothing for that file, with no reminder. A probe with a malformed publish followed by a valid one returned `None` after the timeout, and the thread's traceback was printed. Fix: each message is dispatched inside `try` / `except Exception` with a debug log, so one malformed message is skipped (`lsp/client.py`; `logger` added). Test: `test_a_malformed_message_does_not_stop_the_reader`, red before the fix and green after. The fix is inside commit `aa0b7f3` (made before that commit), not a separate commit. The probe script is outside the repository, in the scratchpad (`malformed_probe.py`), and was not deleted.
- **Other differences from the plan:**
  a. Task 37 Step 3 edits 1 to 4 matched their old text exactly once each and were applied as written. Step 2's red state and Step 4's runs are as the plan describes.
  b. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, as the brief and the owner's standing instruction for this branch require. The session's attribution note named Haiku 5.5. This is the same choice as Batches A to C and E to G; Batch D's commits used the Haiku line, as its ledger records.
  c. Git printed "LF will be replaced by CRLF" for the new and edited files. The content is unchanged; the warning comes from the environment's line-ending setting.
  d. Nothing was deleted, nothing was pushed, and the branch was not switched.

### Expectation changes

None. Batch H changes no existing test; the only test file it adds or edits is `tests/unit/lsp/test_lsp.py`, which is new.

### Observations not fixed (found while implementing Batch H; found by reading unless noted)

1. A malformed `publishDiagnostics` (for example `diagnostics` that is not a list) is now skipped by the reader guard, so the call waits the full timeout (5 s on each Write or Edit) and reports nothing for that file, with no reminder and no notice. Reproduced with the probe (`None` after the 3 s timeout).
2. A malformed item inside an otherwise valid list (for example a `range` that is not an object) makes `format_diagnostics` raise in the background worker. The executor keeps the exception and nothing logs it, so that file's diagnostics silently do not reach the model. Not reproduced.
3. A late answer can be taken for the current one. A publish that arrives after its call timed out is stored; the next call for the same file sees the event set and may return the previous text's diagnostics. Not reproduced.
4. The server's stderr is discarded (`stderr=DEVNULL`, as in the plan's code). A server that fails to start or crashes gives the model only "could not start …", "did not answer …" or "the language server stopped", never its own message.
5. Diagnostics run only after Write and Edit, as the plan specifies. A file changed by a shell command is not checked.

### Closed by this batch

None of the earlier observations. Batch H adds no fix to an earlier batch's observation.

## W5 summary

Branch `feat/orin-code-parity-w0-w3`. Plan `docs/superpowers/plans/2026-10-19-w5-extensibility.md`. Batches A to H are done. Batch I was folded into Task 38, and its tool-ledger part happened in Batch E (the four MCP resource and refresh tools).

### Batches and commits

- **A, skills and slash commands on disk (Tasks 1–5):** plan `b7b2235`; `9264570`, `ccefc6d`, `f290581`, `511e11a`; ledger `fb48436`.
- **B, agents in files (Tasks 6–11):** plan `2930b85`; `6de7d23`, `dbeae63`, `0432591`, `11540f6`, `98212eb`; extra `9d19390` (not a plan task); ledger `82850d1`.
- **C, agent teams (Tasks 12–15):** plan `b433a14`; `0205a20`, `39034d6`, `bd70874`; ledger `1e63ca5`.
- **D, MCP servers in configuration files (Tasks 16–19):** plan `0f59a6f`; `80edda2`, `3073c81`, `93fa1f0`; ledger `d7946b0`.
- **E, the MCP protocol (Tasks 20–26):** plan `a9cccce`; `31fd601`, `210f865`, `63a6f8b`, `0fbc3e5`, `7d2b345`, `84ad5a6`; ledger `1d1ef86`.
- **F, plugins (Tasks 27–32):** plan `1b67f75`; `46c65a2` (Task 27), `8332c04`, `b14ebfe`, `5f8976d`, `66a0f74`; ledger `03264c0`.
- **G, one hook system (Tasks 33–36):** plan `d504407`; `8446e98`, `722fe60`, `79c4fd7`, `6aaba17` (deviation 39, separate commit); ledger `ab62a0c`.
- **H, diagnostics after edits and the W5 close (Tasks 37–38):** plan `909b430`; `aa0b7f3` (Task 37); this commit (Task 38: ledger and summary).

### Deviations 1–41 (plus 42, from Batch H)

Plan header:
1. User skills and commands are read from `~/.orin/{skills,commands}` and `~/.claude/{skills,commands}`, not `ORIN_CONFIG_DIR/skills`.
2. No file watcher: each turn reads the skill and command folders afresh.
3. A skill's `allowed-tools` lasts until the end of the turn, decided after deny rules, ask rules, plan mode and protected paths.
4. A skill's `model` and `effort` are read and not applied (W6).
5. A person's `/name` for a disk skill or command reaches the model as a command reminder with the full instructions; the picker is W7.
6. `$ARGUMENTS[N]` and `$N` are 0-based and are substituted only when arguments were given.
7. Plugin skills stay in the skill library until Batch F.
8. CLI flags (`--mcp-config`, `--strict-mcp-config`, `--agents`, `orin code mcp serve`) are W6; the `/agents` editor, `@server:uri` mentions, the elicitation card, the MCP prompt picker and the command picker's new entries are W7.

Batch B:
9. An agent's `isolation`, `mcpServers`, `memory`, `effort`, `color` and `hooks` are read (`AgentDefinition`) and not applied.
10. A `model` alias maps through `modelAliases`; an alias with no mapping, or a model not allowed for subagents, runs on the parent's model.
11. A background agent's report is read with `check_subagent` (or `TaskOutput`) or from its output file; its completion does not resume the turn.
12. The `verifier` builtin is Orin's own wording and has no editing tools.

Batch C:
13. The team roster lives on the turn's session and is gone at the next turn; the mailbox is stored through the chat store's engine, or in memory (`InMemoryMailbox`) where the store has none.
14. `TeammateIdle` is emitted on the lifecycle bus and its hook answers are not read; the lead receives the teammate's last report (up to 2,000 characters) as a `teammate` reminder.
15. A message to a busy teammate waits until its run ends; a teammate reads at most `MAX_TEAM_DELIVERIES = 10` batches per run, then goes idle. `ask_agents` is unchanged.

Batch D:
16. A preloaded skill's `allowed-tools` and `` !`cmd` `` blocks stay out of a subagent; its text stays inside the subordinate wrapper.
17. `~/.orin/mcp.json`, `~/.orin/workspaces/<key>/mcp.json` and `.orin/mcp.json` are read; Orin's entry replaces Claude Code's entry of the same name within a scope.
18. A file server whose slug an Orin server already uses is skipped, and the model is told.
19. `personal` is `mcp_service.allow_stdio`; a session without a service, and a shared Orin, read only the managed file.
20. `expand` raises for `ORIN_*` and `AGENTOS_*` names, which stops only the server that uses them.
21. No approval prompt; pending project servers are listed in the `mcp` reminder with the settings keys that approve them.
22. File servers list their tools in parallel within `MCP_TIMEOUT` and cache each answer for 600 s (60 s for a failure); they cannot sign in with OAuth; `sse` and `ws` file servers are not supported yet.

Batch E:
23. Elicitation is declined unless an `Elicitation` hook answers it, and the model hears of the request in an `mcp` reminder; sampling requests are refused; `roots/list` returns an empty list.
24. `ReadMcpResourceDir` lists the resources whose address starts with the given uri; images come back as images; other binary content is described by its size and type, not saved.
25. `RefreshMcpTools` replaces a configuration-file server's tools; Orin's own servers keep their approved tools (`applied: false` with a note); a `list_changed` notice during a call is followed after that call.
26. No warning at 10,000 tokens; the cut at `MAX_MCP_OUTPUT_TOKENS` applies (default 25,000 tokens, clamped to 1,000–250,000).
27. MCP prompts do not appear in a `/` menu; `/mcp__server__prompt args` is handled in `_apply_user_command`. Its arguments were first split with `shlex`, which Batch G's Task 33 replaced with a quote-aware split.
28. SSE and WebSocket serve configuration-file servers only (`McpServerService.propose` refuses them); a stdio server gets 0.3 s to exit after its input closes before its process tree is killed; there is no Windows Job Object.

Batch F:
29. A plugin skill is read leniently (YAML frontmatter, only the fields the library stores); `allowed-tools`, `argument-hint` and other Claude Code keys are dropped.
30. A plugin's agents are named `plugin:agent` and lose `hooks`, `mcpServers` and `permissionMode`; its output styles are named `plugin:style`; its `settings.json` is not read. (Batch H now reads its `.lsp.json`; see deviation 40.)
31. `enabledPlugins: false` turns an installed plugin's agents and styles off for the turn; `true` for a plugin that is not installed is reported in a `plugins` reminder and not fetched.
32. Option values are encrypted with the provider-secret cipher; a sensitive value is never returned, only `is_set`; a missing required option blocks turning the plugin on; a sensitive option is refused in a command line or URL; changed values reach the MCP servers when the plugin is turned off and on again; options are set through `GET`/`PUT /v1/plugins/{id}/config`. (Batch G gives option values to hooks as well.)
33. A marketplace is a GitHub repository or a local folder; `extraKnownMarketplaces` is not read; a shared Orin refuses a local folder.
34. Only the first `commands` path of a manifest is read, and an inline `hooks` object is ignored.

Batch G:
35. A plugin hook runs only if its id is in the person's consent (`ActivePlugin.enabled_hook_ids`); it runs `command` handlers only, through `resolve_argv` with no shell, in the project folder, with the allowlisted environment, `plugin_variables` and the option values.
36. The worker no longer calls `rehydrate_hooks`, and the turn-local `HookEngine` stays empty; `plugins/hook_engine.py` and `plugins/rehydrate.py` remain and their tests still pass.
37. A skill's hooks are registered at its first use, once per (skill, agent), and removed with the settings hooks at turn start or on a refused configuration change; an agent's hooks are removed in the `finally` of `_execute_subagent_run`, and its `Stop` becomes `SubagentStop`.
38. Recorded in the plan: the stdio exit behaviour is Batch E code and was not changed in Batch G.
39. (Batch G's executor.) A managed `disableAllHooks` also switches off a managed skill's or agent's hooks; `_scoped_hooks` had skipped that check for managed origins (commit `6aaba17`).

Batch H:
40. A plugin's language server runs only once the person allowed the plugin to run code, and only on an Orin that runs plugin hooks; its environment is the worker's without Orin's variables or credential-named variables, plus its `env`.
41. Diagnostics are gathered in the background after Write and Edit and reach the model with its next request (errors and warnings, at most 30); a failing server is turned off for the turn and the model is told once; Orin has no `LSP` navigation tool; `restartOnCrash`, `maxRestarts`, `transport: socket` and `workspaceFolder` are not read.
42. (Batch H's executor; not in the plan, and numbered after the plan's last deviation.) A malformed message from a language server no longer stops its reader thread (Batch H, deviation 42).

### Deliberate expectation changes, by batch

- **A:** none recorded.
- **B:** `tests/unit/agentic/tools/test_monitor_review_fixes.py::test_a_subagent_is_built_with_the_turn_reminder_queue` (Task 9: it now checks that the monitor sink is the turn's queue and that the subagent's own `reminders` is not the turn queue; the recording wrapper also returns the toolset, as the plan prescribes).
- **C:** `tests/unit/persistence/test_postgres_schema.py` (lists `agent_mailbox`); `tests/unit/persistence/test_w3_schema.py` (head revision `0050_w5_extensibility`); `tests/unit/agentic/tools/test_tool_ledger.py` (32 included, 5 deferred, 12 excluded); `tests/unit/agentic/tools/test_public_contract.py` (the Agent tool lists `name` and `team_name`; a `send_message` entry).
- **D:** none. The diff of `tests/` adds tests and two lines in `tests/conftest.py`, and removes no line.
- **E:** `test_tool_ledger.py` changed from 32 / 5 / 12 to 36 / 1 / 12; `test_public_contract.py` gained four entries (`list_mcp_resources`, `read_mcp_resource`, `read_mcp_resource_dir`, `refresh_mcp_tools`).
- **F:** `test_postgres_schema.py` (`plugin_settings` added); `test_w3_schema.py` (both assertions changed from `0050_w5_extensibility` to `0051_w5_plugin_settings`); `tests/integration/api/test_profile_isolation.py` (`GET` and `PUT /v1/plugins/{plugin_id}/config` added to the owned routes).
- **G:** `tests/unit/plugins/test_hooks_manifest.py`, three cases (the reference hooks file's first hook now asserts `run_async is True` with no warning; an unknown-event test is renamed and uses `NotAnEvent`; the timeout clamp expects 600 instead of 30); `tests/unit/workers/test_multi_session_concurrency.py` (one stub line removed).
- **H:** none.

No other existing test was changed, weakened or skipped.

### Open observations, by batch (status as the ledgers and commits record it)

- **Batch A** (its review findings):
  - 1, unscoped shell grants from a skill: closed by Batch B, Task 6.
  - 2, whole file read before the size check: closed by Task 6.
  - 3, full enumeration before the cap: partly closed. Bounded per folder at 2,000 entries (`MAX_FOLDER_ENTRIES`); the 200-skill cap still applies after the listing.
  - 4, symlinked `SKILL.md`: closed in code by Task 6, not verified on Windows (the test skips here, since symbolic links cannot be created).
  - 5, double substitution: closed by Task 6 (cosmetic).
  - 6, arguments inside `` !`cmd` `` blocks: **open**. No later ledger records it closed.
  - Also open: `Skill` is offered whenever the session carries a disk catalog, even an empty one. Tightening the test to "the catalog has skills" is a one-line change, left for the owner.
- **Batch B:**
  - Preloaded skills: the wrapper part is closed by Batch C, Task 12. **Still open:** a preloaded skill's `allowed-tools` are not granted to the subagent and its `` !`cmd` `` blocks are not run (Batch D, deviation 16).
  - Replacing a builtin: closed for repositories by Batch C, Task 12 (they cannot replace `explore`, `plan`, `general-purpose`, `general`, `fork` or `verifier`). A person's or a managed definition still may; the owner may want a warning.
  - Not verified: the Task 6 symbolic-link test skips on this Windows account.
- **Batch C** (all four closed by Batch D, Task 16): messages lost when a start is refused (item 1 of Task 16); idle with mail still pending (item 3); cancelled teammates (item 5, with a test); a teammate's name is turn-wide (item 4). Also, `agent_tools.py` is 1698 lines and the guard in `test_split.py` requires fewer than 1700, so two lines of room remain.
- **Batch D:**
  - A teammate can stay busy for the rest of the turn when its start raises: closed by Batch F, Task 27 (commit `46c65a2`, "a teammate is released when its start raises").
  - Failure texts mask only values of four or more characters and never the URL path: closed by Batch F, Task 27 (commit `46c65a2`).
  - **Open:** approval is by settings keys only (deviation 21), so approved file servers may run any command line. The approval prompt arrives with W7.
- **Batch E:**
  - NaN or infinite numbers in an elicitation answer are accepted: closed by Batch G, Task 33, item 1.
  - Backslashes in `/mcp__…` arguments are lost: closed by Batch G, Task 33, item 2.
  - A `/mcp__…` message is silently ordinary text when no MCP server is connected: closed by Batch G, Task 33, item 3.
  - SSE and WebSocket check the address only when the transport is built: closed by Batch G, Task 33, item 4 (commit `8446e98`, "address re-checks").
  - **Open:** a graceful stdio close does not kill what the server started (Batch G, deviation 38).
- **Batch F** (not verified, or left for later):
  - Postgres: migration `0051_w5_plugin_settings` and the `plugin_settings` table were verified on SQLite only; the integration suite skips the Postgres tests here.
  - The config routes were tested with a fake plugin service; the authentication path (`principal_for`, rate limit, authorize) was not exercised with a real security object.
  - Windows hook paths: a hook command substitutes `${CLAUDE_PLUGIN_ROOT}` unquoted and then splits it with `shlex`, so a plugin installed under a folder with a space in its path splits into two arguments. This existed before Batch F; not fixed.
  - `"agents": "."` maps to the default `agents` folder, not to the package root; not fixed.
  - Marketplace `search` returns the server-side absolute path of a relative plugin as its `reference`; a bare relative source without `./` is read as a GitHub `owner/repo`; not fixed (the plan specifies the `./` form).
  - The lenient frontmatter reader does not cap the size of the frontmatter before YAML parsing; Batch A's loader has the same property; not changed.
- **Batch G** (not verified, or left for later):
  - **Decision needed:** plugin hook consent is tied to the hook id, not to its content. Hook ids are `{plugin}:{Event}:{index}`, and the command is read from the installed package at every turn; the recorded `package_digest` is never compared with the files. (a) A changed command under a consented id, or a hook inserted earlier in the same event (which shifts the indexes), runs under the old consent. (b) `set_enabled(enabled=True)` in `plugins/service.py` gives every hook row of a re-activated package the previous "all hooks were enabled" flag, so a hook that a later version adds is enabled without a new consent, which contradicts deviation 35 (Batch F code). Not fixed: the full fix needs a schema change and a migration. The ledger's suggested fix: store a digest of the event, matcher and command with each consent row, run a hook only when its current digest matches, and carry consent into a re-activation only per (id, digest).
  - Windows hook paths and symlinks inside a plugin folder: not tested.
  - Postgres: Batch G adds no migration, but the 65 integration skips were not run here.
  - POSIX: the 43 shell-script hook tests were skipped on this machine, so the POSIX runner path is covered by test code only.
  - Frontend: only `npm --prefix frontend test` was run in Batch G (no frontend file changed).
- **Batch H:** the five observations listed in its section above (malformed publish waits the full timeout; malformed diagnostic item silently dropped; late answer may be taken for the current one; stderr discarded; diagnostics only after Write and Edit).

### Suites at the W5 close (Task 38)

- `uv run pytest -q tests/unit`: 3857 passed, 60 skipped, 0 failed.
- `uv run pytest -q tests/integration`: 78 passed, 65 skipped (Postgres and Redis not configured), 0 failed.
- `npm --prefix frontend test`: 103 files, 609 tests passed. Lint and build: exit 0.
- Platform: Windows only. The POSIX branches of the shell-script hook tests and the symbolic-link tests did not run here.

### Standing caveats for the owner

- The commit trailer is `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` on the W5 commits of Batches A to C and E to H, as the owner's standing instruction for this branch requires. Batch D's three commits used the Haiku line, as its ledger records. The session's attribution note named Haiku 5.5. The owner should decide which line stays.
- No commit was pushed, and no file was deleted.

## Review fixes (part 1)

Scope: the eight "Must fix before W6" findings W5-R1 to W5-R8 of `docs/superpowers/reviews/2026-10-19-w5-review.md`. Each was test-first: its tests were written, run and seen to fail for the reason the finding gives, then the fix was made, then the tests passed. One commit per finding, on this branch. W5-R9 to W5-R16 and the deferred items were not touched.

Per finding (commit, change, tests in `tests/unit/...`):
- **W5-R1** (`5fe8a70`): `enableAllProjectMcpServers` and `enabledMcpjsonServers` are always ignored from a repository's settings (`settings/loader.py`). A repository can still disable its servers. Tests: `settings/test_w5_review_fixes.py` (5): the repository's own and `settings.local.json` files cannot approve; the person's user layer and grants file still approve.
- **W5-R2** (`2cff188`): a project skill's shell grant must start with one program's literal name; wildcard spellings are dropped and logged (`_is_scoped_grant`, `agentic/tools/orin/disk_skills.py`). `Bash(git status:*)` still works. Tests: `agentic/test_w5_review_fixes.py` (2).
- **W5-R3** (`8c8881c`): `HookContribution.consent_reference` = `{hook_id}#{first 16 hex of sha256(event, matcher, command, timeout)}`; the activator, `hook_specs`, `rehydrate_hooks` and `set_enabled`'s carry-over use it; `set_enabled` refuses a pending plugin (`plugin_not_approved`, which the API answers 409). Tests: `plugins/test_w5_review_fixes.py` (8).
- **W5-R4** (`61a03ca`): with `plugin_hooks` off, settings hooks come only from the managed and user layers; no skill, agent or plugin hook is installed (`_scoped_hooks` returns None); a user http hook must pass `_public_url(url, resolve_dns=True)`. Tests: `agentic/test_w5_review_fixes.py` (7, server and desktop cases).
- **W5-R5** (`547fa10`): `personal` flag on `skill_roots`/`load_disk_skills`, `agent_roots`/`load_agent_definitions`, `_style_dirs`/`load_output_styles`/`resolve_output_style`; the session passes `personal=self.plugin_hooks`. Tests: `agentic/test_w5_review_fixes.py` (4).
- **W5-R6** (`424a7a3`): `lsp.config.declared_servers(install_path)` is the one parser for `.lsp.json`; `LspServerContribution` (`{name}#{digest}`); `PluginInspection.lsp_servers` is counted and shown on the card (name, command, args, extensions); activation records disabled `lsp` rows; `set_hooks_enabled`, `set_enabled` (carry-over and the blanket toggle) treat `CONSENT_KINDS = ("hook", "lsp")`; `lsp_servers` starts only a server whose exact row is enabled. Tests: `lsp/test_w5_review_fixes.py` (6).
- **W5-R7** (`8a70763`): inline spans are found in the raw body (`split_inline_commands`); an argument fills a span as one shell word (`shell_word`) and fills plain text with its backticks escaped (`escape_backticks`); the rendered text is never re-scanned. Tests: `agentic/test_w5_review_fixes.py` (3).
- **W5-R8** (`18c1321`): a skill's grants are added before its inline commands run. Tests: `agentic/test_w5_review_fixes.py` (2: the first use asks nothing; a deny still wins on the first use).

### Existing expectations changed (recorded)
1. `plugins/test_service.py::test_get_and_list_expose_the_contribution_rows` (R3): the hook reference is `demo:SessionStart:0#<digest>`; the assertion checks the prefix.
2. `plugins/test_activator.py::test_hooks_are_installed_without_consent_to_execute` (R3): the reference equals `inspection.hooks[0].consent_reference`.
3. `plugins/test_plugin_hooks_unified.py::_row` (R3): the hook ids are mapped to their consent references.
4. `plugins/test_command_rehydration.py::test_rehydrate_hooks_registers_active_plugins_with_their_stored_consent` (R3): the stored reference is the consent reference (helper `_consent_reference`).
5. `plugins/test_command_rehydration.py::test_rehydrate_hooks_fails_closed_on_partially_consented_rows` (R3): the expectation changed from "no hook runs" to "exactly the consented hook runs", because consent is now per hook and the all-rows rule is gone; the disabled row is for a hook the package does not declare. The test name is unchanged.
6. `hooks/test_scoped_hooks.py`, the skill test and the agent test (R4): `session.plugin_hooks = True`, because on a shared Orin a skill's or an agent's hooks no longer run.
7. `agentic/test_agent_definitions_session.py::test_custom_agents_are_listed_for_the_model` (R5): `session.plugin_hooks = True`.
8. `agentic/test_disk_skills_session.py`, two tests (R5): `session.plugin_hooks = True`.
9. `plugins/test_plugin_runtime.py::test_the_turn_reads_the_persons_plugins` (R5): `session.plugin_hooks = True`.
10. `lsp/test_lsp.py::_row` (R6): consent is an `lsp` row keyed by the server's reference, not a hook row.

Items 6 to 9 change the test sessions, not the code under test: `TurnSession.plugin_hooks` defaults to False, the shared-Orin behaviour, so these desktop expectations now opt in explicitly. No other existing test changed.

### Deviations from the finding text
- R2: also dropped, for a project skill: a grant whose program is a shell or a command wrapper (`env`, `sh`, `bash`, `cmd`, `powershell`, `sudo`, `xargs`, ...), because `Bash(env:*)` approves every command. Not in the finding's text.
- R3: `set_hooks_enabled` also refuses a pending plugin, and registers hooks only while the plugin is active (the legacy `HookEngine` path ran SessionStart hooks that the person had not seen); `inspect()` stops the previous package's registered hooks; the legacy registration is filtered by the same references. The digest is over event, matcher, command and timeout as specified, so changing only `async` keeps consent.
- R3, **upgrade**: rows written before this change have plain ids and count as no consent. **The person must allow the plugins' hooks once more after this change.** The same holds for the new language-server rows (R6).
- R4: the desktop is unchanged as the finding says, so a repository's skill http hook can still reach loopback there. On a shared Orin the public check runs at install time with a DNS lookup and is not pinned to the connection (WebFetch pins its address), so a DNS rebinding window remains for user http hooks.
- R5: a plugin's agent and output-style folders are also not read on a shared Orin (the finding names only the managed and trusted-project folders).
- R6: `declared_servers` takes the install path only and reads the manifest itself; the digest covers the whole declared entry; the card shows the declared command and args with `${CLAUDE_PLUGIN_ROOT}` unresolved.
- R7: on Windows the inline command runs through `cmd.exe` (the Bash tool uses `shell=True`), where `\"` is not an escape. A value with a double quote, a percent sign, a line break or a NUL is therefore refused and that command is not run, instead of escaped as the finding's text suggests.

### Not fixed, or residual (honest)
- **The consent covers the declared command, not the script it runs** (R3, R6): a hook `python ${CLAUDE_PLUGIN_ROOT}/hooks/a.py` keeps its consent if `hooks/a.py` is edited in place, and the same holds for a language server's script. Closing it needs a digest of the package's files; not done.
- **Program-wide grants remain for a project skill** (R2): `Bash(git:*)` passes the finding's rule (a literal word before `:`), so a repository skill can still pre-approve every `git` subcommand, including `git push --force`. Broad non-shell grants (`Edit`, `WebFetch`) also pass. Refusing a bare program with `:*` would be a policy choice for the owner.
- R6: "allow hooks" also starts the language servers the card lists (the finding's design); the card now shows them.
- R3, UI (not changed, outside the findings' files): `frontend/src/features/plugins/PluginCard.tsx` leaves the "Permitir execução de hooks" switch enabled for a pending plugin, so a click now gets the 409 and the card shows nothing. The fix is one attribute, `disabled={busy || plugin.state === 'pending_approval'}` on that switch, with a test.
- R3: the review's own reproduction `test_r03_plugin_consent_carry_over.py` now stops at `set_enabled(True)` on the pending record with the intended `PluginServiceError`.
- Not verified here: the 65 integration skips need a PostgreSQL DSN (none configured), so the Postgres-backed tests did not run; the POSIX branch of the shell-word test, and the POSIX shell-quoting path in general, did not run on this Windows machine.

### Suites after part 1
- `uv run pytest -q tests/unit`: 3894 passed, 60 skipped, 0 failed (before the fixes: 3857 passed, 60 skipped).
- `uv run pytest -q tests/integration`: 78 passed, 65 skipped (PostgreSQL DSN not configured), 0 failed.
- `npm --prefix frontend test`: 103 files, 609 tests passed. `npm --prefix frontend run lint`: exit 0. `npm --prefix frontend run build`: exit 0 (the existing chunk-size warning).
- `src/agentos/agentic/agent_tools.py`: 1698 lines (the limit is below 1700).

## Review fixes (part 2)

Scope: the "Must fix before W6" findings W5-R9 to W5-R16 of `docs/superpowers/reviews/2026-10-19-w5-review.md`, and the UI follow-up of W5-R3 (the hooks switch). Each was test-first: its tests were written, run and seen to fail for the reason the finding gives, then the fix was made, then the tests passed. One commit per finding, on this branch. The deferred items (W5-D1 to W5-D5) were not touched.

Per finding (commit, change, tests in `tests/unit/...` or `frontend/...`):
- **W5-R9** (`c73c85b`): every call has an absolute ceiling, `call_ceiling_seconds(timeout)` = min(10 x timeout, `MAX_CALL_SECONDS` = 600 s), and at most `MAX_SERVER_REQUESTS_PER_CALL` = 100 answered server requests (`mcp/protocol.py`); the stdio, SSE and WebSocket waits check both. Tests: `mcp/test_w5_review_fixes.py` (7: a polite server is still answered; a server pinging forever is cut at the ceiling; a server sending too many requests is refused; for each transport).
- **W5-R10** (`7aa9505`): `_policy_url` normalises a URL and a pattern (lower-case scheme and host, default port dropped, one trailing host dot dropped, dot segments and `%2e` resolved); a host `*` matches host characters only; a URL that cannot be read (user information, a backslash, whitespace or a control character, an unknown scheme, a bad port, an odd host) is refused when an allow list or a deny list exists (`mcp/file_config.py`). Tests: `mcp/test_w5_review_fixes.py` (22).
- **W5-R11** (`a0189c6`): `split_frontmatter` uses a `yaml.SafeLoader` subclass that refuses anchors and aliases (`_frontmatter_loader`), and refuses a block over 64 KB (UTF-8 bytes) before it is parsed (`skills/disk.py`). The lenient plugin-skill reader inherits both. Tests: `skills/test_w5_review_fixes.py` (7: an alias in a skill, an agent file, a plugin skill and a plugin agent; an anchor without an alias; the 64 KB cap; a control).
- **W5-R12** (`8aeb4bb`): `_teammate_refusal` claims a new name under `_team_lock` with a placeholder (busy, no agent id yet); `_ask_agent` does not resume a placeholder; `_release_teammate` removes a placeholder whose start never began; a message to a busy name is queued by the existing path (`agentic/session.py`). Tests: `agentic/test_w5_review_fixes_part2.py` (3: two starts run one; a failed start frees the name; a message to a name being started is queued).
- **W5-R13** (`06f8dc4`): `_lsp_closed` (set by `_close_lsp`, reset by `build_runtime`) and `_lsp_lock` guard the manager factory (`_lsp_manager`, now a wrapper of `_make_lsp_manager`) and the pool submission; `_close_lsp` waits up to `LSP_CLOSE_WAIT_SECONDS` = 2 s for submitted checks before it closes the manager. Tests: `agentic/test_w5_review_fixes_part2.py` (2: a write after close starts nothing; closing waits for a check in flight).
- **W5-R14** (`bbe5439`): `resolve_argv` splits the declared command first and puts the native folder into each word afterwards; every confinement check is unchanged (`plugins/hook_executor.py`). Tests: `plugins/test_w5_review_fixes_part2.py` (5: the four spellings with a space in the folder; the confinement still refuses a script beside the package).
- **W5-R15** (`9d6828f`): `_validate` refuses an entry that is a symlink, a junction (`Path.is_junction()`) or another reparse point (`_is_link`, `plugins/fetcher.py`). Tests: `plugins/test_w5_review_fixes_part2.py` (1: a junction in a local package; Windows only, skipped elsewhere).
- **W5-R16** (`6588bc7`): for a configuration-file server (`allow_any_command=True`), the operator check runs only when `shutil.which(command)` resolves to a `.cmd` or `.bat` file, and then against the cmd operators (see the deviations); Orin's own servers keep the old rule (`mcp/transport_stdio.py`). Tests: `mcp/test_w5_review_fixes.py` (3: the three ordinary values start; Orin's own servers keep the POSIX rule; a `.cmd` launcher refuses `%`, `^`, `&` and `|`; Windows only).
- **UI-R3** (`53f2fcc`): the hooks switch is `disabled={busy || plugin.state === 'pending_approval'}`; it covers the consent rows of kind `hook` and `lsp` (the coordinator's addition): it is shown when any exists and is on only when every one is allowed (`frontend/src/features/plugins/PluginCard.tsx`). Tests: `frontend/src/features/plugins/PluginCard.test.tsx` (4: a language-server-only plugin shows the switch; the switch is on only when every row is allowed; a pending plugin's switch is disabled; an active plugin's switch is enabled).

### Existing expectations changed (recorded)
None. No existing test file changed in part 2: every test added is in a new file (`tests/unit/mcp/test_w5_review_fixes.py`, `tests/unit/skills/test_w5_review_fixes.py`, `tests/unit/agentic/test_w5_review_fixes_part2.py`, `tests/unit/plugins/test_w5_review_fixes_part2.py`, `frontend/src/features/plugins/PluginCard.test.tsx`), and every existing test passed unchanged.

### Deviations from the finding text
- R9: the ceiling is a function of each transport's timeout (`call_ceiling_seconds`), not a single module constant, because the timeout is per transport. The stdio transport also terminates the server when the request cap is hit (the finding names the ceiling). The ceiling is checked on every loop pass, so a flood of notifications is cut too. SSE and WebSocket leave the connection open after the limit, as their existing timeout does.
- R10: a pattern that names no port matches any port of its host; a pattern that names no path matches any path (so the existing `https://*` deny still works); the schemes are http, https, ws and wss. An IPv6 literal host is unreadable, so it is refused when a list exists. A malformed URL is refused by any allow list, even for a server the allow list names (fail closed).
- R11: the 64 KB cap counts the UTF-8 bytes of the frontmatter block.
- R13: the grace period (the finding says "briefly") is 2 s.
- R14: the native folder (backslashes on Windows) is substituted, where the previous code substituted forward slashes; `_inside` resolves the result as before.
- R16: for a `.cmd` or `.bat` launcher the refused set is `&|<>^%` plus CR and LF, not only the `><^%` the finding names. cmd.exe reads `&` and `|` as operators, and the old rule refused them, so the narrower set would let `&` through a `.cmd` launcher. For configuration-file servers `$`, `;` and the backtick are no longer refused. Credential values are checked against the same set when the launcher is a `.cmd` or `.bat`.
- UI-R3: the switch also covers the language-server rows (the coordinator's message; the finding names only the hook rows). The label is unchanged.

### Not fixed, or residual (honest)
- **R13, residual:** `LspManager.check` has no closed flag of its own. A check still running after the 2 s grace can start a language server after `manager.close()`. The session-level fix narrows that window and does not close it. Closing it needs a closed flag in `lsp/manager.py`, outside the finding's files; not done.
- **R12, residual (pre-existing):** a start that fails after `_ask_agent` has written the roster record (a model that is not allowed, or the turn's subagent budget is used up) leaves a real, idle roster entry, because `_release_teammate` only clears `busy`. Not changed.
- **R9:** an SSE or WebSocket connection that hit its ceiling stays open, so a later call on it can read messages left from the earlier one (the same holds for the existing timeout).
- **R15, R16:** the junction and `.cmd` tests are Windows-only and were not run on POSIX. The POSIX symlink refusal is the existing code path.
- **UI-R3, visual:** no browser check was run. The change disables an existing switch; it is verified by the component tests, lint and build only.
- **Integration:** the 65 integration tests that need PostgreSQL were skipped (no DSN configured), so the Postgres-backed paths were not run.

### Suites after part 2
- `uv run pytest -q tests/unit`: 3944 passed, 60 skipped, 0 failed (after part 1: 3894 passed, 60 skipped); 50 tests added.
- `uv run pytest -q tests/integration`: 78 passed, 65 skipped (no PostgreSQL DSN), 0 failed.
- `npm --prefix frontend test`: 104 files, 613 tests passed (after part 1: 609); 4 added.
- `npm --prefix frontend run lint`: exit 0. `npm --prefix frontend run build` (`tsc -b` and `vite build`): exit 0 (the existing chunk-size warning).
- `src/agentos/agentic/agent_tools.py`: 1698 lines (the limit is 1700).
