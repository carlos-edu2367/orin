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
