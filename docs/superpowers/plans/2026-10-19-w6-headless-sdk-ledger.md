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
