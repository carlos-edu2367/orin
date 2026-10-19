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
