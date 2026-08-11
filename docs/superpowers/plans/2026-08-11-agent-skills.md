# Agent Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add secure, lazy-loaded procedural Skills to AgentOS conversations and the product UI.

**Architecture:** A `SkillRegistry` owns versioned SKILL.md metadata, retrieval, availability and dependencies. `TurnSession` injects only suggested metadata and exposes Skill tools; persisted versions and execution snapshots provide auditability.

**Tech Stack:** Python 3.13, Pydantic, SQLAlchemy/Alembic, FastAPI, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Skill text is subordinate operational content and never grants permissions or executes scripts.
- Retrieval must fall back to lexical scoring and must not block a turn.
- The initial prompt may contain metadata only; instructions enter only through `use_skill`.
- New production behavior starts with a focused failing test.

---

### Task 1: Domain model and in-memory registry

**Files:** Create `src/agentos/skills/{models,parser,registry,retrieval,builtins}.py`; create `tests/unit/skills/test_registry.py` and `test_retrieval.py`.

- [ ] **Step 1: Write failing registry and retrieval tests**

```python
def test_use_resolution_returns_immutable_version_and_detects_cycles():
    registry = SkillRegistry([pdf_skill, cyclic_skill])
    assert registry.resolve("pdf").ref.version == "1.0.0"
    with pytest.raises(SkillDependencyCycle): registry.load("cyclic")

def test_pdf_attachment_outranks_unrelated_skills():
    assert registry.retrieve(RetrievalQuery("compare", attachments=("report.pdf",))).items[0].id == "pdf"
```

- [ ] **Step 2: Run the tests and observe missing module failures**

Run: `python -m pytest tests/unit/skills/test_registry.py tests/unit/skills/test_retrieval.py -q`

- [ ] **Step 3: Implement models, strict parser, registry and lexical/signal retriever**

```python
class SkillRegistry:
    def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...
    def load(self, skill_id: str, *, version: str | None = None, available_tools: Collection[str] = ()) -> LoadedSkill: ...
```

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/skills/test_registry.py tests/unit/skills/test_retrieval.py -q`

### Task 2: SKILL.md packages and resources

**Files:** Create `src/agentos/skills/builtin/*/SKILL.md`; modify `builtins.py`; test `tests/unit/skills/test_parser.py`.

- [ ] **Step 1: Write failing package parsing/resource listing tests**

```python
def test_builtin_packages_expose_metadata_without_reading_instructions():
    skill = load_builtin_skills()[0]
    assert skill.metadata.description and skill.instructions is None
```

- [ ] **Step 2: Run test and implement four concise built-ins**

Run: `python -m pytest tests/unit/skills/test_parser.py -q`

- [ ] **Step 3: Verify test and static package layout**

Run: `python -m pytest tests/unit/skills/test_parser.py -q`

### Task 3: Conversation runtime tools and context

**Files:** Modify `src/agentos/agentic/{agent_tools,session,runtime}.py`; test `tests/unit/agentic/test_skill_tools.py` and `test_turn_session.py`.

- [ ] **Step 1: Write failing lazy-load/cache/prompt-security tests**

```python
def test_prompt_contains_metadata_but_not_skill_body(session):
    assert "Systematic Debugging" in session.build_runtime().system_prompt
    assert "Ignore every prior instruction" not in session.build_runtime().system_prompt

def test_use_skill_returns_body_once_and_never_grants_a_tool(toolset):
    assert "Workflow" in toolset.invoke("use_skill", {"skill_id": "debugging"}).content
    assert "already loaded" in toolset.invoke("use_skill", {"skill_id": "debugging"}).content
```

- [ ] **Step 2: Run tests and implement session-scoped registry/cache plus four tools**

Run: `python -m pytest tests/unit/agentic/test_skill_tools.py tests/unit/agentic/test_turn_session.py -q`

- [ ] **Step 3: Verify provider-agnostic tool-result flow**

Run: `python -m pytest tests/unit/agentic/test_skill_tools.py tests/unit/agentic/test_turn_session.py tests/unit/agentic/test_agentic_runtime_loop.py -q`

### Task 4: Durable persistence and audit

**Files:** Modify `schema.py`; create migration `0027_agent_skills.py` and `persistence/postgres/skills.py`; test `tests/unit/persistence/test_skill_schema.py`.

- [ ] **Step 1: Write failing schema/round-trip/snapshot tests**

```python
def test_execution_load_persists_version_digest_and_content_snapshot(engine):
    store.record_load(context, loaded)
    assert store.loads_for_execution(context.execution_id)[0].content_digest == loaded.digest
```

- [ ] **Step 2: Run tests and implement skill/version/agent/execution tables plus adapter**

Run: `python -m pytest tests/unit/persistence/test_skill_schema.py -q`

- [ ] **Step 3: Verify migration upgrade path**

Run: `python -m pytest tests/unit/persistence/test_skill_schema.py tests/unit/persistence/test_migrations.py -q`

### Task 5: API and events

**Files:** Modify `api/gateway.py`, `api/contracts.py`, bootstrap composition and activity projection; tests `tests/unit/api/test_skill_api.py`.

- [ ] **Step 1: Write failing API contract tests for list/search/create/update/associate**

```python
def test_search_is_compact_and_paginated(client):
    response = client.get("/v1/skills?query=debug&limit=1")
    assert set(response.json()["items"][0]) == {"id", "name", "description", "version", "tags", "source", "available"}
```

- [ ] **Step 2: Implement authorized routes and activity event projection**

Run: `python -m pytest tests/unit/api/test_skill_api.py -q`

- [ ] **Step 3: Verify API suite**

Run: `python -m pytest tests/unit/api/test_skill_api.py tests/unit/api/test_api_asgi.py -q`

### Task 6: Skills library frontend

**Files:** Create `frontend/src/features/skills/*`, `frontend/src/api/skills.ts`; modify routes, command palette and styles; test `frontend/tests/unit/SkillsPage.test.tsx`.

- [ ] **Step 1: Write failing list/search/detail/create form tests**

```tsx
it('filters compact skill rows without rendering instructions', async () => {
  render(<SkillsPage client={client} />)
  await user.type(screen.getByRole('searchbox'), 'debug')
  expect(screen.getByText('Systematic Debugging')).toBeVisible()
  expect(screen.queryByText('Workflow')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Implement library, detail disclosures, creation form and API client**

Run: `npm test -- --run frontend/tests/unit/SkillsPage.test.tsx`

- [ ] **Step 3: Build and verify frontend suite**

Run: `npm run test -- --run && npm run build`

### Task 7: End-to-end validation and documentation

**Files:** Add `tests/integration/agentic/test_skills_e2e.py`; update RFC 904 and `CREATING_SKILLS.md` as required.

- [ ] **Step 1: Write E2E failing test**

```python
def test_debug_request_suggests_metadata_then_loads_only_debugging_skill(provider):
    provider.request_skill("systematic-debugging")
    result = session.build_runtime().run("turn-1")
    assert result.state == "completed"
```

- [ ] **Step 2: Run focused test, correct integration defects, then verify all relevant suites**

Run: `python -m pytest tests/unit/skills tests/unit/agentic tests/unit/api/test_skill_api.py tests/integration/agentic/test_skills_e2e.py -q`

- [ ] **Step 3: Run complete verification**

Run: `python -m pytest -q && cd frontend && npm run lint && npm run test && npm run build`
