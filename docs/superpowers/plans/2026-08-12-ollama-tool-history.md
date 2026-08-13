# Ollama Native Tool History Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt only the Ollama native request payload so multi-step tool loops work after the first tool result.

**Architecture:** Keep the runtime's internal message representation unchanged. Add a provider-boundary conversion in `HTTPProviderStreamTransport._ollama_request` that turns OpenAI-style assistant tool history into Ollama-native assistant/tool messages before serialization.

**Tech Stack:** Python 3, httpx, pytest, existing `NormalizedStreamItem`/NDJSON transport.

## Global Constraints

- Preserve unrelated working-tree changes and stage only files belonging to this fix.
- Keep OpenRouter, OpenAI, Anthropic and OmniRoute payload behavior unchanged.
- Do not expose credentials or raw upstream error bodies.
- Preserve existing multi-tenant, authorization, limits and tool validation behavior.

---

### Task 1: Normalize Ollama tool-call history at the provider boundary

**Files:**
- Modify: `src/agentos/agentic/provider_stream.py:378-399`
- Test: `tests/integration/agentic/test_provider_tool_loop.py`
- Test: `tests/unit/agentic/test_provider_stream_payload.py`

**Interfaces:**
- Consumes: the existing `messages` list passed into `_ollama_request`.
- Produces: Ollama-native messages with structured function arguments and `tool_name` tool results.

- [ ] **Step 1: Write the failing regression test**

Add a two-request Ollama MockTransport case. The first response emits one native NDJSON tool call. The second request assertion must require:

```python
assistant_call = second_payload["messages"][1]["tool_calls"][0]
assert assistant_call["function"]["arguments"] == {"path": "a.txt"}
assert second_payload["messages"][2] == {
    "role": "tool",
    "tool_name": "read_file",
    "content": "file contents",
}
```

The current implementation must fail this test because it sends a string argument and `tool_call_id`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -q tests/integration/agentic/test_provider_tool_loop.py -k ollama
```

Expected: failure in the new second-request payload assertion, before any production change.

- [ ] **Step 3: Implement the minimal provider-boundary conversion**

Add a private helper used only by `_ollama_request` that:

1. clones the message list rather than mutating runtime history;
2. converts each assistant tool call's string JSON arguments with `json.loads`, retaining `{}` for invalid/non-object values;
3. records each call id to its function name while walking messages in order;
4. converts each `role=tool` message to `tool_name` using that map and keeps its content;
5. leaves ordinary messages and already-structured arguments unchanged.

Apply the helper to the `messages` value only in `_ollama_request`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -q tests/integration/agentic/test_provider_tool_loop.py tests/unit/agentic/test_provider_stream_payload.py
```

Expected: all focused tests pass, including existing OpenRouter and Ollama payload tests.

- [ ] **Step 5: Run broader verification**

Run:

```powershell
& '.venv\Scripts\python.exe' -m pytest -q tests/unit/agentic tests/integration/agentic
& '.venv\Scripts\python.exe' -m compileall -q src tests
git diff --check
```

Expected: exit code 0 for all commands and no whitespace errors.

- [ ] **Step 6: Save technical memory and review scope**

Create `docs/agent_memory/2026-08-12-ollama-native-tool-history.md` describing the Ollama-native history contract and the provider-boundary fix. Review `git diff` and confirm unrelated existing modifications are not staged.

- [ ] **Step 7: Commit only the fix**

```powershell
git add docs/superpowers/specs/2026-08-12-ollama-tool-history-design.md docs/superpowers/plans/2026-08-12-ollama-tool-history.md docs/agent_memory/2026-08-12-ollama-native-tool-history.md src/agentos/agentic/provider_stream.py tests/integration/agentic/test_provider_tool_loop.py tests/unit/agentic/test_provider_stream_payload.py
git commit -m "fix(ollama): normalize native tool history"
```

- [ ] **Step 8: Publish the committed fix on `main`**

Confirm the commit is on `main`, then run:

```powershell
git push origin main
```

Report the resulting commit and push status only after fresh command output confirms success.
