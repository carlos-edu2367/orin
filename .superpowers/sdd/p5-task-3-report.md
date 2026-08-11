# Plan 5 Agent Capabilities — Task 3 Report

## Outcome

Implemented the declarative policy gate for the agent-facing tool catalog.
The chat path now accepts an optional policy, attaches coarse policy tags to
the specified tools, filters denied tools out of the published definitions,
and retains the existing unknown-tool failure behavior if a model calls a
filtered name anyway.

The existing `tool_runtime` path was not changed or introduced into
`AgentToolset.invoke`. Tool results therefore continue to cross the
agent-facing chat boundary as readable, bounded content, while the separate
tool-runtime provider projection remains unchanged.

## Changed files

- `src/agentos/agentic/tool_policy.py`
  - Added the `ToolPolicy` protocol with
    `allows(name: str, tags: tuple[str, ...]) -> bool`.
  - Added frozen, slotted `AllowList`.
  - `allowed=None` permits every tool not matched by `denied`.
  - `name` entries match tool names; `tag:<value>` entries match policy tags.
  - Explicit denial takes precedence over allowance.
- `src/agentos/agentic/agent_tools.py`
  - Added `ToolDefinition.policy_tags`, defaulting to `()`.
  - Tagged `write_file`, `edit_file`, `run_command`, `remember`,
    `create_agent`, and `ask_agent` as `mutates`.
  - Tagged `fetch_url`, `web_search`, and `browse_page` as `network`.
  - Added optional policy filtering to the cached definition catalog.
  - Filtered tools are absent from `definitions()`, `schemas()`, and name
    resolution, so invocation follows the established `UNKNOWN_TOOL` result.
- `src/agentos/agentic/session.py`
  - Added optional `tool_policy` storage to `TurnSession`.
  - Passed it to the per-turn `AgentToolset`.
- `tests/unit/agentic/test_tool_policy.py`
  - Added the five acceptance tests from the Task 3 brief.
- `.superpowers/sdd/p5-task-3-report.md`
  - Added this implementation and verification report.

All changed Python modules begin with `from __future__ import annotations`.

## Verification

- `uv run pytest tests/unit/agentic/test_tool_policy.py -v`
  - 5 passed.
- `uv run pytest tests/unit/agentic -v`
  - 159 passed, 2 skipped.
- `uv run pytest tests/unit/agentic tests/unit/workers tests/unit/tool_runtime -v`
  - 185 passed, 2 skipped.
- `git diff --check`
  - Passed.
- `git grep -n "AGENTOS_SEARCH_API_KEY" -- src`
  - The key is referenced only by `src/agentos/agentic/web_search.py`.
- Existing boundary tests confirm that `web_search` is absent without a
  configured client and `browse_page` is absent without a browser.

The initial `uv run pytest tests/unit -q` collection is blocked by the
repository's pre-existing duplicate bare test module name
(`provider_catalog/test_service.py` versus `skills/test_service.py`). The
alternative `--import-mode=importlib` run exposes four other pre-existing
tests that use bare intra-directory imports, so neither full-suite command
provides a clean repository-wide signal. The requested affected suites above
ran cleanly.

## Brief discrepancy

The brief's interface summary says a denied invocation should return
`TOOL_NOT_AUTHORIZED`, but its supplied acceptance test expects
`UNKNOWN_TOOL`, and Step 4 explicitly says filtered tools will use the
existing `UNKNOWN_TOOL` conversion. This implementation follows the concrete
acceptance test and detailed Step 4 behavior, preserving the existing public
unknown-tool contract. No separate authorization error code was introduced.

## Scope and boundary review

- No provider boundary or `tool_runtime` module was changed.
- No routing through `ToolRuntime.invoke` was added.
- Orin public copy was preserved.
- Existing network restrictions and secret redaction behavior were preserved.
- Changes are limited to the Task 3 policy module, agent tool/session wiring,
  acceptance tests, and this report.
