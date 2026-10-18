# Remove Internal Browser (Playwright MCP Only) & Interactive Cards UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Orin's internal browser subsystem (`IsolatedConversationBrowser`, internal Chromium installer, and `browse_page`/`browser_*`/`verify_frontend` native tools) in favor of the official Playwright MCP server (`@playwright/mcp`), replace the Settings > Browser view with a 1-click Playwright MCP installer/manager, and fix & redesign the interactive user-decision cards (`UserQuestionCard`, `CodeSessionProposalCard`, `McpApprovalCard`, `PluginApprovalCard`, `ToolPermissionCard`).

**Architecture:** Backend MCP catalog gains the official `playwright` entry (`npx -y @playwright/mcp@latest`) and `McpServerService.propose()` auto-hydrates catalog fields when `catalog_id` is passed via HTTP. All internal browser tools, runtime hooks, and Chromium installer endpoints/CLI subcommands are removed. On the frontend, `BrowserSection.tsx` manages the `playwright` MCP server, `models.py` stops redacting `code_session_proposal.prompt`, `propose_code_session` preserves the active session provider/model, and `.approval-card` / `.user-question-card` are redesigned without double borders or unstyled native form controls.

**Tech Stack:** Python 3.12 (FastAPI, SQLAlchemy, pytest), React 19 + TypeScript + Vite + Vitest + Playwright E2E.

**Spec:** `docs/superpowers/specs/2026-10-17-remove-internal-browser-and-ask-user-ui-design.md`

## Global Constraints

- Windows PowerShell environment: always invoke `npm.cmd` and `npx.cmd` instead of `npm`/`npx`.
- Every task follows strict TDD: write failing test (RED) -> implement minimal code (GREEN) -> refactor & adversarial check (REFACTOR).
- Preserve all unrelated comments and docstrings.
- Visually verify the updated UI (`BrowserSection`, `CodeSessionProposalCard`, `UserQuestionCard`) in a real Chromium browser via Playwright on desktop and mobile viewports before declaring completion.

---

### Task 1: Add Official Playwright MCP to Catalog & Hydrate `catalog_id` in `McpServerService.propose`

**Files:**
- Modify: `src/agentos/mcp/catalog.py:63-195`
- Modify: `src/agentos/mcp/service.py:152-201`
- Test: `tests/unit/mcp/test_catalog.py`
- Test: `tests/unit/mcp/test_service.py`

**Interfaces:**
- Produces: `find_catalog_entry("playwright") -> McpCatalogEntry` with `transport=McpTransport.STDIO`, `command="npx"`, `args=("-y", "@playwright/mcp@latest")`, `secrets=()`.
- Produces: `McpServerService.propose({"user_id": ..., "catalog_id": "playwright", "display_name": "Playwright"})` auto-hydrating `transport`, `command`, `args`, `url`, `secret_names` from `CATALOG` when omitted.

- [ ] **Step 1: Write the failing tests in `tests/unit/mcp/test_catalog.py` and `tests/unit/mcp/test_service.py`**

```python
# In tests/unit/mcp/test_catalog.py:
def test_catalog_includes_playwright_mcp_entry() -> None:
    entry = find_catalog_entry("playwright")
    assert entry is not None
    assert entry.display_name == "Playwright"
    assert entry.transport == McpTransport.STDIO
    assert entry.command == "npx"
    assert "@playwright/mcp@latest" in entry.args
    assert entry.secrets == ()
    assert any(item.catalog_id == "playwright" for item in search_catalog("navegador"))

# In tests/unit/mcp/test_service.py:
def test_propose_hydrates_transport_and_command_from_catalog_id(engine) -> None:
    service = McpServerService(engine)
    created = service.propose({
        "user_id": "u_1",
        "catalog_id": "playwright",
        "display_name": "Playwright",
    })
    assert created["catalog_id"] == "playwright"
    assert created["transport"] == "stdio"
    assert created["command"] == "npx"
    assert "@playwright/mcp@latest" in created["args"]
    assert created["secret_names"] == []
```

- [ ] **Step 2: Run pytest to verify the new tests fail (RED)**

Run: `pytest tests/unit/mcp/test_catalog.py tests/unit/mcp/test_service.py -v`
Expected: FAIL (`find_catalog_entry("playwright")` is `None` and `propose` raises `McpServiceError: transport must be 'stdio' or 'http'`).

- [ ] **Step 3: Implement `playwright` catalog entry in `src/agentos/mcp/catalog.py` and catalog hydration in `src/agentos/mcp/service.py`**

```python
# In src/agentos/mcp/catalog.py inside CATALOG:
    McpCatalogEntry(
        catalog_id="playwright",
        display_name="Playwright",
        summary="Navegador automatizado via Playwright MCP para abrir páginas dinâmicas, clicar, preencher formulários e capturar telas.",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@playwright/mcp@latest"),
        setup_instructions="Requer Node.js (npx) disponível no ambiente. Nenhuma chave ou credencial é necessária.",
        keywords=("playwright", "browser", "navegador", "web", "chromium", "automacao", "pagina"),
    ),
```

```python
# In src/agentos/mcp/service.py inside McpServerService.propose:
        command = dict(command)
        catalog_id = command.get("catalog_id")
        if catalog_id and not command.get("transport"):
            from .catalog import find_catalog_entry
            entry = find_catalog_entry(str(catalog_id))
            if entry is None:
                raise McpServiceError(f"unknown MCP catalog_id '{catalog_id}'")
            command.setdefault("transport", entry.transport.value)
            if entry.command is not None:
                command.setdefault("command", entry.command)
            if entry.args and not command.get("args"):
                command["args"] = list(entry.args)
            if entry.url is not None:
                command.setdefault("url", entry.url)
            if entry.secrets and not command.get("secret_names"):
                command["secret_names"] = [item.name for item in entry.secrets]
```

- [ ] **Step 4: Run pytest to verify the tests pass (GREEN)**

Run: `pytest tests/unit/mcp/test_catalog.py tests/unit/mcp/test_service.py -v`
Expected: PASS.

---

### Task 2: Fix `[REDACTED]` on `code_session_proposal.prompt` & Default Provider/Model Preservation in `propose_code_session`

**Files:**
- Modify: `src/agentos/agentic/models.py:27-165`
- Modify: `src/agentos/agentic/agent_tools.py:5221-5274`
- Test: `tests/unit/agentic/test_propose_code_session_tool.py`
- Test: `tests/unit/agentic/test_activity_contracts.py`

**Interfaces:**
- Produces: `AgentActivityEvent` / `_bounded` preserves `code_session_proposal["prompt"]` up to `16_000` chars without turning it into `"[REDACTED]"`, while still redacting `prompt` in arbitrary payloads and sanitizing inline `api_key=...` patterns.
- Produces: `AgentToolset.propose_code_session` ignores placeholder model/provider strings (`"default"`, `"auto"`, `"current"`) and preserves `self._default_provider` / `self._default_model_id` when the model guesses `"openai"` + `"default"`.

- [ ] **Step 1: Write failing tests in `tests/unit/agentic/test_propose_code_session_tool.py` and `tests/unit/agentic/test_activity_contracts.py`**

```python
# In tests/unit/agentic/test_propose_code_session_tool.py:
def test_propose_code_session_prompt_is_not_redacted_in_activity_event(tmp_path) -> None:
    from datetime import UTC, datetime
    from agentos.agentic.models import AgentActionKind, AgentActivityEvent, AgentLifecycleState

    toolset = AgentToolset(
        tmp_path,
        default_provider="openrouter",
        default_model_id="qwen/qwen-3.7-flash",
    )
    long_prompt = "Traduzir aulas 7 e 8 do módulo 5 mantendo formatação markdown.\n" * 40
    outcome = toolset.propose_code_session(
        title="Tradução Aulas 7 e 8",
        prompt=long_prompt,
        provider="openai",
        model_id="default",
    )
    proposal = outcome.payload["code_session_proposal"]
    assert proposal["provider"] == "openrouter"
    assert proposal["model_id"] == "qwen/qwen-3.7-flash"

    event = AgentActivityEvent(
        event_id="evt_1",
        turn_id="turn_1",
        conversation_id="conv_1",
        user_id="user_1",
        sequence=1,
        occurred_at=datetime.now(UTC),
        state=AgentLifecycleState.WAITING_USER,
        kind=AgentActionKind.TOOL,
        summary=outcome.summary,
        payload=outcome.payload,
    )
    assert event.payload["code_session_proposal"]["prompt"] == long_prompt.strip()
    assert event.payload["code_session_proposal"]["prompt"] != "[REDACTED]"
```

- [ ] **Step 2: Run pytest to verify the test fails (RED)**

Run: `pytest tests/unit/agentic/test_propose_code_session_tool.py -v`
Expected: FAIL (`proposal["provider"] == "openai"` and `event.payload["code_session_proposal"]["prompt"] == "[REDACTED]"`).

- [ ] **Step 3: Update `src/agentos/agentic/models.py` and `src/agentos/agentic/agent_tools.py`**

```python
# In src/agentos/agentic/models.py:
MAX_CODE_PROPOSAL_TEXT = 16_000
_LARGE_BUDGET_KEYS = frozenset({
    "questions", "plugin", "server", "structured_patch", "artifacts",
    "permission_request", "todos", "code_session_proposal",
})
_CODE_PROPOSAL_PUBLIC_KEYS = frozenset({
    "proposal_id", "title", "prompt", "workspace_path",
    "provider", "model_id", "permission_mode", "project_id",
})
```
In `_bounded`: pass `parent_key: str | None = None`, and when `parent_key == "code_session_proposal"` and `normalized_key in _CODE_PROPOSAL_PUBLIC_KEYS`, do not trigger `_sensitive_key(key)` (if `normalized_key == "prompt"` and `isinstance(item, str)`, apply `sanitize_summary(item)` and `text_limit=MAX_CODE_PROPOSAL_TEXT`).

In `src/agentos/agentic/agent_tools.py` (`propose_code_session`):
```python
        _PLACEHOLDER_MODELS = {"default", "auto", "current", "none", "null"}
        clean_model = model_id.strip() if isinstance(model_id, str) else ""
        if clean_model.lower() in _PLACEHOLDER_MODELS:
            clean_model = ""
        clean_provider = provider.strip().lower() if isinstance(provider, str) else ""
        # When the model guesses a generic provider + placeholder model (e.g. openai/default)
        # while the conversation already has a configured default_provider, keep the conversation's provider.
        if self._default_provider and (not clean_provider or not clean_model):
            effective_provider = self._default_provider
        else:
            effective_provider = clean_provider or self._default_provider
        effective_model = clean_model if (clean_model and effective_provider == (clean_provider or self._default_provider)) else (self._default_model_id or clean_model or None)
```

- [ ] **Step 4: Run pytest to verify the tests pass (GREEN)**

Run: `pytest tests/unit/agentic/test_propose_code_session_tool.py tests/unit/agentic/test_activity_contracts.py -v`
Expected: PASS.

---

### Task 3: Remove Internal Browser Tools, Engine Installer, CLI Subcommand & Gateway Endpoints

**Files:**
- Modify: `src/agentos/agentic/agent_tools.py`
- Modify: `src/agentos/agentic/contract.py`
- Modify: `src/agentos/agentic/phases.py`
- Modify: `src/agentos/agentic/runtime.py`
- Modify: `src/agentos/agentic/session.py`
- Modify: `src/agentos/workers/chat.py`
- Modify: `src/agentos/api/gateway.py`
- Modify: `src/agentos/launcher/cli.py`
- Delete: `src/agentos/launcher/browser.py`
- Delete: `src/agentos/agentic/browser_tools.py`
- Delete: `src/agentos/browser/engine.py`
- Delete: `src/agentos/browser/conversation_worker.py`
- Delete: `src/agentos/browser/playwright_adapter.py`
- Modify/Delete tests: `tests/unit/agentic/test_task_contract.py`, `tests/unit/agentic/test_phase_controller.py`, `tests/unit/agentic/test_agent_tools.py`, `tests/unit/agentic/test_browser_tools.py`, `tests/unit/agentic/test_browser_registry.py`, `tests/unit/browser/test_engine.py`, `tests/unit/browser/test_conversation_worker.py`, `tests/unit/browser/test_playwright_boundary.py`

**Interfaces:**
- Produces: `TOOLKITS` in `contract.py` is `frozenset({"files", "terminal", "web", "delegation", "mcp", "plugins"})` (no `"browser"` toolkit requirement for frontend tasks).
- Produces: `AgentToolset.definitions()` never publishes `browse_page`, `browser_*`, or `verify_frontend`.
- Produces: `build_system_prompt` in `session.py` guides the agent to use the `playwright` MCP server (`search_mcp_tools` / `configure_mcp(catalog_id="playwright", display_name="Playwright")`) when browser interaction is needed.

- [ ] **Step 1: Write failing tests verifying internal browser removal**

Update `tests/unit/agentic/test_task_contract.py`, `tests/unit/agentic/test_phase_controller.py`, and `tests/unit/agentic/test_agent_tools.py`:
- Assert `"browser"` is not in `TOOLKITS` and frontend contracts succeed with `toolkits=["files", "terminal"]` (and optional `"mcp"`).
- Assert `browse_page`, `browser_observe`, `verify_frontend` are not in `AgentToolset(tmp_path).definitions()`.

- [ ] **Step 2: Run pytest on contract, phase, and agent_tools tests to see them fail (RED)**

Run: `pytest tests/unit/agentic/test_task_contract.py tests/unit/agentic/test_phase_controller.py tests/unit/agentic/test_agent_tools.py -v`
Expected: FAIL.

- [ ] **Step 3: Remove internal browser tools, runtime references, CLI `browser` command, and `/v1/runtime/browser*` routes**

1. `src/agentos/agentic/contract.py`: Remove `"browser"` from `TOOLKITS`, remove the `"browser"` check in `_require_mechanical_acceptance`, and remove `"browser"` from `synthesize()`.
2. `src/agentos/agentic/phases.py`: Remove `"browser"` from `TOOLKIT_TOOLS`, remove `"browser_observe"`, `"browser_screenshot"`, `"verify_frontend"` from `PHASE_TOOLS[Phase.VERIFY]`, update `PHASE_INSTRUCTIONS`.
3. `src/agentos/agentic/agent_tools.py`: Remove `from agentos.browser.engine ...`, remove `browse_page`, `browser_*`, and `verify_frontend` definitions and handler methods, remove `_code_mode_visual_check` gate on `verify_frontend`.
4. `src/agentos/agentic/runtime.py` & `src/agentos/agentic/session.py`: Remove `browse_page` / `browser_*` from runtime tool lists, remove `AgentBrowserView` import, replace `## Browser` prompt block with Playwright MCP guidance.
5. `src/agentos/workers/chat.py`: Remove `ConversationBrowserRegistry` and `browser` acquisition/release.
6. `src/agentos/api/gateway.py`: Remove `engine_installer` import and `/v1/runtime/browser` + `/v1/runtime/browser/install` endpoints.
7. `src/agentos/launcher/cli.py`: Remove `browser` subparser and `command == "browser"` branch; delete `src/agentos/launcher/browser.py`.
8. Delete obsolete internal browser modules (`src/agentos/agentic/browser_tools.py`, `src/agentos/browser/engine.py`, `src/agentos/browser/conversation_worker.py`, `src/agentos/browser/playwright_adapter.py`) and their dedicated unit test files; update any remaining imports in `src/agentos/bootstrap/production.py`.

- [ ] **Step 4: Run full backend test suite on affected modules to verify everything passes (GREEN)**

Run: `pytest tests/unit/agentic/ tests/unit/mcp/ tests/unit/api/ -v`
Expected: PASS.

---

### Task 4: Replace `BrowserSection.tsx` with Playwright MCP Installer & Manager

**Files:**
- Modify: `frontend/src/features/settings/BrowserSection.tsx`
- Modify: `frontend/src/features/settings/sections.ts`
- Modify: `frontend/src/features/settings/PreferencesModal.tsx`
- Delete: `frontend/src/api/browserEngine.ts`
- Delete: `frontend/src/features/conversations/BrowserActivityCard.tsx`
- Modify: `frontend/src/features/conversations/ActivityStream.tsx`
- Modify: `frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx`

**Interfaces:**
- Consumes: `listMcpServers`, `createMcpServer`, `approveMcpServer`, `setMcpServerEnabled`, `testMcpServer`, `deleteMcpServer` from `frontend/src/api/mcp.ts`.
- Produces: `BrowserSection({ client?: ApiClient })` rendering 1-click installation/activation and management of the Playwright MCP server (`catalog_id: 'playwright'`).

- [ ] **Step 1: Write failing unit tests for the new `BrowserSection` (Playwright MCP) in `frontend/src/features/settings/__tests__/PreferencesAndHubs.test.tsx`**

Test cases:
1. When `listMcpServers` returns `[]`, `BrowserSection` shows `"Playwright MCP"`, command preview `npx -y @playwright/mcp@latest`, and button `"Instalar Playwright MCP"`.
2. Clicking `"Instalar Playwright MCP"` calls `POST /v1/mcp/servers` (`{ catalog_id: 'playwright', display_name: 'Playwright' }`) and `POST /v1/mcp/servers/{id}/approve` (`{ secrets: {} }`), transitioning to the active state (`"Ativo"`).
3. When already installed (`state: 'active'`), shows status badge, tool count, `"Testar conexão"`, `"Desativar"`, and `"Remover"`.

- [ ] **Step 2: Run Vitest to verify the test fails (RED)**

Run: `npm.cmd --prefix frontend test -- --run src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the new `BrowserSection.tsx` and remove `browserEngine.ts` / `BrowserActivityCard.tsx`**

Update `BrowserSection.tsx` to use the MCP API (`listMcpServers`, `createMcpServer`, `approveMcpServer`, `testMcpServer`, `setMcpServerEnabled`, `deleteMcpServer`), update copy in `sections.ts` and `PreferencesModal.tsx`, and remove `BrowserActivityCard` from `ActivityStream.tsx`.

- [ ] **Step 4: Run Vitest to verify the tests pass (GREEN)**

Run: `npm.cmd --prefix frontend test -- --run src/features/settings/__tests__/PreferencesAndHubs.test.tsx`
Expected: PASS.

---

### Task 5: Redesign & Fix Interactive Cards (`CodeSessionProposalCard`, `UserQuestionCard`, `ApprovalCard` Family)

**Files:**
- Modify: `frontend/src/features/conversations/CodeSessionProposalCard.tsx`
- Modify: `frontend/src/features/conversations/UserQuestionCard.tsx`
- Modify: `frontend/src/features/conversations/ActivityStream.tsx`
- Modify: `frontend/src/features/conversations/TurnTimeline.tsx`
- Modify: `frontend/src/features/conversations/ChatPage.tsx`
- Modify: `frontend/src/styles/agentos.css`
- Test: `frontend/src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx`
- Test: `frontend/src/features/conversations/__tests__/TurnTimelineExtensions.test.tsx`

**Interfaces:**
- Produces: `renderActivityGroup` and `TurnTimeline` forward `client`, `defaultProvider`, `defaultModelId`, and `models` from `ChatPage` down to `CodeSessionProposalCard`.
- Produces: `CodeSessionProposalCard` renders friendly provider labels (`OpenAI`, `Anthropic`, `OpenRouter`, `OmniRoute`, `Ollama`), filters out `"default"` model placeholders when real models exist, shows the `"Escolher pasta…"` button in `ChatPage`, and styles `<select>`, `<textarea>`, and `<input>` consistently.
- Produces: `UserQuestionCard` (`ask_user`) renders clean selectable option cards and collapses the optional note `<textarea>` behind a `"+ Adicionar observação (opcional)"` toggle on choice questions.
- Produces: `.approval-card` and `.user-question-card` in `agentos.css` remove the double-bordered inner box (`cards-inside-cards`) and style all form controls (`input`, `select`, `textarea`) with theme tokens.

- [ ] **Step 1: Write failing unit tests in `frontend/src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx`**

Test cases:
1. When `proposal` has `provider: 'openai'` and `model_id: 'default'`, and `defaultProvider: 'openrouter'` + `defaultModelId: 'qwen/qwen-3.7-flash'` are provided, `CodeSessionProposalCard` selects `openrouter` (`OpenRouter`) and `qwen/qwen-3.7-flash` instead of `default`.
2. When rendered via `TurnTimeline` with `client`, `defaultProvider`, `defaultModelId`, `models`, the `"Escolher pasta…"` button is visible and model options include the provider's models.
3. In `UserQuestionCard`, a `single_choice` question hides the optional observation `<textarea>` until `"+ Adicionar observação (opcional)"` is clicked, while a `text` question shows the `<textarea>` immediately.

- [ ] **Step 2: Run Vitest to verify the tests fail (RED)**

Run: `npm.cmd --prefix frontend test -- --run src/features/conversations/__tests__/CodeSessionProposalCard.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement component and CSS updates**

1. Update `CodeSessionProposalCard.tsx`:
   - Add `PROVIDER_LABELS: Record<ProviderName, string>` (`openai: 'OpenAI'`, `anthropic: 'Anthropic'`, `openrouter: 'OpenRouter'`, `omniroute: 'OmniRoute'`, `ollama: 'Ollama'`).
   - Treat `proposal.model_id === 'default'` / `'auto'` as empty so `defaultModelId` from the active chat wins; if `proposal.model_id` was `'default'` and `defaultProvider` is provided, prefer `defaultProvider`.
   - Use clean `.approval-card__grid` / `gap-3` layout for Provider, Modelo, and Modo de permissão.
2. Update `ActivityStream.tsx`, `TurnTimeline.tsx`, and `ChatPage.tsx`:
   - Pass `client`, `defaultProvider`, `defaultModelId`, and `models` through `TurnTimeline` -> `renderActivityGroup` -> `CodeSessionProposalCard`.
3. Update `UserQuestionCard.tsx`:
   - Add `noteExpanded` state per question for choice questions (`single_choice` / `checkbox`), toggled by a `+ Adicionar observação (opcional)` button (or auto-expanded if `currentAnswer.note` is non-empty).
   - Enhance option labels with radio/checkbox visual indicators and keyboard accessibility.
4. Update `frontend/src/styles/agentos.css`:
   - Remove the nested green border/box on `.approval-card__form` and `.user-question-card__step`; use a clean top divider (`border-top: 1px solid var(--line)`) inside a single elevated card surface.
   - Style `.approval-card__field select` and `.approval-card__field textarea` alongside `.approval-card__field input` with proper padding (`9px 12px`), `border: 1px solid var(--line-strong)`, `border-radius: 8px`, `background: var(--raised)`, `color: var(--text)`, focus ring, and custom select arrow.

- [ ] **Step 4: Run Vitest and `npm run build` to verify all tests and TypeScript compilation pass (GREEN)**

Run: `npm.cmd --prefix frontend test -- --run` and `npm.cmd --prefix frontend run build`
Expected: PASS with 0 errors.

---

### Task 6: Real Browser Visual Verification (Playwright Desktop + Mobile + Axe WCAG)

**Files:**
- Modify: `frontend/tests/e2e/code-mode-ux.spec.ts`

- [ ] **Step 1: Add E2E visual and accessibility checks for `BrowserSection` (Playwright MCP) and `CodeSessionProposalCard` / `UserQuestionCard`**

Verify in real Chromium via Playwright (desktop `1440x900` and mobile `390x844`):
1. `PreferencesModal` -> `Sistema & Ambiente` -> `Playwright MCP` card renders cleanly, 1-click install transitions to `Ativo`, 0 Axe WCAG violations.
2. `CodeSessionProposalCard` and `UserQuestionCard` render with single clean card border, styled `<select>` and `<textarea>` controls, visible `"Escolher pasta…"` button, real prompt text (no `[REDACTED]`), and 0 Axe WCAG violations.
3. Capture screenshots to the artifacts directory and inspect them visually.

- [ ] **Step 2: Run Playwright E2E suite**

Run: `npx.cmd playwright test tests/e2e/code-mode-ux.spec.ts` (from `frontend/`)
Expected: PASS (all tests green, 0 Axe violations).
