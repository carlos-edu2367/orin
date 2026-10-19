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

Batch B has not started.
