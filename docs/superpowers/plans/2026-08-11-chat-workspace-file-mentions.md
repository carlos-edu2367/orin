# Menções de arquivos do workspace no chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar arquivos de workspaces mencionáveis, pré-visualizáveis, baixáveis e abríveis pelo sistema a partir do chat.

**Architecture:** O backend preserva `ConversationWorkspace.resolve` como fronteira única para inventário e entrega. Ferramentas devolvem artefatos novos/modificados, o runtime os publica e o frontend converte `workspace://` e eventos de artefato em cartões com ações.

**Tech Stack:** Python 3.12, FastAPI, React, TypeScript, Vite e Vitest.

## Global Constraints

- Todo caminho vindo de mensagem ou rota é relativo ao workspace e passa por `ConversationWorkspace.resolve`.
- HTML do agente é pré-visualizado em iframe sem scripts e sem mesma origem.
- A abertura externa exige sessão, CSRF e autorização de mutação.
- Não incluir modificações preexistentes do usuário em commits.

---

### Task 1: Detectar arquivos criados pelas ferramentas

**Files:**
- Modify: `src/agentos/agentic/workspace.py`
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/session.py`
- Test: `tests/unit/agentic/test_agent_tools.py`
- Test: `tests/unit/agentic/test_turn_session.py`

**Interfaces:**
- Produces: `ConversationWorkspace.file_snapshot() -> dict[str, FileSnapshot]` e `changed_files(before) -> list[dict[str, object]]`.
- Produces: `ToolOutcome.payload["artifacts"]` contendo caminhos relativos, tamanho e mtime.
- Produces: um `artifact.created` público para cada arquivo novo/modificado.

- [ ] **Step 1: Write the failing tests**

```python
def test_run_command_reports_a_pdf_created_in_the_workspace(toolset):
    result = toolset.run_command('python -c "open(\'report.pdf\', \'wb\').write(b\'%PDF\')"')
    assert result["payload"]["artifacts"] == [{"path": "report.pdf", "size_bytes": 4}]

def test_turn_session_publishes_detected_artifact_events(session):
    session.emit_lifecycle(session.turn, "tool_finished", tool_name="run_command", status="succeeded", tool_payload={"artifacts": [{"path": "report.pdf", "size_bytes": 4}]})
    assert session.store.recorded[-1].event_type.value == "artifact.created"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_turn_session.py -q`

Expected: failure because snapshots and artifact events do not exist.

- [ ] **Step 3: Implement minimal snapshots, diffs and event publication**

Add a bounded recursive regular-file snapshot. Capture before/after only around workspace-mutating tool calls; attach the difference as `artifacts`. In `emit_lifecycle`, record a public artifact event after its successful tool event.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_turn_session.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/workspace.py src/agentos/agentic/agent_tools.py src/agentos/agentic/session.py tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_turn_session.py
git commit -m "feat(chat): emit workspace file artifacts"
```

### Task 2: Servir arquivos do workspace com segurança

**Files:**
- Create: `src/agentos/agentic/file_preview.py`
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/bootstrap/production.py`
- Test: `tests/unit/api/test_api_asgi.py`

**Interfaces:**
- Produces: `GET /v1/conversations/{conversation_id}/files/{path:path}?disposition=inline|attachment`.
- Produces: `POST /v1/conversations/{conversation_id}/files/{path:path}/open`.
- Consumes: conversation/project ownership to derive the effective workspace.

- [ ] **Step 1: Write failing route tests**

```python
response = client.get("/v1/conversations/chat_abc/files/report.pdf?disposition=inline")
assert response.status_code == 200
assert response.headers["content-type"].startswith("application/pdf")
assert response.headers["x-content-type-options"] == "nosniff"

response = client.get("/v1/conversations/chat_abc/files/../secret.txt")
assert response.status_code in {404, 422}
assert "secret.txt" not in response.text
```

Also cover attachment, project workspace mapping, foreign ownership and CSRF on the POST action.

- [ ] **Step 2: Run the API test to verify red**

Run: `uv run pytest tests/unit/api/test_api_asgi.py -q`

Expected: FAIL because neither file route exists.

- [ ] **Step 3: Implement minimal secure file access**

Resolve the conversation's workspace with the current user; for projects use the project workspace, otherwise conversation id. Resolve the requested path, require a regular file, return `FileResponse` with a trusted MIME type and `X-Content-Type-Options: nosniff`. For open, use a platform opener only after the resolved-file check.

- [ ] **Step 4: Run the API test to verify green**

Run: `uv run pytest tests/unit/api/test_api_asgi.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/file_preview.py src/agentos/api/gateway.py src/agentos/bootstrap/production.py tests/unit/api/test_api_asgi.py
git commit -m "feat(api): serve workspace files safely"
```

### Task 3: Render menções e cartões no chat

**Files:**
- Create: `frontend/src/features/conversations/WorkspaceFileCard.tsx`
- Create: `frontend/src/features/conversations/WorkspaceFilePreview.tsx`
- Modify: `frontend/src/features/conversations/MarkdownMessage.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/features/conversations/activityTypes.ts`
- Modify: `frontend/src/styles/agentos.css`
- Test: `frontend/tests/unit/MarkdownMessage.test.tsx`
- Test: `frontend/tests/unit/ChatPage.test.tsx`

**Interfaces:**
- Consumes: `WorkspaceFileReference { conversationId: string; path: string; name: string }`.
- Produces: a custom Markdown link renderer and cards for unmentioned `artifact.created` events.
- Consumes: the secure file routes from Task 2.

- [ ] **Step 1: Write failing frontend tests**

```tsx
render(<MarkdownMessage conversationId="chat_abc" content="[Abrir](workspace://index.html)" />)
expect(screen.getByRole("button", { name: "Visualizar index.html" })).toBeInTheDocument()
expect(screen.getByRole("link", { name: "Baixar index.html" })).toHaveAttribute("href", expect.stringContaining("/files/index.html?disposition=attachment"))
```

Add a ChatPage fixture with `artifact.created` for `report.pdf`; expect a preview action even with no explicit mention.

- [ ] **Step 2: Run focused frontend tests to verify red**

Run: `npm --prefix frontend test -- MarkdownMessage.test.tsx ChatPage.test.tsx --run`

Expected: FAIL because workspace links are ordinary links and event cards do not exist.

- [ ] **Step 3: Implement cards and preview**

Reject empty, absolute and traversal reference paths. Render HTML in `<iframe sandbox>`; use inline content URLs for HTML/PDF/image/text, an accessible fallback for unknown types, a download anchor, and a CSRF-aware POST for external opening. Dedupe by normalized relative path.

- [ ] **Step 4: Run focused frontend tests to verify green**

Run: `npm --prefix frontend test -- MarkdownMessage.test.tsx ChatPage.test.tsx --run`

Expected: PASS.

- [ ] **Step 5: Build and commit**

```bash
npm --prefix frontend run build
git add frontend/src/features/conversations frontend/src/styles/agentos.css frontend/tests/unit/MarkdownMessage.test.tsx frontend/tests/unit/ChatPage.test.tsx
git commit -m "feat(chat): preview workspace file mentions"
```

### Task 4: Instruir o agente e verificar ponta a ponta

**Files:**
- Modify: `src/agentos/agentic/session.py`
- Test: `tests/unit/agentic/test_turn_session.py`
- Test: `tests/integration/api/test_frontend_contracts.py`

**Interfaces:**
- Produces: instrução para responder com `[filename](workspace://relative/path)`, priorizando entregáveis finais e scripts úteis.

- [ ] **Step 1: Write a failing behavior test**

```python
prompt = build_system_prompt(tool_names=(), memories=[], agents=[], workspace_hint="", subagents_enabled=False)
assert "workspace://path/to/file" in prompt
```

- [ ] **Step 2: Run the test to verify red**

Run: `uv run pytest tests/unit/agentic/test_turn_session.py tests/integration/api/test_frontend_contracts.py -q`

Expected: FAIL because the prompt has no link guidance.

- [ ] **Step 3: Add the minimal prompt guidance**

Tell the agent to link useful workspace files with the canonical Markdown form, prioritizing final outputs while including generators when useful.

- [ ] **Step 4: Run full feature verification**

Run: `uv run pytest tests/unit/agentic/test_agent_tools.py tests/unit/agentic/test_turn_session.py tests/unit/api/test_api_asgi.py tests/integration/api/test_frontend_contracts.py -q`

Run: `npm --prefix frontend test -- MarkdownMessage.test.tsx ChatPage.test.tsx --run`

Run: `npm --prefix frontend run build`

Expected: all commands exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/agentos/agentic/session.py tests/unit/agentic/test_turn_session.py tests/integration/api/test_frontend_contracts.py
git commit -m "feat(agent): guide workspace file mentions"
```

