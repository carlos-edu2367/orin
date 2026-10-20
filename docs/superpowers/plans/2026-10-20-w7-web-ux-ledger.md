# W7 — Web UX parity: ledger

Plan: `docs/superpowers/plans/2026-10-20-w7-web-ux.md`. Spec §10.

## Decisions recorded while planning

1. **Host-file editors are local-profile only** (fail closed on a shared server: 404); `orin code`'s token may read
   them but never write.
2. **Redaction (W0-R11):** the activity log (and so its SSE stream and the interface) and HTTP hooks redact secrets in
   every string; local command hooks and the `orin code` transcript keep raw values, as in Claude Code.
3. **The status line** runs only from a trusted folder, never under `disableAllHooks`, only from managed policy under
   `allowManagedHooksOnly`, with a 5 s timeout, 5 lines of 400 characters (ANSI kept), one run per conversation at a
   time (a repeat within 1 s is cached); its stdin carries the documented fields Orin knows, the others are omitted.
4. **StatusLine and FileSuggestion are settings, not hook events; MessageDisplay is not implemented (W8).**
5. **Background shells record their creation time**; the tasks panel stops a shell only when the pid still names that
   process; an entry written before W7 is shown, never stopped.
6. **`orin code`'s session index keeps no ACL of its own** (W6-R15 covers transcripts and host requests).

## Batch A — Backend routes (Tasks 1–6)

Commits (plan commit `c58374d`; the plan's messages and trailer):

- `912c907` `fix(w7): secrets are redacted in the activity log, its stream and HTTP hooks` (Task 1)
- `694d5d2` `feat(w7): the settings API for the interface's editors` (Task 2)
- `155d79c` `feat(w7): background tasks routes that stop only the process a record names` (Task 3)
- `dec973d` `feat(w7): the status line and the command catalog routes` (Task 4)
- `c94d8e9` `feat(w7): UserPromptExpansion and permission-prompt Notification hooks` (Task 5)
- `d765612` `fix(w7): CLI transcripts expire in every conversation and their folders are owner-only` (Task 6)

The executor's tree was compared file by file with the coordinator's validated replay of the plan: identical.

Suites (Windows, 2026-10-20, on `main` at `d765612`, run by the coordinator — the executor handed back before its
full runs finished):

- `uv run pytest -q tests/unit`: 4355 passed, 66 skipped (398 s).
- `uv run pytest -q tests/integration`: 123 passed, 65 skipped (264 s).
- Linux (Docker, the validated tree): `tests/unit` 4371 passed, 48 skipped, 2 failed — the two container-only failures
  known since W6 (no Node in the container: `test_verification_command_with_screenshots_preserves_code_mode_verified`,
  `test_runtime_recovers_from_thinking_only_turn_and_filters_code_mode_schemas`).
- Found by the Linux run while planning and fixed in the plan: on POSIX, `process_created` fell back to `ps` (through
  `subprocess`) even where `/proc` exists, which broke a test that patches `subprocess.Popen`; it now returns None when
  `/proc` exists without the pid, and any failure of the `ps` fallback is None too.
- Found by the integration run while planning: the new routes had to be classified in
  `tests/integration/api/test_profile_isolation.py` (settings routes as instance-level and closed on a server;
  conversation routes as owned); the status line route checks the conversation's owner before anything else.

Deviations reported by the executor:

1. Task 2: 12 of 13 tests failed first (not 13): the shared-server 404 test passes before the routes exist, since an
   unknown route is a 404 too.
2. Task 3: `_alive()` in `cli_code/commands/background.py` is now unused (left, as in the plan).
3. Task 5: the broker's `event_emitter` lambda returns a tuple (the caller ignores the value).

Weak spots noted, not fixed: `_STATUSLINE_CACHE`/`_STATUSLINE_LOCKS` (conversation routes) and `_PRIVATE_DIRS`
(`api_token.py`) keep one entry per conversation or folder for the process's life (small, bounded by use).

Expectation changes: none beyond the isolation classification above.
