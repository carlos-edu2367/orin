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

## Batch B — Headless turns (Tasks 6–11)

Commits on `main` after the plan commit `cdfc51b` (`git log --oneline cdfc51b..HEAD`):

- `7167e94` feat(w6): a turn carries the options orin code gives it (Task 6)
- `936b46a` feat(w6): an orin code turn runs with its CLI layer, tools and prompts (Task 7)
- `f89f286` feat(w6): orin code sessions and model selection (Task 8)
- `67c4fe4` feat(w6): Orin's activity events as Claude Code's headless messages (Task 9; `pyproject.toml`, `uv.lock`)
- `10d8e76` feat(w6): orin code -p and stream-json runs on the local Orin (Task 10)
- `b288c6b` feat(w6): --json-schema answers are validated and retried (Task 11; `pyproject.toml`, `uv.lock`)

Tests added (Windows, 2026-10-19):

- `tests/unit/cli_code/test_turn_options.py`: 15
- `tests/unit/cli_code/test_cli_turn_session.py`: 11
- `tests/unit/cli_code/test_sessions_and_selection.py`: 13 (1 skipped on Windows: the POSIX mode test)
- `tests/unit/cli_code/test_protocol.py`: 14 (parsed with the official `claude-agent-sdk` 0.2.159 parser)
- `tests/unit/cli_code/test_turn_options_builder.py`: 23
- `tests/unit/cli_code/test_structured.py`: 8
- `tests/integration/cli_code/test_headless.py`: 14 (live runtime on the fake Ollama; real `orin-code.exe`)
- Existing expectation changed: `tests/unit/persistence/test_w3_schema.py` now expects head `0052_w6_cli_turn_options`
  (the only change to an existing test).

Suite results (Windows, end of Batch B, `uv run pytest -q ...`):

- `tests/unit`: **4122 passed, 64 skipped, 0 failed** (322.4 s)
- `tests/integration`: **95 passed, 65 skipped, 0 failed** (131.7 s)
- `cli_code` unit and integration together: 153 passed, 2 skipped (before the Task 11 additions)
- `src/agentos/agentic/agent_tools.py`: 1699 lines (untouched by this batch)
- After the live tests: no `agentos.launcher` python process left running.

Deviations from the plan:

1. **Task 6, gateway snippet 7 was not unique.** The `create_kwargs["permission_mode"]` block occurs twice (the
   conversation route and the project route, which also reads `result = application.create(`). The Edit used the
   plan's snippet plus the following `ProviderCatalogContext(principal.user_id, "conversation.create")` line, which
   selects the conversation route only. The project conversation route is unchanged, as the plan requires.
2. **Task 7, `session.py` used `CODE_MODE_CORE_TOOLS` without importing it.** Added it to the existing
   `from .runtime import AgenticLimits, AgenticTurnRuntime` line (no other change to the import block).
3. **Task 8: no RED run.** The test file and `sessions.py` / `selection.py` were written in the same step and the first
   run was already green (12 passed, 1 skipped). The plan's expected collection error was not seen. Behaviour was
   checked by the tests; the TDD order was not followed for this task.
4. **Task 10: no RED run for the live tests.** `test_headless.py` (12 live tests) went straight to green; the unit
   builder tests did see their RED (`ModuleNotFoundError: agentos.cli_code.headless`). Same situation as Batch A's
   Task 5 scaffolding.
5. **`uv sync` after Task 11** uninstalled and reinstalled one package in `.venv` (the output did not name it; the local
   `agentos` package is the likely one, as in Batch A's note 3). `uv.lock` changed only by the two added lines of
   Task 11 (`jsonschema` in the `agentos` dependency list; the package was already locked through the SDK).
6. Task 9: `uv lock` added 304 lines (the SDK's dependency closure) and removed none; `uv lock` reported only `Added`
   entries. Nothing was downgraded.

Not verified in this batch (honest gaps):

- **POSIX branches were not run** (Windows only). The Linux/Docker run is the coordinator's release step.
- **The send route's `cli.trust_workspace` path has no test.** `test_turn_options.py` covers trust only on
  `POST /v1/conversations`. The send route calls `_trust_workspace(..., conversation_workspace(...).root)`, which would
  trust a *managed* workspace folder when the conversation has no bound folder. Recommended follow-up: a test, and
  trust only a bound folder on send.
- **Batch C items still refused:** `can_use_tool` and every control request except `initialize` answer an error;
  permission prompts never reach the host, so `--permission-prompts host` behaves as `none` in a headless run until Batch C
  (as the plan's deviation 19 states).
- **Pre-existing test pollution (not from this batch, not fixed):** the real `C:\Users\carlos\.orin\trusted-workspaces.json`
  holds pytest temporary folders for user `profile-b` (written 2026-10-19 11:48, before this batch's runs; entries from
  `test_skill_hooks_on_a_server` and `test_settings_hooks_on_a_serve`), and the real `C:\Users\carlos\.orin\settings.json`
  (2026-10-18) holds a hook that points at a pytest temporary folder. This batch's tests use `local-user` and their own
  temporary folders and do not appear in those files. Deleting or editing those files was not done (needs the owner's
  decision).
- **Frontend**: no frontend file changed in this batch; not run.
- **Installers**: unchanged (`orin-code.exe` is still only the console script; Batch A note stands).

## Batch C — The control protocol (Tasks 12–16)

Commits on `main` after the plan commit `7454faa` (`git log --oneline 7454faa..HEAD`):

- `ac96e37` fix(w6): a CLI turn's transcript lives apart from the sanitized activity log (Task 12)
- `a9fa171` feat(w6): a turn can ask the SDK host behind orin code (Task 13)
- `227a64c` feat(w6): an SDK host's hooks run as callback hooks (Task 14)
- `8632209` feat(w6): the SDK control protocol (Task 15)
- `68d2fb3` feat(w6): agents from --agents and from the SDK host (Task 16)

Tests added: `tests/unit/cli_code/test_transcript.py`, `tests/unit/cli_code/test_host_requests.py`,
`tests/unit/cli_code/test_cli_agents.py`, `tests/unit/hooks/test_callback_hooks.py`; the trust-on-send cases in
`test_turn_options.py`; the tool-event transcript case in `test_cli_turn_session.py`; the live
`test_a_long_tool_result_reaches_the_host_whole` (`test_headless.py`); `tests/integration/cli_code/test_control_protocol.py`
(10 live tests on `tests/integration/cli_code/sdk_host.py`); the profile-isolation rows for the transcript and
host-request routes.

Suites (Windows, 2026-10-20, after Task 16):

- `uv run pytest -q tests/unit`: 4141 passed, 65 skipped.
- `uv run pytest -q tests/integration`: 107 passed, 65 skipped (live `tests/integration/cli_code`: 29 passed).
- No `agentos.launcher` process left after the live runs.
- `src/agentos/agentic/agent_tools.py` is still 1699 lines; nothing under `data/` was written in this batch (its last
  write is from 17 October).

Deviations from the plan (decided or found while executing):

1. **Task 12 live RED not run.** The unit REDs were run (`ModuleNotFoundError` for `cli_transcript`; the trust case
   failed on the managed-folder assertion). `test_a_long_tool_result_reaches_the_host_whole` was not run red before its
   fix; it is green now.
2. **Task 13 and Task 14 REDs** failed at collection as the plan says, but Task 14's first error was the
   `ImportError` for `CallbackHookRunner`, not `unsupported hook type` (the test's expected first failure).
3. **Task 15 RED** of the live control-protocol tests failed at run time (`FileNotFoundError`: the write never happened),
   not with `No module named ...host_bridge`: the live tests do not import the bridge.
4. **Task 15 imports:** `headless.py` drops `import os` (the plan's replacement); `_control` was its only use.
5. **Task 15 sdk_host and live test names** are the plan's; nothing was renamed.
6. **Task 13 gateway placement:** the `host-requests` route is inserted directly before the `workspace-trust` POST route
   (the plan's anchor). Behaviour is unaffected. (During Task 12 the transcript route was briefly put after the POST
   route and then moved after the `GET workspace-trust` route, where the plan puts it.)

Expectation changes (recorded as the plan names them):

- `tests/unit/cli_code/test_cli_turn_session.py::test_tool_events_carry_the_input_and_result_only_for_a_cli_turn` is
  replaced by `test_tool_events_point_at_the_transcript_only_for_a_cli_turn` (it asserted the design Task 12 removes).
- `tests/integration/cli_code/test_headless.py::test_an_sdk_style_stream_json_session`: `mcp_status` now succeeds.
- `tests/unit/cli_code/test_turn_options_builder.py::test_flags_without_support_yet_are_refused`: `--agents` is
  replaced by `--agent` (`--agents` is now supported).
- `tests/conftest.py` isolates `ORIN_DATA_DIR` (Task 12).

Not done or not verified (honest gaps):

- **Batch D still refused:** `mcp_message`, SDK MCP servers, `--mcp-config` and `--strict-mcp-config`. Any other control
  subtype answers an error.
- **Windows only.** POSIX branches (`test_entries_are_owner_only`, file modes) were skipped or not run here.
- **Host-request and hook timeouts** are covered by unit tests with fake clocks and by the live `sdk_hook` tests; no live
  test has a host that times out.
- **`can_use_tool`** only ever allows once (`allow_once`); `updatedPermissions` are not persisted, as deviation 24 says.
  An allow with a changed `updatedInput` is denied and the turn is not interrupted.
- **Pre-existing test pollution (not from this batch, not fixed):** the earlier note on the real `C:\Users\carlos\.orin`
  files (`trusted-workspaces.json`, `settings.json`) still stands; this batch's tests do not write there.
- **Frontend:** no frontend file changed; not run.
- **Installers:** unchanged.

## Batch D — Conformance (Task 17)

Commit: `2b724cd` `test(w6): the official claude-agent-sdk drives orin code (W6 acceptance)`, the only new file
`tests/integration/cli_code/test_sdk_conformance.py` (verbatim from the plan; no test weakened or edited).

The official `claude-agent-sdk==0.2.159`, unmodified, with `cli_path` pointing at `orin-code`, against the live runtime
on the fake Ollama. Results (Windows, 2026-10-20):

- `test_a_simple_query`: passed (single text block "Hello from fake.", success result).
- `test_streaming_partial_messages`: passed (message_start ... message_stop, two content_block_delta).
- `test_can_use_tool_allows_and_denies`: passed (allow writes the file; deny leaves it absent and records a permission denial).
- `test_the_sdks_hooks`: passed (the SDK's PreToolUse hook denies the Write; tool_use_id matches the tool block).
- `test_interrupt`: passed (error_during_execution after an interrupt, fewer than 40 deltas).
- `test_resume`: passed (same session_id, success, conversation listed).

Suites after this commit:

- `uv run pytest -q tests/integration/cli_code/test_sdk_conformance.py`: 6 passed (46 s).
- `uv run pytest -q tests/integration`: 113 passed, 65 skipped (195 s). The 65 skips are the Postgres/Redis tests
  (`AGENTOS_TEST_POSTGRES_DSN` / `AGENTOS_REDIS_URL` not configured), the same count as after Task 16.

Not done or not verified (honest gaps):

- The plan's "see it can fail" check was not run as a separate step: the test was not made to fail on purpose (the plan
  forbids editing it). Its failure path is therefore not demonstrated by this batch.
- The unit suite (`tests/unit`) was not re-run in this batch.
- The 65 skipped tests need Postgres/Redis; those paths are not verified here.
- Windows only; POSIX-specific branches are not covered.

## Batch E — MCP from the CLI, subcommands and the interface (Tasks 18–21)

Commits (verbatim plan files, one commit per task; the trailer is `Co-Authored-By: Claude Opus 5.5`):

- `4e804b7` `feat(w6): MCP servers from orin code's command line and the SDK host` (Task 18)
- `42bf5e4` `feat(w6): orin code mcp (configuration files and serve)` (Task 19)
- `7ba396d` `feat(w6): orin code agents, doctor, auto-mode, background runs and purge` (Task 20)
- `279bdfa` `feat(w6): orin code plugin, and orin code [prompt] opens the session` (Task 21)

Tests (TDD: each new test file was run before its implementation; Task 18's unit file failed on the missing
`agentos.mcp.transport_sdk`, Task 19's on the missing `commands` package, Task 20's on the unimplemented subcommands,
Task 21's plugin unit test on the missing `commands.plugin`):

- Task 18: `tests/unit/mcp/test_cli_mcp_servers.py` (6); `tests/integration/cli_code/test_headless.py` and
  `test_sdk_conformance.py` gained one live test each (`--mcp-config` with the echo server; an SDK host's
  `create_sdk_mcp_server` relayed through `mcp_message`). Both pass.
- Task 19: `tests/unit/cli_code/test_mcp_subcommands.py` (9 passed).
- Task 20: `tests/unit/cli_code/test_session_subcommands.py` (7); `tests/integration/cli_code/test_info_live.py` (3 live:
  doctor, a background run with attach/agents/logs/rm, stop cancelling the turn). All pass.
- Task 21: `tests/unit/cli_code/test_plugin_subcommands.py` (1); `tests/integration/cli_code/test_interface_live.py` (2
  live: plugin list, `orin code "say hello"` opening a Code session). All pass.

Suites after Batch E (Windows, 2026-10-20):

- `uv run pytest -q tests/unit`: 4164 passed, 65 skipped (330 s). The 65 skips are the Postgres/Redis tests, as before.
- `uv run pytest -q tests/integration`: 120 passed, 65 skipped (224 s). Batch D had 113 passed; the +7 are the new live
  tests above.
- `tests/integration/cli_code` alone: 37 passed (143 s) after Task 18; the Task 19 and 21 unit tests and the Task 20/21
  live files were run separately and passed.
- After the suites: no `agentos.launcher` or background `orin-code` process is left; `git status` shows nothing under `data/`.
- `src/agentos/agentic/agent_tools.py` is 1699 lines (untouched by this batch).

Deviations from the plan (decided while implementing; the plan's code was copied verbatim otherwise):

1. **Task 18, `tests/integration/cli_code/test_headless.py`:** the plan's replacement snippet
   `    assert messages[-1]["result"] == "Done."` occurs twice in the file (the second is inside the `num_turns` assertion
   on line 62), so the Edit tool refused it. The new test was appended at the end of the file instead, with the
   preceding line (`assert "line 0000" ...`) added to the old snippet to make it unique. The test body is verbatim.
2. **Task 18, `session.py` relay:** the plan registers the SDK relay for the turn (`register_relay`) and never removes it.
   `unregister_relay` is not called anywhere in `src/` (see "Incomplete" below). Left as the plan writes it.
3. **Task 20, expected failure:** the plan expected a collection error for `test_session_subcommands.py`. Its imports
   (`main` only) succeed, so the failure is at test-body level: 4 of its 7 tests failed before the implementation, and 3
   (the parametrized `import`/`review` refusals and `--bg` without a print prompt) already passed through the existing
   refusal paths.
4. **Expectation changes (named in the plan):** Task 18 `test_turn_options_builder.py` checks `--session-mirror` instead
   of `--mcp-config`; Task 20 checks `--resume-session-at` instead of `--bg`. Both applied verbatim.
5. Parallel Edit calls were used for disjoint snippets in the same file (the calls ran in order; no snippet overlapped).

Incomplete or not verified (honest list):

- **Relay leak:** `register_relay` is called per turn with an SDK server (`session.py`, around line 3573) and nothing
  unregisters it when the turn ends. The `_RELAYS` dict keeps the closure (and so the session) for every such turn for
  the life of the process. Needs a `try/finally` with `unregister_relay(relay_key, ask)` around the turn, or at
  turn close. Not fixed here because the plan specifies this code; flagged for a follow-up.
- **Plan test precedence:** `test_subcommands_without_support_say_so` asserts `code == 1 and "not supported yet" in err
  or "W8" in err`; `and` binds tighter, so the `or` branch can pass on its own. Left as written (plan text, not weakened
  by this batch); it is weaker than it reads.
- `orin code plugin`: only `list`, `validate` and the update/marketplace-remove refusals are exercised. `install`
  (inspect + approve), `uninstall`, `enable`, `disable`, `configure`, `details` and `marketplace add|list` have no test
  against the live API.
- `orin code doctor`: only the healthy path (exit 0) is tested; the exit-1 path when a check fails is not.
- `orin code mcp serve`: tested for initialize, tools/list, a Read, a refused Write (dontAsk), an unknown tool, and the
  stdio framing. `run_command` (Bash) through the MCP server is not exercised.
- `orin code [prompt]` opens the browser through `webbrowser`; the tests point `BROWSER` at a no-op Python, so the real
  Windows browser hand-off is not verified.
- `purge --yes` is tested only for the session index file; the removal of `cli-transcripts` and `cli-host-requests` is
  not asserted.
- `auto-mode critique` (a plan-mode turn without tools) is not tested.
- `mcp login`/`logout` are refused by design (decision 33); the interface does OAuth.
- No visual check: this batch has no frontend changes.
- Windows only.

## Batch F — Inherited fixes (Tasks 22–30)

Commits on `main` after the plan commit `22d7694` (one per task, the plan's messages and trailer):

- `baa5083` `fix(w6): bound the auto-mode classifier's threads and keep its breaker per conversation` (Task 22, W1-R10)
- `737d7cd` `fix(w6): an SDK relay ends with its turn` (Task 23, Batch E's relay leak and weak test)
- `1ca8531` `fix(w6): tools that never prompt are not read-only, and plan mode refuses a wakeup` (Task 24, W4-R26)
- `4aa3b19` `fix(w6): an MCP resource blob that is not base64 is described as unreadable` (Task 25, W5-D4)
- `30f1b40` `fix(w6): a stdio MCP server's children end with it` (Task 26, W5-D2)
- `b61d1a9` `fix(w6): the LSP client matches versions, ends a malformed wait and keeps stderr's tail` (Task 27, W5-D3)
- `b529be7` `fix(w6): consolidation counters survive concurrent processes` (Task 28, W3-R23)
- `e8a6705` `feat(w6): orin code --init and --maintenance fire the Setup hooks` (Task 29)
- `9312ee0` `feat(w6): an agent file's effort and a forked skill's model apply` (Task 30, W5 deviations 4 and 9)

The executor's tree was compared file by file with the coordinator's validated replay of the plan: identical.

Tests added: `tests/unit/permissions/test_w6_auto_mode.py` (3), `tests/unit/mcp/test_sdk_relay_lifetime.py` (1),
`tests/unit/permissions/test_w6_no_prompt_tools.py` (4), `tests/unit/mcp/test_w6_resource_blobs.py` (3),
`tests/unit/mcp/test_w6_stdio_children.py` (1), `tests/unit/lsp/test_w6_lsp_fixes.py` (4),
`tests/unit/memory/test_w6_consolidation_state.py` (2, real concurrent processes), `tests/unit/hooks/test_w6_setup_hook.py` (3),
`tests/unit/agentic/test_w6_agent_effort.py` (3), `tests/unit/agentic/tools/test_w6_skill_model.py` (4). Each new file was
run red before its implementation.

Expectation changes (named in the plan): `test_push_notification.py` and `test_wakeup_tools.py` check `NO_PROMPT_TOOLS`
(the tool still never prompts); `test_session_subcommands.py` line 72 asserts the exact refusal text instead of
`... and A or B`.

Suites after Batch F (Windows, 2026-10-20, on `main` at `9312ee0`):

- `uv run pytest -q tests/unit`: 4192 passed, 65 skipped (354 s).
- `uv run pytest -q tests/integration`: 120 passed, 65 skipped (238 s).
- Linux (Docker, the validated tree of this plan): `tests/unit/{mcp,lsp,memory,permissions,hooks,cli_code}`, the agentic
  tool tests and the agent-effort test: 1315 passed, 37 skipped (the POSIX process-group and `fcntl` paths run there).
- `src/agentos/agentic/agent_tools.py`: 1699 lines; nothing written under `data/`.
- The batch close was finished by the coordinator: the executor handed back before the full suites ended and without
  the ledger.

Deviations from the plan (executor's report, checked by the coordinator):

1. **Task 30 RED:** two of the four skill-model tests (a refused model runs on the parent's; no model asks nothing) already
   passed before the implementation, because the tool passed `model_id=None` anyway; the plan said three would fail.
   The allowed-model test and the session test failed as expected, and so did the agent-effort test.
2. **Task 26:** the Job's `assign(pid)` result is not checked; when the assignment fails, the descendant snapshot taken
   while the server runs is the only cover (as for a Store Python, which leaves the Job).
3. **Task 23:** `_sdk_relay` is read with `getattr(..., None)` and is not initialised in `__init__` (as the plan writes it).
4. The executor reported that Task 25's `_blob_size` was placed before `_listing` rather than at a different anchor; the
   committed file is identical to the plan's result.

Decisions recorded with this batch (the plan's 42–50): see the plan's "Deviations decided while planning Batch F".

Not done or not verified (honest gaps):

- **`code_mode/permissions.py` still has its own `READ_ONLY_TOOLS`** with `ask_user` and `use_skill`. Nothing reads it
  (only `__all__` exports it); it should be removed or aligned with the engine's lists. Not changed.
- **LSP `close()` never closes the server's stdin**; a failed start logs an unraisable `BrokenPipe`/`ResourceWarning`
  in tests (pre-existing; the tests pass).
- **W5-D2 on Windows:** the developer machine's Python is the Microsoft Store build, which leaves Job Objects, so the
  Windows test passes through the descendant snapshot, not through the Job. The Job path is exercised only where Python
  is a normal install (CI).
- **W3-R31** (earlier turns replayed without their thinking blocks against the real Anthropic API): not verified; no
  Anthropic key in this environment.
- Frontend: not touched; not run.

## W6 summary

Against spec §9 (each point names the batch that delivered it; "gap" is what is not there):

- **§9.1 architecture — delivered.** A thin client in `src/agentos/cli_code/` (Batch A): it starts the local runtime when
  needed, authenticates with a per-run token in `ORIN_RUN_DIR` (owner-only: `icacls` with the user's SID on Windows,
  mode 0600 on POSIX) and refuses the SERVER profile (remote is W8).
- **§9.2 surface — mostly delivered.** `orin code -p` with the output and input formats, sessions (`-c`, `-r`,
  `--session-id`, `-n`, `--no-session-persistence`, `--bg`), model, permission and configuration flags (`--mcp-config`,
  `--strict-mcp-config`, `--settings`, `--setting-sources`, `--agents`, `--permission-prompt-tool stdio`,
  `--permission-prompts host|none`), prompt flags, `--json-schema` with 3 retries, `--max-turns`, `--max-budget-usd`,
  `--autocompact`, `--init`/`--maintenance` (Setup, Batch F), debug flags (Batches B, C, E, F); `orin code [prompt]`
  opens the session in the interface (Batch E); the subcommands `mcp`, `plugin`, `agents`, `logs`, `stop`, `rm`,
  `attach`, `doctor`, `auto-mode`, `purge` (Batch E); the excluded flags and subcommands are refused; exit codes 0/1.
  **Gaps (refused, never ignored — plan decision 49):** `--fork-session`, `-w/--worktree`, `--agent`, `--plugin-dir`,
  `--exclude-dynamic-system-prompt-sections`, `--prompt-suggestions`, `--bare`, `--safe-mode`, `--restricted`,
  `--desktop`, `--resume-session-at`, `--resume-drops-turn`, `--session-mirror`, `--task-budget`, `--thinking-display`;
  `import` and `review` (W8); `plugin eval` (W8); `plugin update` and `marketplace remove|update` point to the interface;
  `mcp login|logout` point to the interface; `attach` follows the log and takes no stdin messages (decision 38).
- **§9.3 stream-json — delivered with gaps.** Output: `system/init`, `assistant`, `user`, `stream_event` in Anthropic's
  SSE shape for every provider, `system/compact_boundary`, hook events with `--include-hook-events`, `result` with
  `success`, `error_max_turns`, `error_during_execution`, `error_max_budget_usd`, `error_max_structured_output_retries`
  and the listed fields (Batches B, C). Host → harness: `initialize` (hooks, agents, SDK MCP servers), `interrupt`,
  `set_permission_mode`, `set_model`, `set_max_thinking_tokens` (these three apply from the next turn, decision 25),
  `rewind_files` (code only), `mcp_status`. Harness → host: `can_use_tool` (allow once; a changed `updatedInput` is
  refused; `updatedPermissions` are not persisted), `hook_callback`, `mcp_message` (Batches C, E).
  **Gaps:** `system/init` always reports empty `mcp_servers` and `slash_commands`; `system/microcompact_boundary`,
  `task_started`, `task_progress` and `task_notification` are not emitted.
- **§9.4 conformance — met.** The official `claude-agent-sdk==0.2.159` (dev dependency, unmodified) drives `orin-code`
  through `cli_path` on the fake provider, offline: simple query, streaming, `can_use_tool`, SDK hooks, interrupt,
  resume (Batch D), plus an SDK MCP server through `mcp_message` (Batch E).

Inherited items:

| Item | Where it was closed, or why it remains |
| --- | --- |
| W1-R10 classifier threads, per-turn breaker, `autoMode.model` | Batch F Task 22 |
| W3-R24 `--permission-prompts none` → W1-R9 | Batch B (the session applies `permission_prompts: none`) |
| W3-R23 consolidation state race | Batch F Task 28 |
| W3-R25 `--json-schema` with retries | Batch B Task 11 |
| W3-R31 thinking replay against the real API | **not verified** (no Anthropic key) |
| W0 ledger: Setup hook event | Batch F Task 29 (`--init`, `--maintenance`) |
| W2 ledger: SDK hook callbacks | Batch C Task 14 |
| W5 deviation 4: skill `model`/`effort` | **partly**: a forked skill's `model` (Task 30); an inline skill's `model` and any skill's `effort` remain (W8) |
| W5 deviation 9: agent fields | **partly**: `effort` (Task 30); `isolation`, `mcpServers`, `memory`, `color`, `hooks` remain (W8) |
| W5-D2 MCP stdio children | Batch F Task 26 |
| W5-D3 LSP version, malformed publish, stderr | Batch F Task 27 (a publish without `version` is still accepted) |
| W5-D4 invalid base64 blob | Batch F Task 25 |
| W4-R26 read-only vs never-prompt tools | Batch F Task 24 (the unused `code_mode/permissions.py` list is still wrong) |

Platform coverage: every batch ran on Windows; the coordinator ran the changed areas on Linux (Docker) before each
push. The 65 skipped tests need Postgres/Redis and were not run.

## Review fixes (W6 review)

Part 1 of the fixes to `docs/superpowers/reviews/2026-10-20-w6-review.md`: W6-R1, W6-R2 and W6-R3. Each finding was
written test first; the commit message names the finding.

| Finding | Commits | Tests | Decisions, deviations and what remains |
| --- | --- | --- | --- |
| W6-R1 permission for the wrong call | `d1ea356` | `tests/unit/cli_code/test_w6_r1_permission_call.py` (7), `tests/unit/permissions/test_w6_r1_invocation_id.py` (3) | The invocation id travels in the broker's record and event through `agentos/agentic/call_context.py` (a thread-local, set by the toolset's `ActiveInvocation` mixin, which replaces the property in `agent_tools.py`; that file went from 1699 to 1693 lines). `Translator.open_call(invocation, internal)` replaces `open_tool`, which had no other user. A call that is not open (a subagent's) is shown from the broker's own `command` or `path`; with neither, the request is denied without asking the host. |
| W6-R2 stopping `orin code` stops its turn | `2d937aa`, `bbd17b3` | Unit: `tests/unit/cli_code/test_w6_r2_cancel_on_exit.py` (9), `test_w6_r2_session_lease.py` (5), `tests/unit/agentic/test_w6_r2_cli_lease.py` (14), `tests/unit/api/test_w6_r2_cli_lease_route.py` (3), `tests/unit/conversations/test_w6_r2_turn_lease.py` (2), `tests/unit/workers/test_w6_r2_lease_drop.py` (3). Live: `tests/integration/cli_code/test_w6_r2_lease_live.py` (kills the process 2 s into a 120-word slow answer; the turn must end `cancelled` and the answer must not reach `word119`). `bbd17b3` classifies the new route in `test_profile_isolation.py`, which the route added to the suite broke. | See the list below. |

W6-R2 decisions and deviations:
- The lease is renewed where the CLI turn is queued (`conversations/chat.py`, before the dispatch row is committed), not in the gateway's create and send routes. The worker can claim a turn as soon as the transaction commits, and a missing lease reads as lost, so a renewal after the response could race the worker. The gateway route only renews.
- The lease route checks that the turn is in the conversation's `turns` list (the conversation read). A turn that is not among the turns that read returns gets 404 from the heartbeat; the heartbeat is for the current turn, which is recent.
- The worker wraps `cancelled` for CLI turns (`agentic/session.py`). It records one notice ("orin code stopped following this turn") with the existing `TURN_STARTED` activity type, the precedent the session already uses for notices. The lease file is dropped when the turn closes, however it closes (`workers/chat.py`, `_run_claimed` now wraps `_run_claimed_turn`).
- A pending permission or host request ends at its next poll after the cancel (`request_and_wait` and `HostRequests.ask` both check `cancelled`). It is not answered with an immediate deny; a wait lasts at most one poll interval after the lease expires.
- Windows: `SIGBREAK` goes through the same exit path (`signal`), and SIGTERM does too. A closed console window (CTRL_CLOSE) cannot be caught from Python; the lease covers it. The console control handler the spec allowed was not written.
- `tests/fixtures/fake_ollama.py`: `[[slow:<n>]]` sets the word count. The default stays 40 words (10 s), which finishes before a 15 s lease could end it, so the live test needs 120.
- TDD order, honestly: the `cli_lease` module, the chat renewal and the worker's drop were written before their tests, so those tests passed on first run and did not fail first. The other R2 tests were run red first.

W6-R3 decisions and deviations:
- `start_background` records `created` (`process_created`: Windows `GetProcessTimes` on the child's handle; POSIX `/proc/<pid>/stat` field 22, or `ps -o lstart=`). `background_runs`, `attach`/`logs -f`, `stop`, `rm` and `purge` use `_same_process`. A record without `created`, or whose time cannot be read, is finished and kills nothing. That also covers every run recorded before this fix.
- Windows kills through a handle opened and validated by creation time, with its validated descendants (`_windows_descendants` with an object that has `pid` and `_handle`). POSIX sends SIGKILL to the process group only after the check.
- Not done: the review's suggested `ended_at` written by the child when it exits. The creation-time check already stops a reused pid from being touched; a finished run is reported as finished without it.
- Live: `tests/integration/cli_code/test_info_live.py::test_stopping_a_background_run_cancels_its_turn` now asserts the run's process is gone after `stop`.

Suites at the end of part 1 (`uv run pytest -q`): unit `4243 passed, 65 skipped`; integration `121 passed, 65 skipped` (after `bbd17b3`; the first integration run had one failure, the new route missing from the isolation test's classification, fixed in `bbd17b3`). `src/agentos/agentic/agent_tools.py` is 1693 lines. No `agentos.launcher` or `orin-code` process was left running.

Not in this part: W6-R4 to W6-R17, listed in the review's findings table.
