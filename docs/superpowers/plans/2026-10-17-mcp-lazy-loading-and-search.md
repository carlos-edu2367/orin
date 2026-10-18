# MCP Lazy Loading, Contextual/Semantic Tool Search & Unlimited Server Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate eager MCP schema context bloat by lazy-loading remote MCP tools on demand via `search_mcp_tools`, `inspect_mcp_server`, and `call_mcp_tool`, remove the artificial 256-tool-per-server cap, and surface upstream HTTP status/provider metadata in OpenRouter media generation errors.

**Architecture:**
1. Remove the `MAX_TOOLS_PER_SERVER` batch rejection in `src/agentos/mcp/sanitize.py` and replace the 8-page cap in `src/agentos/mcp/client.py` with cursor-cycle detection (`seen_cursors`) and a 1000-page ceiling.
2. Add `src/agentos/mcp/search.py` to index and search MCP tool descriptors across tool names, descriptions, and JSON `input_schema` fields (property names, descriptions, titles, enums, required parameters) with camelCase/snake_case splitting, PT/EN bilingual synonym expansion, and optional semantic scoring. Expose server/descriptor metadata on `McpToolProvider` (`src/agentos/mcp/toolset.py`).
3. Keep `mcp__<slug>__<tool>` definitions registered in `AgentToolset.definitions()` (for direct dispatch), but filter `AgentToolset.schemas()` and `AgenticTurnRuntime._tool_schemas()` so `mcp__<slug>__<tool>` schemas are only sent to the LLM once activated in `toolset.active_mcp_tool_names`. Publish 3 native MCP meta-tools (`search_mcp_tools`, `inspect_mcp_server`, `call_mcp_tool`) and inject a compact summary of connected MCP servers into the system prompt.
4. Enhance `MediaGenerationClient._extract_error` (`src/agentos/agentic/media_client.py`) to include `HTTP <status_code>` and OpenRouter `error.metadata` (`provider_name` and `raw`).

**Tech Stack:** Python 3.12+, `pytest`, `httpx`, SQLAlchemy (in-memory SQLite for integration tests).

---

## File Structure

- **Modify:** `src/agentos/mcp/sanitize.py` — Remove `MAX_TOOLS_PER_SERVER` batch rejection while keeping per-tool schema validation.
- **Modify:** `src/agentos/mcp/client.py` — Increase `MAX_TOOL_PAGES` to `1000` and stop pagination on repeated `nextCursor`.
- **Create:** `src/agentos/mcp/search.py` — Contextual, schema-aware, PT/EN bilingual, and optional semantic search engine for MCP tools.
- **Modify:** `src/agentos/mcp/toolset.py` — Expose server summaries and tool descriptor lookup (`server_summaries()`, `server_tools()`, `all_descriptors()`) on `McpToolProvider`.
- **Modify:** `src/agentos/agentic/agent_tools.py` — Track `_active_mcp_tool_names`, filter unactivated `mcp__*` tools out of `schemas()`, and implement `search_mcp_tools`, `inspect_mcp_server`, and `call_mcp_tool`.
- **Modify:** `src/agentos/agentic/runtime.py` — Include `search_mcp_tools`, `inspect_mcp_server`, `call_mcp_tool` in `CODE_MODE_CORE_TOOLS` and only publish activated `mcp__*` tools in Code Mode.
- **Modify:** `src/agentos/agentic/phases.py` — Add `search_mcp_tools` and `inspect_mcp_server` to `Phase.PLAN` and update `Phase.ORIENT` prompt instructions for lazy-loaded MCP tools.
- **Modify:** `src/agentos/agentic/prompts.py` & `src/agentos/agentic/session.py` — Inject compact connected MCP server summary into `volatile` system prompt and subagent prompts.
- **Modify:** `src/agentos/agentic/media_client.py` — Include HTTP status code and OpenRouter `error.metadata` (`provider_name`, `raw`) in `_extract_error`.
- **Modify / Create Tests:**
  - `tests/unit/mcp/test_sanitize.py`
  - `tests/unit/mcp/test_client.py`
  - `tests/unit/mcp/test_search.py` (new)
  - `tests/unit/agentic/test_agent_tools_mcp.py`
  - `tests/unit/agentic/test_media_generation_tools.py`

---

### Task 1: Remove MCP Tool Count Limit & Harden Pagination

**Files:**
- Modify: `src/agentos/mcp/sanitize.py`
- Modify: `src/agentos/mcp/client.py`
- Test: `tests/unit/mcp/test_sanitize.py`
- Test: `tests/unit/mcp/test_client.py`

- [ ] **Step 1: Write failing tests in `tests/unit/mcp/test_sanitize.py` and `tests/unit/mcp/test_client.py`**

In `tests/unit/mcp/test_sanitize.py`, replace `test_the_descriptor_batch_is_bounded` with a test verifying that large batches (e.g. 500 tools) are accepted without raising `UntrustedDescriptorRejected`, while malformed entries in the same batch are still individually dropped:

```python
def test_large_descriptor_batches_are_accepted_without_artificial_cap():
    payload = [
        {"name": f"tool_{index}", "description": f"Tool {index}", "inputSchema": {"type": "object"}}
        for index in range(500)
    ]
    payload.append({"name": "bad tool name!", "description": "dropped", "inputSchema": {"type": "object"}})
    sanitized = sanitize_tool_descriptors(payload)
    assert len(sanitized) == 500
    assert sanitized[0].name == "tool_0"
    assert sanitized[-1].name == "tool_499"
```

In `tests/unit/mcp/test_client.py`, add tests for multi-page pagination (> 8 pages) and cursor-cycle protection:

```python
def test_list_tools_paginates_beyond_eight_pages():
    class PaginatedTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__({})
            self.page = 0

        def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
            self.sent.append(dict(frame))
            if "id" not in frame:
                return None
            if frame["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": frame["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "demo"}, "capabilities": {"tools": {}}}}
            self.page += 1
            next_cursor = f"cursor_{self.page}" if self.page < 12 else None
            return {
                "jsonrpc": "2.0",
                "id": frame["id"],
                "result": {
                    "tools": [{"name": f"t_{self.page}", "description": "d", "inputSchema": {"type": "object"}}],
                    **({"nextCursor": next_cursor} if next_cursor else {}),
                },
            }

    client = McpClient(PaginatedTransport())
    client.initialize()
    tools = client.list_tools()
    assert len(tools) == 12
    assert [t.name for t in tools] == [f"t_{i}" for i in range(1, 13)]


def test_list_tools_stops_on_repeating_cursor_cycle():
    class CyclingTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__({})
            self.calls = 0

        def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
            self.sent.append(dict(frame))
            if "id" not in frame:
                return None
            if frame["method"] == "initialize":
                return {"jsonrpc": "2.0", "id": frame["id"], "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "demo"}, "capabilities": {"tools": {}}}}
            self.calls += 1
            return {
                "jsonrpc": "2.0",
                "id": frame["id"],
                "result": {
                    "tools": [{"name": f"t_{self.calls}", "description": "d", "inputSchema": {"type": "object"}}],
                    "nextCursor": "same_cursor",
                },
            }

    transport = CyclingTransport()
    client = McpClient(transport)
    client.initialize()
    tools = client.list_tools()
    assert len(tools) == 2
    assert transport.calls == 2
```

- [ ] **Step 2: Run pytest to verify the new tests fail (RED)**

Run: `pytest tests/unit/mcp/test_sanitize.py tests/unit/mcp/test_client.py -q`
Expected: FAIL (`test_large_descriptor_batches_are_accepted_without_artificial_cap` fails with `UntrustedDescriptorRejected: Server advertised 501 tools (limit 256)`; `test_list_tools_paginates_beyond_eight_pages` returns 8 tools instead of 12; `test_list_tools_stops_on_repeating_cursor_cycle` loops 8 times instead of 2).

- [ ] **Step 3: Implement changes in `src/agentos/mcp/sanitize.py` and `src/agentos/mcp/client.py` (GREEN)**

In `src/agentos/mcp/sanitize.py`:
- Remove the `if len(items) > MAX_TOOLS_PER_SERVER: raise UntrustedDescriptorRejected(...)` check from `sanitize_tool_descriptors`.

In `src/agentos/mcp/client.py`:
- Update `MAX_TOOL_PAGES = 1000`.
- In `McpClient.list_tools`, track `seen_cursors: set[str] = set()` and break if `not isinstance(cursor, str) or not cursor or cursor in seen_cursors`, adding `seen_cursors.add(cursor)` before continuing.

- [ ] **Step 4: Run pytest to verify tests pass**

Run: `pytest tests/unit/mcp/test_sanitize.py tests/unit/mcp/test_client.py -q`
Expected: PASS.

---

### Task 2: Contextual, Schema-Aware & Semantic MCP Tool Search Engine

**Files:**
- Create: `src/agentos/mcp/search.py`
- Modify: `src/agentos/mcp/toolset.py`
- Test: `tests/unit/mcp/test_search.py`
- Test: `tests/unit/mcp/test_toolset.py`

- [ ] **Step 1: Write failing tests in `tests/unit/mcp/test_search.py` and `tests/unit/mcp/test_toolset.py` (RED)**

Create `tests/unit/mcp/test_search.py` covering:
1. Searching by tool name (`get_funnel_metrics`, camelCase/snake_case splitting).
2. Searching by description text.
3. Searching by JSON `input_schema` property names (`step_id`, `redirect_slug`), property descriptions, and `enum` values (`"quiz_single_choice"`).
4. Bilingual PT/EN & accent-insensitive matching (`"métricas do funil"` matching `get_funnel_metrics`, `"criar página"` matching `create_page`).
5. Filtering by `server` slug or display name.
6. Optional `semantic_scorer` boosting and graceful fallback if `semantic_scorer` raises an exception.
7. `McpToolProvider.server_summaries()` and `McpToolProvider.server_tools()` in `tests/unit/mcp/test_toolset.py`.

- [ ] **Step 2: Run pytest to verify the new tests fail (RED)**

Run: `pytest tests/unit/mcp/test_search.py tests/unit/mcp/test_toolset.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'agentos.mcp.search'`).

- [ ] **Step 3: Implement `src/agentos/mcp/search.py` and helper methods on `McpToolProvider` in `src/agentos/mcp/toolset.py` (GREEN)**

In `src/agentos/mcp/search.py`:
- Define `McpToolSearchDocument`:
  - `server_slug: str`
  - `server_name: str`
  - `tool_name: str`
  - `qualified_name: str` (`mcp__<slug>__<name>`)
  - `description: str`
  - `input_schema: Mapping[str, Any]`
  - `parameter_names: tuple[str, ...]`
  - `schema_text: str` (extracted recursively from `properties`, `required`, `description`, `title`, `enum`, `items`, `anyOf`, `oneOf`, bounded to a safe recursion depth).
- Implement identifier splitting (`camelCase`, `snake_case`, `kebab-case`), Unicode accent folding (`NFKD`), PT↔EN domain synonym expansion (`metrica`/`metrics`, `funil`/`funnel`, `etapa`/`step`, `criar`/`create`, `listar`/`list`, `buscar`/`search`/`find`, `atualizar`/`update`/`edit`, `excluir`/`deletar`/`delete`/`remove`, `salvar`/`save`, `rascunho`/`draft`, `pagina`/`page`, `usuario`/`user`, `configuracao`/`config`/`settings`, `arquivo`/`file`, `imagem`/`image`, `audio`/`voice`, `video`/`clip`, `relatorio`/`report`, `leads`/`lead`, `dominio`/`domain`), and weighted field scoring:
  - Exact tool name / qualified name match: +40
  - Substring match in tool name: +18
  - Name token overlap / stem match: +10 per token
  - `input_schema` parameter name overlap / stem match: +7 per token
  - Description token overlap / stem match: +4 per token
  - `input_schema` description / enum token overlap: +3 per token
  - Server slug / display name match: +5 per token
  - Optional `semantic_scorer(query, doc)` bonus: `+10 * max(0.0, min(1.0, score))` wrapped in `try...except Exception`.

In `src/agentos/mcp/toolset.py`:
- Store server metadata and descriptors on `McpToolProvider` so callers can query:
  - `server_summaries() -> tuple[dict[str, Any], ...]`: returns `({"slug": ..., "display_name": ..., "tool_count": ...}, ...)` for active servers with enabled tools.
  - `search_tools(query: str = "", *, server: str | None = None, limit: int = 10, semantic_scorer=None) -> list[McpToolSearchResult]`
  - `inspect_server(server: str, *, include_schemas: bool = False, offset: int = 0, limit: int = 200) -> dict[str, Any] | None`
- Also support duck-typed `mcp_provider` objects in `AgentToolset` (such as `FakeProvider` in unit tests that only expose `.definitions()`) by synthesizing `McpToolSearchDocument` items from `self._mcp_provider.definitions()` whenever `self._mcp_provider` does not implement `server_summaries` / `search_tools` directly!

- [ ] **Step 4: Run pytest to verify all search and toolset tests pass**

Run: `pytest tests/unit/mcp/test_search.py tests/unit/mcp/test_toolset.py -q`
Expected: PASS.

---

### Task 3: Lazy-Load MCP Schemas & Add `search_mcp_tools`, `inspect_mcp_server`, `call_mcp_tool`

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/runtime.py`
- Modify: `src/agentos/agentic/phases.py`
- Modify: `src/agentos/agentic/prompts.py`
- Modify: `src/agentos/agentic/session.py`
- Test: `tests/unit/agentic/test_agent_tools_mcp.py`

- [ ] **Step 1: Write failing tests in `tests/unit/agentic/test_agent_tools_mcp.py` (RED)**

Add unit and adversarial tests to `tests/unit/agentic/test_agent_tools_mcp.py`:
1. `test_mcp_remote_tool_schemas_are_lazy_loaded_by_default`:
   - Build `AgentToolset` with an `McpToolProvider` having 120 tools.
   - Verify `toolset.definitions()` contains all 120 `mcp__auryly__*` definitions (so direct invocation works), **but** `toolset.schemas()` and `toolset.schemas(allowed=tools_for(Phase.ORIENT), kinds=kinds_for(None, phase=Phase.ORIENT))` do **not** include any `mcp__auryly__*` schemas initially; instead they publish `search_mcp_tools`, `inspect_mcp_server`, and `call_mcp_tool`.
2. `test_search_mcp_tools_finds_by_schema_and_activates_tools_in_current_turn`:
   - Call `toolset.invoke("search_mcp_tools", {"query": "step_id quiz_single_choice"})`.
   - Verify it returns the matching tool with its full `input_schema` and automatically activates that tool so subsequent `toolset.schemas()` calls include `mcp__auryly__save_funnel_draft` (and only the activated tool, not the other 119 tools).
3. `test_inspect_mcp_server_returns_all_server_tools_and_optionally_activates_them`:
   - Call `toolset.invoke("inspect_mcp_server", {"server": "auryly", "include_schemas": True, "activate_all": False})`.
   - Verify it returns server metadata and tool list with schemas without activating all tools in `schemas()`, and when `activate_all=True` is passed, activates them.
4. `test_call_mcp_tool_invokes_remote_tool_directly_and_respects_read_only_mode`:
   - Call `toolset.invoke("call_mcp_tool", {"server": "auryly", "tool": "get_lesson", "arguments": {"id": "1"}})` and verify it dispatches to the underlying `mcp__auryly__get_lesson` definition.
   - Verify that in `read_only_mode=True` (`explore`/`plan` subagents), `search_mcp_tools` and `inspect_mcp_server` are available, while `call_mcp_tool` and `mcp__*` are blocked/excluded.
5. `test_system_prompt_includes_compact_connected_mcp_summary`:
   - Verify `build_system_prompt(..., mcp_servers=({"slug": "auryly", "display_name": "Auryly", "tool_count": 117},))` includes the connected MCP server summary and instructions for `search_mcp_tools`, `inspect_mcp_server`, and `call_mcp_tool`.

- [ ] **Step 2: Run pytest to verify the new tests fail (RED)**

Run: `pytest tests/unit/agentic/test_agent_tools_mcp.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement lazy-loading and the 3 MCP meta-tools across `agent_tools.py`, `runtime.py`, `phases.py`, `prompts.py`, and `session.py` (GREEN)**

1. In `src/agentos/agentic/agent_tools.py`:
   - Initialize `self._active_mcp_tool_names: set[str] = set()` on `AgentToolset`, with a public property `active_mcp_tool_names -> frozenset[str]`.
   - When `self._mcp_provider is not None`, register:
     - `search_mcp_tools` (`kind="mcp"`, `read_only=True`, `policy_tags=("mcp",)`): searches across active MCP tools (using `McpToolSearchEngine`), returns matching tools with full `input_schema`, and if `activate=True` (and not `self._read_only_mode`), adds their `qualified_name` to `self._active_mcp_tool_names`.
     - `inspect_mcp_server` (`kind="mcp"`, `read_only=True`, `policy_tags=("mcp",)`): returns full server details and paginated tool list (`offset=0`, `limit=200`, max `500`), including `input_schema` when `include_schemas=True`, and adding to `self._active_mcp_tool_names` when `activate_all=True` (and not `self._read_only_mode`).
     - `call_mcp_tool` (`kind="mcp"`, `read_only=False`, `policy_tags=("mcp", "mutates")`): resolves `mcp__<slug>__<tool>` (or accepts a already-qualified `mcp__...` name) and invokes it through `self.invoke(qualified_name, arguments)` so permission broker, policy, truncation, and error handling are 100% unified.
   - In `AgentToolset.schemas(allowed=None, kinds=None)`:
     - Helper `_is_schema_published(item: ToolDefinition) -> bool`: if `item.kind == "mcp"` and `item.name.startswith("mcp__")`, return `item.name in self._active_mcp_tool_names`; otherwise `True`.
     - Apply `_is_schema_published(item)` in both branches of `schemas()`.
2. In `src/agentos/agentic/runtime.py`:
   - Add `"search_mcp_tools"`, `"inspect_mcp_server"`, `"call_mcp_tool"` to `CODE_MODE_CORE_TOOLS`.
   - In `AgenticTurnRuntime._tool_schemas()`, replace the loop that eagerly added every `item.kind == "mcp"` tool to `allowed` with adding only `{"search_mcp_tools", "inspect_mcp_server", "call_mcp_tool"}` plus `getattr(self.toolset, "active_mcp_tool_names", ())`.
3. In `src/agentos/agentic/phases.py`:
   - Add `"search_mcp_tools"` and `"inspect_mcp_server"` to `PHASE_TOOLS[Phase.PLAN]` (which goes from 27 to 29 tools, keeping `ORIENT` and `EXECUTE` at 36 tools while publishing the 3 MCP tools via `kinds_for` `"mcp"`).
   - Update `Phase.ORIENT` prompt guidance in `phases.py` and `build_system_prompt` in `session.py` / `prompts.py` to explain that connected MCP server tools are lazy-loaded via `search_mcp_tools`, `inspect_mcp_server`, and `call_mcp_tool`.
4. In `src/agentos/agentic/session.py`:
   - Pass `mcp_servers=toolset.mcp_server_summaries()` into `build_system_prompt(...)` and include the compact `## Connected MCP servers (lazy-loaded)` section in `volatile` and `_subagent_prompt()` whenever active MCP servers are present.

- [ ] **Step 4: Run pytest on MCP and phase/runtime test suites**

Run: `pytest tests/unit/agentic/test_agent_tools_mcp.py tests/unit/agentic/test_phase_controller.py tests/unit/agentic/test_phase_runtime.py tests/unit/agentic/test_extension_tools_exposure.py tests/integration/test_mcp_end_to_end.py -q`
Expected: PASS.

---

### Task 4: Surface Upstream OpenRouter Error Details in `MediaGenerationClient`

**Files:**
- Modify: `src/agentos/agentic/media_client.py`
- Test: `tests/unit/agentic/test_media_generation_tools.py`

- [ ] **Step 1: Write failing test in `tests/unit/agentic/test_media_generation_tools.py` (RED)**

View `tests/unit/agentic/test_media_generation_tools.py` and add a test verifying that when OpenRouter returns HTTP 429 with `{"error": {"message": "Provider returned error", "code": 429, "metadata": {"provider_name": "Google AI Studio", "raw": "{\"error\":{\"message\":\"Resource has been exhausted (e.g. check quota).\"}}"}}}`, `MediaGenerationClient` raises `MediaGenerationError` containing `"HTTP 429"`, `"Provider returned error"`, `"Google AI Studio"`, and `"Resource has been exhausted"`.

- [ ] **Step 2: Run pytest to verify the new test fails (RED)**

Run: `pytest tests/unit/agentic/test_media_generation_tools.py -q`
Expected: FAIL (error message only says `Image generation failed: Provider returned error` without `HTTP 429` or metadata details).

- [ ] **Step 3: Update `MediaGenerationClient._extract_error` in `src/agentos/agentic/media_client.py` (GREEN)**

In `src/agentos/agentic/media_client.py`:
- Include `f"HTTP {response.status_code}"` prefix when `response.status_code >= 400`.
- Extract `metadata = err.get("metadata")` when `isinstance(err, Mapping)`:
  - Read `provider_name = metadata.get("provider_name")`
  - Read `raw = metadata.get("raw")` (if JSON string, parse inner `error.message` or `message`, else truncate cleanly to 300 chars).
  - Format as `HTTP 429: Provider returned error [provider=Google AI Studio: Resource has been exhausted (e.g. check quota).]`.

- [ ] **Step 4: Run full unit & integration test suite**

Run: `pytest tests/unit/mcp/ tests/unit/agentic/ tests/integration/test_mcp_end_to_end.py -q`
Expected: All tests PASS.
