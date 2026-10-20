# W6 ledger — headless CLI and SDK protocol

## Lote 0 (before the W6 plan)

Bounded fixes on top of 0.9.2 (`eba5b00`), done directly with TDD, one commit per item. This range is part of the
W6 review.

### L0-1 (critical, SERVER) — `full_autonomy` opened the host file system on a shared Orin

- Reproduced first, isolated (scratchpad `lote0/repro1.py`, every `ORIN_*`, HOME and USERPROFILE in a temp dir):
  a `TurnSession(host_folders=False)` with `permission_mode=full_autonomy` built a toolset whose workspace was
  `unrestricted`; `read_file` and `search_files` returned a host file's contents and `write_file` created a file
  outside the workspace.
- Fix (fail-closed): `AgentToolset(host_folders=False)` is the default; `PlanModeTools._sync_workspace_restriction`
  opens the workspace only when the mode is `full_autonomy` **and** the toolset has host folders. `TurnSession._toolset`
  passes `self.host_folders` (which `workers/chat.py` takes from `InstanceCapabilities.host_folders`), so every
  subagent toolset inherits it too.
- Prompts: the workspace hint (`session.full_autonomy_workspace_hint`), `code_mode/prompt.py`
  (`host_access=`) and `code_mode/system_prompt.py` (`render_code_turn_context(host_access=)`) only promise paths
  outside the workspace when that access exists; otherwise the text says the instance gives no access outside it.
- Deliberate: the gateway and `conversations/chat.py` still accept `full_autonomy` in SERVER — there it means "no
  approval prompts", never "outside the workspace". The web selector's wording is a W7 item.
- Test change, not a weakening: `tests/unit/test_full_autonomy_unrestricted.py` now passes `host_folders=True` /
  `host_access=True` explicitly — it describes the desktop case, and the default is now closed. No assertion changed.
- Tests: `tests/unit/test_full_autonomy_host_folders.py` (SERVER + full_autonomy: Read, Write, Glob, Grep outside are
  refused; desktop still reaches the host; default toolset is closed; leaving plan mode on a server stays closed; no
  prompt promises host access).
- `agent_tools.py`: 1699 lines (guard `< 1700`). W6 code must go in its own modules.

### L0-2 (high, every OS) — deny/ask rules did not see the file a `~` (or POSIX `/…`) path opens in full_autonomy

- Reproduced on Windows (test `test_the_unrestricted_tool_opens_the_home_file` + RED run): the unrestricted workspace
  opens `<home>/.ssh/id_rsa` for `~/.ssh/id_rsa`, while `rules._target` → `workspace_path` checked
  `<workspace>/~/.ssh/id_rsa`, so `Read(~/.ssh/**)` / `Edit(~/.ssh/**)` never matched (9 RED cases).
- Fix: `permissions.paths.path_meanings(raw, root, home=)` lists every file a tool path can name — the workspace
  reading first, then `~` expanded with the engine's home (also `~\…`, and `~user/…` when that user exists), and on
  POSIX `/…` at the real root (on Windows a UNC/absolute non-drive path). `rule_matches(any_meaning=True)` checks all
  of them; the engine passes it for **deny and ask** rules and `autoMode.hard_deny`. Covers Read, Write, Edit,
  NotebookEdit (`notebook_path`), Glob and Grep (`path`).
- Decision (fail-closed, recorded): **allow** rules keep the workspace reading only — a second meaning never grants
  anything. `_inside` (acceptEdits) and the protected-path check are unchanged: in every mode but full_autonomy the
  workspace is confined, so the workspace reading is the file that opens; in full_autonomy only deny/ask apply.
- W1-R3 final semantics (decided here): a POSIX `/x` outside every working directory keeps meaning
  `<workspace>/x` for a confined workspace (what the tool opens) and `/x` for a full_autonomy one (what the tool
  opens); deny/ask rules see both. Documented in `path_meanings`'s docstring.
- Known gap, not fixed (pre-existing, also inside the workspace): a Grep/Glob rooted at a *parent* of a denied folder
  (`path="~"`, or `.` with `Read(secrets/**)`) still walks into it. Needs the search tools to filter files by the
  deny rules — handed to the W6 review.
- Tests: `tests/unit/permissions/test_path_meanings.py` (HOME and USERPROFILE monkeypatched to tmp; the POSIX `/…`
  case is `skipif(os.name == "nt")` and runs in Docker/CI).
- Follow-up found by the Linux run: an absolute `//dir/**` rule did not cover the directory itself (the relative
  `dir/**` form already did), so a Glob/Grep rooted at a denied absolute folder was allowed. Fixed in
  `path_rule_matches`; test `test_paths.py::test_an_absolute_dir_rule_covers_the_directory_itself` (both OSes).
- Follow-up: `test_full_autonomy_host_folders.py`'s SERVER case assumed Windows. On POSIX a confined workspace reads
  `/tmp/...` inside itself (W1-R3), so a write there succeeds *inside the workspace*; the test now asserts, on every OS,
  that no host content or host file name reaches the model and nothing is written to the host folder, and on POSIX
  that the write landed inside the workspace. Observed UX wart (not changed): the success text echoes the absolute
  path the model asked for, not the workspace file that was written.

### L0-3 — `tests/unit/test_full_autonomy_unrestricted.py` on POSIX

- The confined cases assumed Windows. Now: on Windows a drive path outside is refused (unchanged); on POSIX the test
  asserts the W1-R3 meaning — the path resolves inside the workspace, the write lands there and the external file is
  never created. Added, on every OS: a `../` write is refused and the external file still does not exist. The `../`
  resolve case and `key()` refusal are unchanged. Green on Windows and in Docker (python:3.13, non-root).

### L0-4 — intermittent `test_parallel_run_subagent_and_checkpoint_concurrency` (systematic debugging)

- Root cause (read, then confirmed): every parallel call takes `checkpoint_lock` in the runtime for
  `effect_started` (the test's journal write sleeps 20 ms plus a SQLite write) before the handler runs, and the
  handler overlapped only if the next call's wait for that lock fit into the first handler's 80 ms sleep. On a slow
  runner it does not, and `max_parallel` stayed 1. Confirmed by making the checkpoint take 100 ms: the old test failed
  every time. The runtime itself was running the calls in parallel.
- Fix (test only, no runtime change): the handler counts under a lock and waits on an `Event` set when two calls are
  inside it at once (10 s timeout). The assertion `max_parallel >= 2` is unchanged.
- Adversarial checks (scratchpad `lote0/serial_check.py`): with a 300 ms checkpoint the test still passes; with the
  runtime forced serial (`MAX_PARALLEL_SUBAGENT_TOOLS = MAX_PARALLEL_TOOLS = 1`) it fails with `max_parallel=1`.

### L0-5 — suites before the push

- Windows (`uv run pytest -q tests/unit tests/integration`): **4054 passed, 127 skipped, 0 failed**.
- Linux, Docker (python:3.13, non-root `runner`, repo copied to `/work`): **4067 passed, 4 failed, 110 skipped**. The same
  4 fail on the baseline `eba5b00` in the same container, so none comes from Lote 0, and the CI's Ubuntu job passed
  them at `eba5b00`:
  - `test_interruption.py::test_a_cancel_stops_the_running_command` and
    `hooks/test_processes.py::test_timeout_kills_the_whole_tree`: the owner's container runs `sleep infinity` as PID 1,
    which never reaps children, so killed processes stay zombies and still "run". A new container
    `orin-linux-init` (from a snapshot image `orin-linux-test:w6-snapshot`, started with `--init`) passes both. The
    owner's `orin-linux-test` container was stopped, not removed.
  - `test_subagents_and_permissions_v076.py::test_verification_command_with_screenshots_preserves_code_mode_verified`
    and `test_orin_code_phase_a.py::test_runtime_recovers_from_thinking_only_turn_and_filters_code_mode_schemas`: the
    container has no Node/npm, so the verification step the tests run cannot succeed there.
- Frontend: not run locally — Lote 0 changes no frontend file; the CI frontend job covers it.

## Batch A — Foundation (Tasks 1–5)

Commits on `main` after the plan commit `b77d146` (`git log --oneline b77d146..HEAD`):

- `dda85a9` feat(w6): the orin code command line (Task 1)
- `676f158` feat(w6): a per-run API token for orin code (Task 2)
- `4bdcbb2` feat(w6): GET /v1/instance tells orin code which Orin it reached (Task 3)
- `7d1329a` feat(w6): orin code finds or starts the local Orin and refuses a shared one (Task 4)
- `6a2ccaf` test(w6): a live Orin runtime on a fake Ollama for orin code tests (Task 5)

Tests added (Windows, 2026-10-19):

- `tests/unit/cli_code/test_args.py`: 34 tests (the command line, refusals, the `orin-code` script and the frozen-copy dispatch)
- `tests/unit/cli_code/test_api_token.py`: 10 collected, 1 skipped on Windows (the POSIX mode test)
- `tests/unit/cli_code/test_instance_route.py`: 3
- `tests/unit/cli_code/test_runtime_client.py`: 15
- `tests/unit/cli_code/test_web_dist_override.py`: 2
- `tests/integration/cli_code/test_live_runtime.py`: 3 (real launcher, API and worker on the fake Ollama, about 23 s)
- Existing expectation changed: `tests/integration/api/test_profile_isolation.py` now classifies `GET /v1/instance` as an
  instance-wide route (`INSTANCE` set). That is the only change to an existing test.

Suite results (Windows, `uv run pytest -q ...`, after Task 5, before this ledger commit):

- `tests/unit`: **4039 passed, 63 skipped, 0 failed** (314.9 s)
- `tests/integration`: **81 passed, 65 skipped, 0 failed** (84.9 s)
- `src/agentos/agentic/agent_tools.py`: 1699 lines (unchanged; this batch does not touch it)
- After the live tests: no `agentos.launcher` python process left running.

Deviations from the plan:

1. **Test literal in Task 2.** The plan's `monkeypatch.setenv("SystemRoot", "C:\Windows")` was written as `"C:\\Windows"`
   in the test file. The value is identical (`C:\Windows`); the plan's form has an invalid escape (`\W`) that Python warns
   about. Test code only, same assertion.
2. **Live tests have no RED run.** Task 5's `test_live_runtime.py` and the fixture are new scaffolding that exercises
   behaviour already built in Tasks 1–4; they went straight to green (3 passed). The TDD RED for Task 5 was the
   `ORIN_WEB_DIST` unit test (`test_orin_web_dist_is_used_as_is` failed before the change).
3. **`uv sync --all-groups`** after Task 1 reinstalled the local `agentos` package into `.venv`. `uv.lock` did not change.
4. **Launcher `main`** (Task 1) now parses the raw argument list in a local variable (`raw`) before the argparse call, as
   the plan's replacement block does; the behaviour for every existing verb is unchanged (`tests/unit/launcher` green).

Not verified in this batch (honest gaps):

- **POSIX branches were not run.** On Windows, `test_the_token_file_is_owner_only_on_posix` skips and the `os.chmod` path
  of `restrict_to_owner` is not exercised. The Linux/Docker run is the coordinator's step at release; the plan's
  Global Constraints say POSIX branches run in CI and in that Docker run.
- **Windows ACL test** (`test_the_token_file_is_owner_only_on_windows`) ran here and passed; it depends on the account
  name in `USERNAME` and on `icacls`.
- **Frontend**: no frontend file changed in this batch; not run.
- **Installers**: a frozen `orin-code.exe` copy is handled in code (`launcher.cli` dispatch, `_launcher_command`) and
  tested with a file named like it, but no installer ships it (the plan puts that outside W6).
- Nothing else from Batch A is incomplete per the plan.
