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
